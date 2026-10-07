"""The shared ad library: every unique ad any teammate has collected.

Two places it can live, with the same rules for what gets stored:
- Library: a SQL database (SQLite on a laptop by default), used by the local
  analysis service and the tests.
- SupabaseLibrary: the team's Supabase project, written by the collector
  over Supabase's web API while signed in as the "collector" user (who can
  add and update ads but not delete them; see supabase_setup.sql). The
  gallery reads it directly.

Results last a day: an ad stays for KEEP_HOURS after a search last found it,
then it is deleted with its picture copy (remove_expired), so the gallery
only shows what Meta was running recently. Searching a category again within
the day refreshes it. Each unique ad gets a small picture copy because Meta's
image links stop working after a few days.
"""
import io
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image
from sqlalchemy import (
    Column, DateTime, Float, Integer, MetaData, String, Table, Text,
    case, create_engine, func, select, update,
)

import meta_design_scraper_session as scraper

KEEP_HOURS = 24  # decided with the user 2026-10-07: "brand new search every day"
DESIGN_LED_MIN_SCORE = 0.5  # same cut-off as the dashboard's "design-led only"
THUMBNAIL_MAX_SIDE = 600
THUMBNAIL_JPEG_QUALITY = 80

# Columns the library filters or sorts on; every other score lives in `scores`.
SCORE_COLUMNS_KEPT_SEPARATELY = {"rank_score", "filter_status", "image_hash", "frame_variant_hash",
                                 "ocr_fingerprint", "creative_variant_count"}
NOT_STORED = {"id", "ok", "error", "details", "image_url", "duplicate", "creative_group_id",
              "is_creative_representative", "creative_variant_of", "representative_reason"}
# What the matching rules need from each stored ad.
MATCH_FIELDS = ("id", "image_hash", "frame_variant_hash", "ocr_fingerprint", "thumbnail_key", "times_seen")

# Keep in step with supabase_setup.sql.
metadata = MetaData()
ads = Table(
    "ads", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("category_key", String(200), nullable=False, index=True),  # casefolded, for matching
    Column("category", String(200), nullable=False),  # as first typed, for display
    Column("country", String(8), nullable=False),
    Column("image_hash", String(64), nullable=False, index=True),  # not unique: same design, other price
    Column("frame_variant_hash", Text, nullable=False, default="[]"),
    Column("ocr_fingerprint", Text, nullable=False, default=""),
    Column("brand", Text, nullable=False, default=""),
    Column("image_url", Text, nullable=False, default=""),  # Meta's link; expires after days
    Column("thumbnail_key", String(200)),  # our own copy, see the image stores
    Column("rank_score", Float),  # None for excluded video frames
    Column("filter_status", String(20), nullable=False, default=""),
    Column("creative_variant_count", Integer, nullable=False, default=0),
    Column("scores", Text, nullable=False, default="{}"),  # JSON: every other analysis value
    Column("details", Text, nullable=False, default="{}"),  # JSON: ad text, page, ids from Meta
    Column("human_label", String(10), nullable=False, default=""),  # 👍/👎 later (step 4)
    Column("first_seen_at", DateTime, nullable=False),
    Column("last_seen_at", DateTime, nullable=False),
    Column("times_seen", Integer, nullable=False, default=1),
)


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def category_key(category):
    return " ".join((category or "").split()).casefold()


def make_thumbnail(image):
    """Small JPEG copy of an analyzed image (bytes), at most THUMBNAIL_MAX_SIDE px."""
    copy = image.convert("RGB")
    copy.thumbnail((THUMBNAIL_MAX_SIDE, THUMBNAIL_MAX_SIDE))
    buf = io.BytesIO()
    copy.save(buf, format="JPEG", quality=THUMBNAIL_JPEG_QUALITY, optimize=True)
    return buf.getvalue()


class _LibraryRules:
    """What gets stored and how a search is matched against earlier ones.

    Subclasses provide `images` (save/url_for) and `_writer()`, a context
    manager whose object can load_existing(key, country), insert(row) -> id
    and mark_seen(match, now, image_url, thumbnail_key).
    """

    def save_search(self, category, country, results, thumbnails, now=None):
        """Store a finished search's unique ads; return {"new": n, "seen_again": m}.

        `results` are /finish results; only analyzed representatives are kept
        (duplicates within the search are copies of them). `thumbnails` maps
        ad id -> JPEG bytes. An ad already in the library for this category
        (by the search's own duplicate rules) is marked seen again, not re-added.
        """
        now = now or utc_now()
        self.remove_expired(now)  # so an ad from yesterday comes back as new, with today's details
        key, country = category_key(category), (country or "").upper()
        keep = [r for r in results if r.get("ok") and r.get("is_creative_representative") == 1]
        counts = {"new": 0, "seen_again": 0}
        with self._writer() as writer:
            existing = writer.load_existing(key, country)
            for result in keep:
                match = self._find_same_ad(result, existing)
                thumbnail_key = self._store_thumbnail(result, thumbnails, match)
                if match:
                    writer.mark_seen(match, now, result.get("image_url", ""), thumbnail_key)
                    counts["seen_again"] += 1
                    continue
                row = self._new_row(result, category, key, country, thumbnail_key, now)
                row_id = writer.insert(row)
                existing.append({"id": row_id, **{f: row[f] for f in MATCH_FIELDS if f != "id"}})
                counts["new"] += 1
        return counts

    @staticmethod
    def _find_same_ad(result, existing):
        """The stored ad this result is a copy of, by the search's own duplicate
        rules (without SIFT, whose descriptors aren't stored). Exact picture first."""
        same_picture_first = sorted(existing, key=lambda row: row["image_hash"] != result.get("image_hash"))
        for row in same_picture_first:
            if scraper.same_underlying_creative(result, row)[0]:
                return row
        return None

    def _store_thumbnail(self, result, thumbnails, match):
        if match and match.get("thumbnail_key"):
            return match["thumbnail_key"]
        data = thumbnails.get(str(result["id"]))
        if not data:
            return None
        key = f"{scraper.safe_name(result['id'])}.jpg"  # per ad: one design can carry different prices
        try:
            self.images.save(key, data)
        except Exception as exc:  # keep the ad; its picture is retried next time it's seen
            print(f"[LIBRARY] Could not store picture {key}: {exc}")
            return None
        return key

    @staticmethod
    def _new_row(result, category, key, country, thumbnail_key, now):
        scores = {k: v for k, v in result.items() if k not in NOT_STORED | SCORE_COLUMNS_KEPT_SEPARATELY}
        rank = result.get("rank_score")
        return {
            "category_key": key, "category": " ".join(category.split()), "country": country,
            "image_hash": result.get("image_hash", ""),
            "frame_variant_hash": result.get("frame_variant_hash", "[]"),
            "ocr_fingerprint": result.get("ocr_fingerprint", ""),
            "brand": str(result.get("details", {}).get("brand", "")),
            "image_url": result.get("image_url", ""),
            "thumbnail_key": thumbnail_key,
            "rank_score": rank if isinstance(rank, (int, float)) else None,
            "filter_status": result.get("filter_status", ""),
            "creative_variant_count": int(result.get("creative_variant_count") or 0),
            "scores": scores,
            "details": result.get("details", {}),
            "first_seen_at": now, "last_seen_at": now, "times_seen": 1,
        }


# --- a SQL database (laptop, local service, tests) ------------------------------

class LocalImageStore:
    """Picture copies in a folder; the API serves them at /thumbnails/<key>."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def save(self, key, data):
        (self.folder / key).write_bytes(data)

    def delete(self, key):
        (self.folder / key).unlink(missing_ok=True)

    def url_for(self, key):
        return f"/thumbnails/{key}"


class _SqlWriter:
    def __init__(self, conn):
        self.conn = conn

    def load_existing(self, key, country):
        query = select(*[ads.c[f] for f in MATCH_FIELDS]).where(ads.c.category_key == key, ads.c.country == country)
        return [dict(row) for row in self.conn.execute(query).mappings()]

    def insert(self, row):
        values = {**row, "scores": json.dumps(row["scores"], ensure_ascii=False),
                  "details": json.dumps(row["details"], ensure_ascii=False)}
        return self.conn.execute(ads.insert().values(**values)).inserted_primary_key[0]

    def mark_seen(self, match, now, image_url, thumbnail_key):
        self.conn.execute(update(ads).where(ads.c.id == match["id"]).values(
            last_seen_at=now, times_seen=ads.c.times_seen + 1,
            image_url=image_url,  # a fresh link from Meta
            thumbnail_key=thumbnail_key,
        ))


class Library(_LibraryRules):
    def __init__(self, database_url, image_store):
        self.engine = create_engine(database_url, pool_pre_ping=True)
        self.images = image_store
        metadata.create_all(self.engine)

    @contextmanager
    def _writer(self):
        with self.engine.begin() as conn:
            yield _SqlWriter(conn)

    def remove_expired(self, now=None):
        """Delete ads no search has found for KEEP_HOURS, and their picture
        copies (unless another kept row still uses the same copy). Returns how many."""
        cutoff = (now or utc_now()) - timedelta(hours=KEEP_HOURS)
        with self.engine.begin() as conn:
            old = conn.execute(select(ads.c.id, ads.c.thumbnail_key).where(ads.c.last_seen_at < cutoff)).all()
            if not old:
                return 0
            conn.execute(ads.delete().where(ads.c.last_seen_at < cutoff))
            still_used = set(conn.execute(select(ads.c.thumbnail_key).where(ads.c.thumbnail_key.is_not(None))).scalars())
        for key in {k for _id, k in old if k} - still_used:
            try:
                self.images.delete(key)
            except Exception as exc:
                print(f"[LIBRARY] Could not delete picture {key}: {exc}")
        return len(old)

    def browse(self, category=None, country=None, design_led_only=True, now=None, limit=1000):
        """Ads found in the last KEEP_HOURS, best first."""
        cutoff = (now or utc_now()) - timedelta(hours=KEEP_HOURS)
        query = select(ads).where(ads.c.last_seen_at >= cutoff)
        if category:
            query = query.where(ads.c.category_key == category_key(category))
        if country:
            query = query.where(ads.c.country == country.upper())
        if design_led_only:
            query = query.where(ads.c.rank_score >= DESIGN_LED_MIN_SCORE)
        query = query.order_by(ads.c.rank_score.desc().nulls_last(), ads.c.id).limit(limit)
        with self.engine.connect() as conn:
            return [self._public(dict(row)) for row in conn.execute(query).mappings()]

    def _public(self, row):
        row["scores"] = json.loads(row["scores"])
        row["details"] = json.loads(row["details"])
        row["thumbnail_url"] = self.images.url_for(row["thumbnail_key"]) if row["thumbnail_key"] else None
        for field in ("first_seen_at", "last_seen_at"):
            row[field] = row[field].strftime("%Y-%m-%d %H:%M")
        return row

    def categories(self, design_led_only=False, now=None):
        """[{category, country, count}] for each category searched in the last KEEP_HOURS.

        With design_led_only, only ads scoring DESIGN_LED_MIN_SCORE+ are counted
        (a category with none is still listed, with 0)."""
        cutoff = (now or utc_now()) - timedelta(hours=KEEP_HOURS)
        counted = ads.c.rank_score >= DESIGN_LED_MIN_SCORE if design_led_only else True
        query = (select(ads.c.category_key, func.min(ads.c.category), ads.c.country,
                        func.sum(case((counted, 1), else_=0)))
                 .where(ads.c.last_seen_at >= cutoff)
                 .group_by(ads.c.category_key, ads.c.country).order_by(ads.c.category_key, ads.c.country))
        with self.engine.connect() as conn:
            return [{"category": name, "country": country, "count": int(n or 0)}
                    for _key, name, country, n in conn.execute(query)]


# --- Supabase (the team's shared library) ----------------------------------------

class SupabaseError(RuntimeError):
    pass


def _check(res, what):
    if res.status_code >= 400:
        raise SupabaseError(f"{what} failed ({res.status_code}): {res.text[:300]}")
    return res


class SupabaseLogin:
    """Signs in as the collector user and keeps a fresh access token."""

    def __init__(self, project_url, publishable_key, email, password, session):
        self.base, self.key, self.email, self.password = project_url.rstrip("/"), publishable_key, email, password
        self.session = session
        self._token, self._expires_at = None, 0

    def token(self):
        if self._token is None or time.time() > self._expires_at - 60:
            res = self.session.post(f"{self.base}/auth/v1/token", params={"grant_type": "password"},
                                    json={"email": self.email, "password": self.password},
                                    headers={"apikey": self.key}, timeout=30)
            if res.status_code >= 400:
                raise SupabaseError(f"Signing in to the team library failed ({res.status_code}). "
                                    "Check the collector email and password in the .env file.")
            body = res.json()
            self._token, self._expires_at = body["access_token"], time.time() + int(body.get("expires_in", 3600))
        return self._token

    def headers(self):
        return {"apikey": self.key, "Authorization": f"Bearer {self.token()}"}


class SupabaseImageStore:
    """Picture copies in the public "thumbnails" bucket."""

    def __init__(self, project_url, login, session, bucket="thumbnails"):
        self.base, self.login, self.session, self.bucket = project_url.rstrip("/"), login, session, bucket

    def save(self, key, data):
        res = self.session.post(f"{self.base}/storage/v1/object/{self.bucket}/{key}", data=data, timeout=60,
                                headers={**self.login.headers(), "Content-Type": "image/jpeg",
                                         "Cache-Control": "max-age=604800"})
        if res.status_code in (400, 409) and ("Duplicate" in res.text or "already exists" in res.text):
            return  # uploaded by an earlier, interrupted save; the collector may not overwrite
        _check(res, f"Uploading picture {key}")

    def url_for(self, key):
        return f"{self.base}/storage/v1/object/public/{self.bucket}/{key}"


class _SupabaseWriter:
    PAGE = 1000  # Supabase returns at most 1000 rows per request

    def __init__(self, base, login, session):
        self.url, self.login, self.session = f"{base}/rest/v1/ads", login, session

    def load_existing(self, key, country):
        rows = []
        while True:
            res = _check(self.session.get(self.url, headers=self.login.headers(), timeout=60, params={
                "select": ",".join(MATCH_FIELDS), "category_key": f"eq.{key}", "country": f"eq.{country}",
                "order": "id", "limit": self.PAGE, "offset": len(rows)}), "Reading the team library")
            page = res.json()
            rows.extend(page)
            if len(page) < self.PAGE:
                return rows

    def insert(self, row):
        body = {**row, "first_seen_at": _iso(row["first_seen_at"]), "last_seen_at": _iso(row["last_seen_at"])}
        res = _check(self.session.post(self.url, json=body, timeout=60,
                                       headers={**self.login.headers(), "Prefer": "return=representation"}),
                     "Adding an ad to the team library")
        return res.json()[0]["id"]

    def mark_seen(self, match, now, image_url, thumbnail_key):
        match["times_seen"] = int(match.get("times_seen") or 1) + 1
        _check(self.session.patch(self.url, params={"id": f"eq.{match['id']}"}, timeout=60,
                                  headers={**self.login.headers(), "Prefer": "return=minimal"},
                                  json={"last_seen_at": _iso(now), "times_seen": match["times_seen"],
                                        "image_url": image_url, "thumbnail_key": thumbnail_key}),
               "Updating an ad in the team library")


def _iso(moment):
    return moment.replace(tzinfo=timezone.utc).isoformat()


class SupabaseLibrary(_LibraryRules):
    def __init__(self, project_url, publishable_key, email, password, session=None):
        self.base = project_url.rstrip("/")
        self.session = session or requests.Session()
        self.login = SupabaseLogin(self.base, publishable_key, email, password, self.session)
        self.images = SupabaseImageStore(self.base, self.login, self.session)

    @contextmanager
    def _writer(self):
        yield _SupabaseWriter(self.base, self.login, self.session)

    def remove_expired(self, now=None):
        """The collector user may not delete (supabase_setup.sql), so expired ads
        are only hidden here: the gallery shows the last KEEP_HOURS."""
        return 0


SUPABASE_SETTINGS = ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY",
                     "CREATIVE_LENS_COLLECTOR_EMAIL", "CREATIVE_LENS_COLLECTOR_PASSWORD")


def library_from_environment():
    """The team's Supabase library when all SUPABASE_SETTINGS are set (the
    collector reads them from .env) and CREATIVE_LENS_LIBRARY isn't "local";
    otherwise a local SQL library
    (CREATIVE_LENS_DATABASE_URL, default library.db + thumbnails/ in the data dir)."""
    if os.environ.get("CREATIVE_LENS_LIBRARY") != "local" and all(os.environ.get(n) for n in SUPABASE_SETTINGS):
        return SupabaseLibrary(*(os.environ[name] for name in SUPABASE_SETTINGS))
    database_url = os.environ.get("CREATIVE_LENS_DATABASE_URL") or f"sqlite:///{scraper.BASE_DIR / 'library.db'}"
    thumbnails = os.environ.get("CREATIVE_LENS_THUMBNAIL_DIR") or scraper.BASE_DIR / "thumbnails"
    return Library(database_url, LocalImageStore(thumbnails))
