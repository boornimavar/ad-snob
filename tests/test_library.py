"""Tests for the shared ad library (SQLite in a temp folder)."""
import io
import json
from datetime import timedelta

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
    assert row["thumbnail_url"] == "/thumbnails/ffff000000000000.jpg"
    assert (tmp_path / "thumbs" / "ffff000000000000.jpg").read_bytes() == b"jpeg-bytes"


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

    assert lib.browse("skincare", now=T0)[0]["thumbnail_url"] == "/thumbnails/ffff000000000000.jpg"
    assert (tmp_path / "thumbs" / "ffff000000000000.jpg").read_bytes() == b"now-we-have-it"


def test_make_thumbnail_is_a_small_jpeg():
    data = library.make_thumbnail(Image.new("RGB", (1080, 1350), "red"))

    image = Image.open(io.BytesIO(data))
    assert image.format == "JPEG" and max(image.size) == library.THUMBNAIL_MAX_SIDE
    assert len(data) < 60_000
