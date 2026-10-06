"""The shared ad library: every unique ad any teammate has collected.

SQLite on a laptop, Postgres (Supabase) online - set CREATIVE_LENS_DATABASE_URL.
An ad is "active" while some search of its category keeps finding it; after
ARCHIVE_AFTER_DAYS without being seen it moves to the archive (never deleted).

Meta's image links expire after a few days, so each unique ad also gets a
small picture copy in an ImageStore.
"""
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image
from sqlalchemy import (
    Column, DateTime, Float, Integer, MetaData, String, Table, Text, UniqueConstraint,
    case, create_engine, func, select, update,
)

import meta_design_scraper_session as scraper

ARCHIVE_AFTER_DAYS = 14
DESIGN_LED_MIN_SCORE = 0.5  # same cut-off as the dashboard's "design-led only"
SAME_AD_CROP_DISTANCE = 6  # the dedup rule's "visual match" crop pHash distance
THUMBNAIL_MAX_SIDE = 600
THUMBNAIL_JPEG_QUALITY = 80

# Columns the library filters or sorts on; every other score lives in `scores`.
SCORE_COLUMNS_KEPT_SEPARATELY = {"rank_score", "filter_status", "image_hash", "frame_variant_hash",
                                 "ocr_fingerprint", "creative_variant_count"}
NOT_STORED = {"id", "ok", "error", "details", "image_url", "duplicate", "creative_group_id",
              "is_creative_representative", "creative_variant_of", "representative_reason"}

metadata = MetaData()
ads = Table(
    "ads", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("category_key", String(200), nullable=False, index=True),  # casefolded, for matching
    Column("category", String(200), nullable=False),  # as first typed, for display
    Column("country", String(8), nullable=False),
    Column("image_hash", String(64), nullable=False),
    Column("frame_variant_hash", Text, nullable=False, default="[]"),
    Column("ocr_fingerprint", Text, nullable=False, default=""),
    Column("brand", Text, nullable=False, default=""),
    Column("image_url", Text, nullable=False, default=""),  # Meta's link; expires after days
    Column("thumbnail_key", String(200)),  # our own copy, see ImageStore
    Column("rank_score", Float),  # None for excluded video frames
    Column("filter_status", String(20), nullable=False, default=""),
    Column("creative_variant_count", Integer, nullable=False, default=0),
    Column("scores", Text, nullable=False, default="{}"),  # JSON: every other analysis value
    Column("details", Text, nullable=False, default="{}"),  # JSON: ad text, page, ids from Meta
    Column("human_label", String(10), nullable=False, default=""),  # 👍/👎 later (step 4)
    Column("first_seen_at", DateTime, nullable=False),
    Column("last_seen_at", DateTime, nullable=False),
    Column("times_seen", Integer, nullable=False, default=1),
    UniqueConstraint("category_key", "country", "image_hash", name="one_row_per_ad_per_category"),
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


class LocalImageStore:
    """Picture copies in a folder; the API serves them at /thumbnails/<key>."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def save(self, key, data):
        (self.folder / key).write_bytes(data)

    def url_for(self, key):
        return f"/thumbnails/{key}"


class Library:
    def __init__(self, database_url, image_store):
        self.engine = create_engine(database_url, pool_pre_ping=True)
        self.images = image_store
        metadata.create_all(self.engine)

    def save_search(self, category, country, results, thumbnails, now=None):
        """Store a finished search's unique ads; return {"new": n, "seen_again": m}.

        `results` are /finish results; only analyzed representatives are kept
        (duplicates within the search are copies of them). `thumbnails` maps
        ad id -> JPEG bytes. An ad already in the library for this category
        (same picture, or a re-crop of it) is marked seen again, not re-added.
        """
        now = now or utc_now()
        key, country = category_key(category), (country or "").upper()
        keep = [r for r in results if r.get("ok") and r.get("is_creative_representative") == 1]
        counts = {"new": 0, "seen_again": 0}
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(ads.c.id, ads.c.image_hash, ads.c.frame_variant_hash, ads.c.thumbnail_key)
                .where(ads.c.category_key == key, ads.c.country == country)
            ).mappings().all()
            existing = [dict(row) for row in existing]
            for result in keep:
                match = self._find_same_ad(result, existing)
                thumbnail_key = self._store_thumbnail(result, thumbnails, match)
                if match:
                    conn.execute(update(ads).where(ads.c.id == match["id"]).values(
                        last_seen_at=now, times_seen=ads.c.times_seen + 1,
                        image_url=result.get("image_url", ""),  # a fresh link from Meta
                        thumbnail_key=thumbnail_key,
                    ))
                    counts["seen_again"] += 1
                    continue
                row = self._new_row(result, category, key, country, thumbnail_key, now)
                new_id = conn.execute(ads.insert().values(**row)).inserted_primary_key[0]
                existing.append({"id": new_id, "image_hash": row["image_hash"],
                                 "frame_variant_hash": row["frame_variant_hash"], "thumbnail_key": thumbnail_key})
                counts["new"] += 1
        return counts

    @staticmethod
    def _find_same_ad(result, existing):
        for row in existing:
            if row["image_hash"] == result.get("image_hash"):
                return row
        for row in existing:
            distance = scraper.crop_hash_distance(row["frame_variant_hash"], result.get("frame_variant_hash", "[]"))
            if distance <= SAME_AD_CROP_DISTANCE:
                return row
        return None

    def _store_thumbnail(self, result, thumbnails, match):
        if match and match.get("thumbnail_key"):
            return match["thumbnail_key"]
        data = thumbnails.get(str(result["id"]))
        if not data:
            return None
        key = f"{result['image_hash']}.jpg"
        self.images.save(key, data)
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
            "scores": json.dumps(scores, ensure_ascii=False),
            "details": json.dumps(result.get("details", {}), ensure_ascii=False),
            "first_seen_at": now, "last_seen_at": now, "times_seen": 1,
        }

    def browse(self, category=None, country=None, archived=False, design_led_only=True, now=None, limit=1000):
        """Ads for the gallery, best first. Active = seen within ARCHIVE_AFTER_DAYS."""
        cutoff = (now or utc_now()) - timedelta(days=ARCHIVE_AFTER_DAYS)
        query = select(ads).where(ads.c.last_seen_at < cutoff if archived else ads.c.last_seen_at >= cutoff)
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

    def categories(self, now=None):
        """[{category, country, active, archived}] for every category in the library."""
        cutoff = (now or utc_now()) - timedelta(days=ARCHIVE_AFTER_DAYS)
        active = func.sum(case((ads.c.last_seen_at >= cutoff, 1), else_=0))
        query = (select(ads.c.category_key, func.min(ads.c.category), ads.c.country, active, func.count())
                 .group_by(ads.c.category_key, ads.c.country).order_by(ads.c.category_key, ads.c.country))
        with self.engine.connect() as conn:
            return [{"category": name, "country": country, "active": int(n_active or 0),
                     "archived": int(total - (n_active or 0))}
                    for _key, name, country, n_active, total in conn.execute(query)]


def library_from_environment():
    """The library configured by environment variables, with laptop defaults."""
    database_url = os.environ.get("CREATIVE_LENS_DATABASE_URL") or f"sqlite:///{scraper.BASE_DIR / 'library.db'}"
    thumbnail_dir = os.environ.get("CREATIVE_LENS_THUMBNAIL_DIR") or scraper.BASE_DIR / "thumbnails"
    return Library(database_url, LocalImageStore(thumbnail_dir))
