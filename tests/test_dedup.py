import json

import meta_design_scraper_session as scraper


def analyzed(image_file, image_hash, ocr="", crops=None, **overrides):
    """A minimal analyzed row as deduplicate_analyzed_rows() expects it."""
    row = {
        "image_file": image_file,
        "image_hash": image_hash,
        "ocr_fingerprint": ocr,
        "frame_variant_hash": json.dumps(crops if crops is not None else [image_hash]),
        "_sift_descriptors": None,
        "rank_score": 0.5,
        "aspect_ratio": 1.0,
        "image_width": 1080,
        "image_height": 1080,
        "creative_score": 0.5,
        "duplicate": 0,
    }
    row.update(overrides)
    return row


# --- helpers ---------------------------------------------------------------

def test_normalize_ocr_tokens():
    # lowercased, punctuation stripped, currency/percent kept with the number,
    # single letters dropped, sorted and de-duplicated
    assert scraper.normalize_ocr_tokens("Flat 50% OFF!! on ₹499 a off") == ["50%", "flat", "off", "on", "₹499"]
    assert scraper.normalize_ocr_tokens(None) == []


def test_ocr_token_jaccard():
    assert scraper.ocr_token_jaccard(["a", "b"], ["a", "b"]) == 1.0
    assert scraper.ocr_token_jaccard("a b c d", "a b") == 0.5
    assert scraper.ocr_token_jaccard([], ["a"]) == 0.0


def test_crop_hash_distance():
    same = "ffff0000ffff0000"
    assert scraper.crop_hash_distance([same], [same]) == 0
    assert scraper.crop_hash_distance(json.dumps([same]), json.dumps(["0000ffff0000ffff", same])) == 0
    assert scraper.crop_hash_distance([same], ["0000ffff0000ffff"]) == 64
    assert scraper.crop_hash_distance("not json", [same]) == 999


def test_safe_name():
    assert scraper.safe_name("Dot & Key  Skin/Care") == "Dot_Key_SkinCare"
    assert scraper.safe_name("") == "Unknown"


def test_clean_text():
    assert scraper.clean_text(None) == ""
    assert scraper.clean_text(["a", 1]) == "a 1"
    assert scraper.clean_text({"k": "₹"}) == '{"k": "₹"}'


# --- deduplication -----------------------------------------------------------

def test_distinct_creatives_stay_separate():
    rows = [
        analyzed("a.jpg", "ffff0000ffff0000"),
        analyzed("b.jpg", "0000ffff0000ffff"),
    ]

    reps, duplicates, groups = scraper.deduplicate_analyzed_rows(rows)

    assert [r["image_file"] for r in reps] == ["a.jpg", "b.jpg"]
    assert duplicates == 0
    assert all(r["is_creative_representative"] == 1 for r in reps)


def test_exact_hash_match_keeps_higher_ranked_frame():
    low = analyzed("low.jpg", "ffff0000ffff0000", rank_score=0.4)
    high = analyzed("high.jpg", "ffff0000ffff0000", rank_score=0.9)

    reps, duplicates, groups = scraper.deduplicate_analyzed_rows([low, high])

    assert [r["image_file"] for r in reps] == ["high.jpg"]
    assert duplicates == 1
    assert high["duplicate"] == 0 and high["creative_variant_count"] == 1
    assert low["duplicate"] == 1
    assert low["is_creative_representative"] == 0
    assert low["creative_variant_of"] == "high.jpg"


def test_near_identical_crops_are_grouped():
    base = "ffff0000ffff0000"
    near = "ffff0000ffff0003"  # 2 bits away
    rows = [analyzed("a.jpg", base), analyzed("b.jpg", "0f0f0f0f0f0f0f0f", crops=[near])]

    reps, duplicates, _ = scraper.deduplicate_analyzed_rows(rows)

    assert len(reps) == 1
    assert duplicates == 1


def test_different_prices_are_never_merged():
    # Same image hash, but the ad copy advertises different prices.
    rows = [
        analyzed("a.jpg", "ffff0000ffff0000", ocr="flat off on 499"),
        analyzed("b.jpg", "ffff0000ffff0000", ocr="flat off on 799"),
    ]

    reps, duplicates, _ = scraper.deduplicate_analyzed_rows(rows)

    assert len(reps) == 2
    assert duplicates == 0


# --- regression: the same picture leading two groups (beauty search, 2026-10-06) ---

def test_exact_copy_joins_its_own_group_not_a_look_alike():
    # B is a different ad that merely looks similar to Z (same template crop).
    # Z is an exact copy of A. Z must land in A's group, never in B's.
    b = analyzed("b.jpg", "0000ffff0000ffff", crops=["ffff0000ffff0000"], rank_score=0.5)
    a = analyzed("a.jpg", "aaaaaaaaaaaaaaaa", crops=["0f0f0f0f0f0f0f0f"], rank_score=0.5)
    z = analyzed("z.jpg", "aaaaaaaaaaaaaaaa", crops=["ffff0000ffff0001"], rank_score=0.9)

    reps, duplicate_count, _groups = scraper.deduplicate_analyzed_rows([b, a, z])

    assert z["creative_group_id"] == a["creative_group_id"] != b["creative_group_id"]
    assert len({r["image_hash"] for r in reps}) == len(reps) == 2
    assert duplicate_count == 1


def test_groups_led_by_the_same_picture_are_merged(monkeypatch):
    # Force the old failure: Z (a copy of A) matches B's group instead of A's
    # and takes over as its lead. The final pass must merge the two groups.
    def fake_same(x, y):
        pair = {x["image_file"], y["image_file"]}
        z_already_grouped = "z.jpg" in pair and any(
            r["image_file"] == "z.jpg" and r.get("creative_group_id") for r in (x, y))
        # Z vs A "fails" while Z is being placed, but they really are the same ad.
        return pair == {"z.jpg", "b.jpg"} or (pair == {"z.jpg", "a.jpg"} and z_already_grouped), "forced"

    monkeypatch.setattr(scraper, "same_underlying_creative", fake_same)
    a = analyzed("a.jpg", "aaaaaaaaaaaaaaaa", rank_score=0.5)
    b = analyzed("b.jpg", "0000ffff0000ffff", rank_score=0.5)
    z = analyzed("z.jpg", "aaaaaaaaaaaaaaaa", rank_score=0.9)

    reps, duplicate_count, groups = scraper.deduplicate_analyzed_rows([a, b, z])

    assert [r["image_file"] for r in reps] == ["z.jpg"]
    assert len(groups) == 1 and duplicate_count == 2
    assert a["duplicate"] == b["duplicate"] == 1 and z.get("duplicate", 0) == 0
    assert a["creative_variant_of"] == b["creative_variant_of"] == "z.jpg"
    assert z["creative_variant_count"] == 2
    assert a["creative_group_id"] == b["creative_group_id"] == z["creative_group_id"]


def test_final_merge_still_keeps_different_prices_apart(monkeypatch):
    # Even when two groups end up led by the same picture, different prices stay apart.
    monkeypatch.setattr(scraper, "same_underlying_creative",
                        lambda x, y: (False, "forced") if {x["image_file"], y["image_file"]} != {"z.jpg", "b.jpg"}
                        else (True, "forced"))
    a = analyzed("a.jpg", "aaaaaaaaaaaaaaaa", rank_score=0.5)
    b = analyzed("b.jpg", "0000ffff0000ffff", rank_score=0.5)
    z = analyzed("z.jpg", "aaaaaaaaaaaaaaaa", rank_score=0.9)

    reps, _dups, _groups = scraper.deduplicate_analyzed_rows([a, b, z])

    assert sorted(r["image_file"] for r in reps) == ["a.jpg", "z.jpg"]
