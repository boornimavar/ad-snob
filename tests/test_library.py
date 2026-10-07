"""Tests for the shared ad library (SQLite in a temp folder)."""
import io
import json
from datetime import timedelta, timezone

import pytest
from PIL import Image

import library

T0 = library.utc_now().replace(microsecond=0)


@pytest.fixture
def lib(tmp_path):
    return library.Library(f"sqlite:///{tmp_path / 'library.db'}", library.LocalImageStore(tmp_path / "thumbs"))


def result(ad_id, image_hash, rank=0.8, crops=None, representative=1, **extra):
    """One /finish result as the analysis API returns it."""
    return {
        "id": ad_id, "ok": True, "image_url": f"https://img.example/{ad_id}.jpg",
        "image_hash": image_hash, "frame_variant_hash": json.dumps(crops or [image_hash]),
        "ocr_fingerprint": "glow serum", "rank_score": rank, "filter_status": "CANDIDATE",
        "is_creative_representative": representative, "creative_variant_count": 0,
        "duplicate": 0 if representative else 1, "clip_design_score": 0.6, "ocr_word_count": 12,
        "details": {"brand": "Dot & Key", "body": "Glow serum"}, **extra,
    }


def test_saves_unique_ads_with_scores_details_and_picture(lib, tmp_path):
    counts = lib.save_search("Skincare", "in", [result("a", "ffff000000000000")], {"a": b"jpeg-bytes"}, now=T0)

    assert counts == {"new": 1, "seen_again": 0}
    [row] = lib.browse("skincare", "IN", now=T0)
    assert row["category"] == "Skincare" and row["country"] == "IN" and row["brand"] == "Dot & Key"
    assert row["rank_score"] == 0.8 and row["scores"]["clip_design_score"] == 0.6
    assert row["details"]["body"] == "Glow serum"
    assert row["first_seen_at"] == row["last_seen_at"] and row["times_seen"] == 1
    assert row["thumbnail_url"] == "/thumbnails/a.jpg"
    assert (tmp_path / "thumbs" / "a.jpg").read_bytes() == b"jpeg-bytes"


def test_duplicates_within_the_search_and_failed_ads_are_not_stored(lib):
    lib.save_search("skincare", "IN", [
        result("a", "ffff000000000000"),
        result("copy", "ffff000000000001", representative=0),
        {"id": "broken", "ok": False, "error": "Could not download"},
    ], {}, now=T0)

    assert [r["image_hash"] for r in lib.browse("skincare", now=T0)] == ["ffff000000000000"]


def test_same_ad_found_again_is_marked_seen_not_added(lib):
    lib.save_search("skincare", "IN", [result("a", "ffff000000000000")], {"a": b"old"}, now=T0)
    later = T0 + timedelta(days=3)

    counts = lib.save_search("SKINCARE", "IN",
                             [result("a2", "ffff000000000000")], {"a2": b"new"}, now=later)

    assert counts == {"new": 0, "seen_again": 1}
    [row] = lib.browse("skincare", now=later)
    assert row["times_seen"] == 2
    assert row["last_seen_at"] == later.strftime("%Y-%m-%d %H:%M")
    assert row["first_seen_at"] == T0.strftime("%Y-%m-%d %H:%M")
    assert row["image_url"] == "https://img.example/a2.jpg"  # fresh link from Meta


def test_a_recropped_copy_counts_as_the_same_ad(lib):
    lib.save_search("skincare", "IN", [result("a", "ffff000000000000", crops=["ffff0000ffff0000"])], {}, now=T0)

    counts = lib.save_search("skincare", "IN",
                             [result("b", "0000ffff00000000", crops=["ffff0000ffff0001"])], {}, now=T0)

    assert counts == {"new": 0, "seen_again": 1}


def test_category_spacing_and_case_do_not_matter(lib):
    lib.save_search("Skin Care", "IN", [result("a", "ffff000000000000")], {}, now=T0)
    counts = lib.save_search("  skin   CARE ", "in", [result("a", "ffff000000000000")], {}, now=T0)

    assert counts["seen_again"] == 1
    assert lib.browse("skin care", "IN", now=T0)[0]["category"] == "Skin Care"


def test_each_category_and_country_keeps_its_own_copy(lib):
    ad = [result("a", "ffff000000000000")]
    lib.save_search("skincare", "IN", ad, {}, now=T0)
    lib.save_search("beauty", "IN", ad, {}, now=T0)
    lib.save_search("skincare", "AE", ad, {}, now=T0)

    assert len(lib.browse(now=T0)) == 3


def test_ads_not_seen_for_14_days_move_to_the_archive(lib):
    lib.save_search("skincare", "IN", [result("old", "ffff000000000000"), result("kept", "0000ffff00000000")], {},
                    now=T0)
    lib.save_search("skincare", "IN", [result("kept", "0000ffff00000000")], {}, now=T0 + timedelta(days=10))
    day_15 = T0 + timedelta(days=15)

    assert [r["image_hash"] for r in lib.browse("skincare", now=day_15)] == ["0000ffff00000000"]
    assert [r["image_hash"] for r in lib.browse("skincare", archived=True, now=day_15)] == ["ffff000000000000"]
    assert lib.categories(now=day_15) == [{"category": "skincare", "country": "IN", "active": 1, "archived": 1}]


def test_browse_is_best_first_and_design_led_by_default(lib):
    lib.save_search("skincare", "IN", [
        result("mid", "ffff000000000000", rank=0.6),
        result("low", "0000ffff00000000", rank=0.2),
        result("top", "00000000ffff0000", rank=0.95),
        result("video", "000000000000ffff", rank="", filter_status="EXCLUDED"),
    ], {}, now=T0)

    assert [r["rank_score"] for r in lib.browse("skincare", now=T0)] == [0.95, 0.6]
    everything = [r["rank_score"] for r in lib.browse("skincare", design_led_only=False, now=T0)]
    assert everything == [0.95, 0.6, 0.2, None]


def test_picture_is_stored_once_and_added_later_if_missing(lib, tmp_path):
    lib.save_search("skincare", "IN", [result("a", "ffff000000000000")], {}, now=T0)
    assert lib.browse("skincare", now=T0)[0]["thumbnail_url"] is None

    lib.save_search("skincare", "IN", [result("a", "ffff000000000000")], {"a": b"now-we-have-it"}, now=T0)
    lib.save_search("skincare", "IN", [result("a", "ffff000000000000")], {"a": b"ignored"}, now=T0)

    assert lib.browse("skincare", now=T0)[0]["thumbnail_url"] == "/thumbnails/a.jpg"
    assert (tmp_path / "thumbs" / "a.jpg").read_bytes() == b"now-we-have-it"


def test_make_thumbnail_is_a_small_jpeg():
    data = library.make_thumbnail(Image.new("RGB", (1080, 1350), "red"))

    image = Image.open(io.BytesIO(data))
    assert image.format == "JPEG" and max(image.size) == library.THUMBNAIL_MAX_SIDE
    assert len(data) < 60_000


def test_library_keeps_ads_with_different_prices_apart(lib):
    # Same template picture, different offers: the search's numeric rule keeps them apart.
    lib.save_search("skincare", "IN", [result("a", "ffff000000000000", crops=["ffff0000ffff0000"],
                                              ocr_fingerprint="flat 40% off ₹499")], {}, now=T0)

    counts = lib.save_search("skincare", "IN", [result("b", "0000ffff00000000", crops=["ffff0000ffff0001"],
                                                       ocr_fingerprint="flat 60% off ₹799")], {}, now=T0)

    assert counts == {"new": 1, "seen_again": 0}


def test_same_picture_with_a_different_price_is_a_different_ad(lib):
    lib.save_search("skincare", "IN", [result("a", "ffff000000000000", ocr_fingerprint="flat off on 499")], {}, now=T0)

    counts = lib.save_search("skincare", "IN", [result("b", "ffff000000000000", ocr_fingerprint="flat off on 799")],
                             {}, now=T0)

    assert counts == {"new": 1, "seen_again": 0}
    assert len(lib.browse("skincare", now=T0)) == 2


def test_category_counts_can_follow_the_design_led_switch(lib):
    lib.save_search("skincare", "IN", [result("hi", "ffff000000000000", rank=0.9),
                                       result("lo", "0000ffff00000000", rank=0.2)], {}, now=T0)

    assert lib.categories(now=T0)[0]["active"] == 2
    assert lib.categories(design_led_only=True, now=T0)[0]["active"] == 1


# --- the team's Supabase library (through a fake of Supabase's web API) ---------

class FakeResponse:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text or json.dumps(body)

    def json(self):
        return self._body


class FakeSupabase:
    """Just enough of Supabase's login, table and storage endpoints."""

    BASE = "https://abc.supabase.co"

    def __init__(self, storage_status=200):
        self.password, self.storage_status = "right", storage_status
        self.rows, self.files, self.logins, self.calls = [], {}, 0, []

    def _authorized(self, headers):
        assert headers["apikey"] == "sb_publishable_x"
        return headers.get("Authorization") == "Bearer user-token"

    def post(self, url, params=None, json=None, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url))
        if url == f"{self.BASE}/auth/v1/token":
            assert params == {"grant_type": "password"}
            if json["password"] != self.password:
                return FakeResponse(400, {"error": "invalid_grant"})
            self.logins += 1
            return FakeResponse(200, {"access_token": "user-token", "expires_in": 3600})
        assert self._authorized(headers)
        if url.startswith(f"{self.BASE}/storage/v1/object/thumbnails/"):
            key = url.rsplit("/", 1)[1]
            if key in self.files:
                return FakeResponse(400, text='{"error":"Duplicate","message":"The resource already exists"}')
            if self.storage_status >= 400:
                return FakeResponse(self.storage_status, text="denied")
            self.files[key] = data
            return FakeResponse(200, {"Key": key})
        assert url == f"{self.BASE}/rest/v1/ads"
        row = {**json, "id": len(self.rows) + 1}
        self.rows.append(row)
        return FakeResponse(201, [row])

    def get(self, url, params=None, headers=None, timeout=None):
        assert url == f"{self.BASE}/rest/v1/ads" and self._authorized(headers)
        fields = params["select"].split(",")
        found = [r for r in self.rows if r["category_key"] == params["category_key"][3:]
                 and r["country"] == params["country"][3:]]
        page = found[params["offset"]:params["offset"] + params["limit"]]
        return FakeResponse(200, [{f: r[f] for f in fields} for r in page])

    def patch(self, url, params=None, json=None, headers=None, timeout=None):
        assert self._authorized(headers)
        row = next(r for r in self.rows if r["id"] == int(params["id"][3:]))
        row.update(json)
        return FakeResponse(204, None, text="")


@pytest.fixture
def supa():
    return FakeSupabase()


def supabase_library(fake, password="right"):
    return library.SupabaseLibrary(FakeSupabase.BASE, "sb_publishable_x", "collector@team.test", password,
                                   session=fake)


def test_supabase_library_adds_ads_and_pictures(supa):
    lib = supabase_library(supa)

    counts = lib.save_search("Skincare", "IN", [result("a", "ffff000000000000"), result("b", "0000ffff00000000")],
                             {"a": b"jpeg-a"}, now=T0)

    assert counts == {"new": 2, "seen_again": 0}
    first = supa.rows[0]
    assert first["category"] == "Skincare" and first["category_key"] == "skincare"
    assert first["scores"]["clip_design_score"] == 0.6 and first["details"]["brand"] == "Dot & Key"
    assert first["first_seen_at"] == T0.replace(tzinfo=timezone.utc).isoformat()
    assert first["thumbnail_key"] == "a.jpg" and supa.files == {"a.jpg": b"jpeg-a"}
    assert supa.rows[1]["thumbnail_key"] is None
    assert lib.images.url_for("a.jpg") == f"{FakeSupabase.BASE}/storage/v1/object/public/thumbnails/a.jpg"
    assert supa.logins == 1  # one sign-in serves the whole search


def test_supabase_library_marks_ads_seen_again(supa):
    lib = supabase_library(supa)
    lib.save_search("skincare", "IN", [result("a", "ffff000000000000")], {}, now=T0)
    later = T0 + timedelta(days=2)

    counts = lib.save_search("skincare", "IN", [result("a2", "ffff000000000000")], {"a2": b"pic"}, now=later)

    assert counts == {"new": 0, "seen_again": 1}
    [row] = supa.rows
    assert row["times_seen"] == 2 and row["last_seen_at"] == later.replace(tzinfo=timezone.utc).isoformat()
    assert row["image_url"] == "https://img.example/a2.jpg" and row["thumbnail_key"] == "a2.jpg"


def test_supabase_library_reads_more_than_one_page(supa, monkeypatch):
    monkeypatch.setattr(library._SupabaseWriter, "PAGE", 2)
    lib = supabase_library(supa)
    hashes = ["ffff000000000000", "0000ffff00000000", "00000000ffff0000", "000000000000ffff", "f0f0f0f0f0f0f0f0"]
    lib.save_search("skincare", "IN", [result(str(n), h) for n, h in enumerate(hashes)], {}, now=T0)

    counts = lib.save_search("skincare", "IN", [result("again", hashes[-1])], {}, now=T0)

    assert counts == {"new": 0, "seen_again": 1}  # found on the third page


def test_supabase_picture_already_uploaded_is_fine(supa):
    supa.files["a.jpg"] = b"from an interrupted save"
    lib = supabase_library(supa)

    lib.save_search("skincare", "IN", [result("a", "ffff000000000000")], {"a": b"new"}, now=T0)

    assert supa.rows[0]["thumbnail_key"] == "a.jpg"


def test_supabase_failed_picture_upload_keeps_the_ad():
    supa = FakeSupabase(storage_status=403)

    supabase_library(supa).save_search("skincare", "IN", [result("a", "ffff000000000000")], {"a": b"x"}, now=T0)

    assert len(supa.rows) == 1 and supa.rows[0]["thumbnail_key"] is None


def test_supabase_wrong_password_is_explained():
    lib = supabase_library(FakeSupabase(), password="wrong")

    with pytest.raises(library.SupabaseError, match="Check the collector email and password"):
        lib.save_search("skincare", "IN", [result("a", "ffff000000000000")], {}, now=T0)


def test_environment_picks_supabase_only_when_fully_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("CREATIVE_LENS_DATABASE_URL", f"sqlite:///{tmp_path / 'env.db'}")
    monkeypatch.setenv("CREATIVE_LENS_THUMBNAIL_DIR", str(tmp_path / "t"))
    for name in library.SUPABASE_SETTINGS:
        monkeypatch.setenv(name, "x")
    assert isinstance(library.library_from_environment(), library.SupabaseLibrary)

    monkeypatch.delenv("CREATIVE_LENS_COLLECTOR_PASSWORD")
    assert isinstance(library.library_from_environment(), library.Library)


def test_local_switch_overrides_supabase_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("CREATIVE_LENS_DATABASE_URL", f"sqlite:///{tmp_path / 'env.db'}")
    monkeypatch.setenv("CREATIVE_LENS_THUMBNAIL_DIR", str(tmp_path / "t"))
    for name in library.SUPABASE_SETTINGS:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("CREATIVE_LENS_LIBRARY", "local")

    assert isinstance(library.library_from_environment(), library.Library)
