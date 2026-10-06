"""Tests for the analysis API. CLIP/OCR are replaced by a fake, so these run fast."""
import base64
import io

import pytest
from PIL import Image

import analysis_api
import meta_design_scraper_session as scraper


def png_base64(color, size=(40, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def fake_scores(word_count=6):
    return {
        "design_score": "", "clip_design_score": 0.7, "clip_product_score": 0.3, "clip_embedding": "",
        "broad_graphic_prob": 0.6, "broad_photo_prob": 0.2, "broad_video_prob": 0.1, "broad_product_prob": 0.1,
        "ocr_word_count": word_count, "ocr_text_density": 0.12, "ocr_score": 0.8,
        "visual_complexity": 0.5, "background_uniformity": 0.4, "creative_score": 0.7,
        "image_width": 40, "image_height": 30, "aspect_ratio": 1.3333,
        "ocr_fingerprint": "flat 50% off serum", "filter_status": "KEEP", "filter_reason": "",
    }


@pytest.fixture
def api(monkeypatch):
    """Test client with model loading and image analysis faked.

    The fake image hash is the image's colour, so two ads with the same colour
    count as an exact duplicate.
    """
    calls = {"load_models": 0, "batch_sizes": [], "images": []}

    def fake_load_models(_path):
        calls["load_models"] += 1

    def fake_analyze(images):
        calls["batch_sizes"].append(len(images))
        out = []
        for image in images:
            if image is None:
                out.append(None)
                continue
            calls["images"].append(image.size)
            r, g, b = image.getpixel((0, 0))
            image_hash = f"{r:02x}{g:02x}{b:02x}0000000000"
            out.append((fake_scores(), image_hash, f'["{image_hash}"]', None))
        return out

    monkeypatch.setattr(scraper, "load_models", fake_load_models)
    monkeypatch.setattr(scraper, "analyze_images_batch", fake_analyze)
    monkeypatch.setattr(analysis_api, "_models_loaded", False)
    monkeypatch.setattr(analysis_api, "_jobs", {})
    monkeypatch.delenv("CREATIVE_LENS_API_KEY", raising=False)
    client = analysis_api.app.test_client()
    client.calls = calls
    return client


def post(client, ads, **kwargs):
    return client.post("/analyze", json={"ads": ads}, **kwargs)


def test_home_page_says_it_is_running(api, monkeypatch):
    monkeypatch.setenv("CREATIVE_LENS_API_KEY", "secret")  # browsing needs no key
    res = api.get("/")
    assert res.status_code == 200
    assert b"analysis service is running" in res.data


def test_health(api):
    assert api.get("/health").get_json() == {"status": "ok", "models_loaded": False}


@pytest.mark.parametrize("body, message", [
    ({}, "Send JSON like"),
    ({"ads": []}, "empty"),
    ({"ads": [{"image_url": "https://x/a.jpg"}]}, "needs an id"),
    ({"ads": [{"id": 1, "image_url": "https://x/a.jpg"}, {"id": "1", "image_url": "https://x/b.jpg"}]}, "more than once"),
    ({"ads": [{"id": 1}]}, "exactly one of"),
    ({"ads": [{"id": 1, "image_url": "https://x/a.jpg", "image_base64": "abc"}]}, "exactly one of"),
    ({"ads": [{"id": 1, "image_url": "file:///etc/passwd"}]}, "not an http(s) link"),
])
def test_rejects_malformed_requests(api, body, message):
    res = api.post("/analyze", json=body)
    assert res.status_code == 400
    assert message in res.get_json()["error"]
    assert api.calls["load_models"] == 0


def test_rejects_too_many_ads(api):
    ads = [{"id": n, "image_url": f"https://x/{n}.jpg"} for n in range(analysis_api.MAX_ADS_PER_PACKET + 1)]
    res = post(api, ads)
    assert res.status_code == 400
    assert "at most" in res.get_json()["error"]


def test_api_key_required_when_configured(api, monkeypatch):
    monkeypatch.setenv("CREATIVE_LENS_API_KEY", "secret")
    ads = [{"id": "a", "image_base64": png_base64("red")}]
    assert post(api, ads).status_code == 401
    assert post(api, ads, headers={"X-API-Key": "wrong"}).status_code == 401
    assert post(api, ads, headers={"X-API-Key": "secret"}).status_code == 200
    assert api.post("/jobs").status_code == 401
    assert api.get("/health").status_code == 200


def test_scores_base64_image(api):
    data = post(api, [{"id": "ad-1", "image_base64": png_base64("red")}]).get_json()

    assert data["summary"] == {"received": 1, "analyzed": 1, "failed": 0, "unique": 1, "duplicates": 0}
    result = data["results"][0]
    assert result["id"] == "ad-1"
    assert result["ok"] is True
    assert result["filter_status"] == "CANDIDATE"
    assert 0 <= result["rank_score"] <= 1
    assert result["is_creative_representative"] == 1
    assert "_sift_descriptors" not in result and "image_file" not in result
    assert api.calls["images"] == [(40, 30)]


def test_downloads_image_urls(api, monkeypatch):
    seen = {}

    def fake_fetch(urls):
        seen["urls"] = urls
        return [Image.new("RGB", (40, 30), "blue"), None]

    monkeypatch.setattr(scraper, "fetch_images_in_memory", fake_fetch)
    data = post(api, [
        {"id": "ok", "image_url": "https://cdn.example/ok.jpg"},
        {"id": "broken", "image_url": "https://cdn.example/broken.jpg"},
    ]).get_json()

    assert seen["urls"] == ["https://cdn.example/ok.jpg", "https://cdn.example/broken.jpg"]
    assert [r["ok"] for r in data["results"]] == [True, False]
    assert "Could not download" in data["results"][1]["error"]
    assert data["summary"]["failed"] == 1


def test_bad_image_fails_alone(api):
    data = post(api, [
        {"id": "bad", "image_base64": base64.b64encode(b"not an image").decode()},
        {"id": "good", "image_base64": png_base64("green")},
    ]).get_json()

    bad, good = data["results"]
    assert bad == {"id": "bad", "details": {}, "ok": False, "error": "image_base64 is not a readable image."}
    assert good["ok"] is True


def test_groups_duplicates_and_keeps_input_order(api):
    data = post(api, [
        {"id": "first", "image_base64": png_base64("red")},
        {"id": "other", "image_base64": png_base64("blue")},
        {"id": "copy", "image_base64": png_base64("red", size=(40, 30))},
    ]).get_json()

    first, other, copy = data["results"]
    assert [r["id"] for r in data["results"]] == ["first", "other", "copy"]
    assert first["creative_group_id"] == copy["creative_group_id"] != other["creative_group_id"]
    assert first["is_creative_representative"] == 1 and first["creative_variant_count"] == 1
    assert copy["duplicate"] == 1 and copy["creative_variant_of"] == "first"
    assert data["summary"]["unique"] == 2 and data["summary"]["duplicates"] == 1


def test_video_frames_are_excluded(api):
    data = post(api, [{"id": "v", "image_base64": png_base64("red"), "is_probable_video_frame": True}]).get_json()
    assert data["results"][0]["filter_status"] == "EXCLUDED"
    assert data["results"][0]["rank_score"] == ""


def test_analyzes_in_clip_sized_batches_and_loads_models_once(api):
    ads = [{"id": n, "image_base64": png_base64((n, 0, 0))} for n in range(scraper.CLIP_BATCH_SIZE + 3)]
    post(api, ads)
    post(api, ads[:1])

    assert api.calls["batch_sizes"] == [scraper.CLIP_BATCH_SIZE, 3, 1]
    assert api.calls["load_models"] == 1
    assert api.get("/health").get_json()["models_loaded"] is True


# --- search jobs: one search sent in several packets -------------------------

def start_job(client):
    res = client.post("/jobs")
    assert res.status_code == 201
    return res.get_json()["job_id"]


def send_packet(client, job_id, ads):
    return client.post(f"/jobs/{job_id}/ads", json={"ads": ads})


def test_duplicates_are_found_across_packets(api):
    job_id = start_job(api)
    send_packet(api, job_id, [{"id": "first", "image_base64": png_base64("red")}])
    send_packet(api, job_id, [{"id": "other", "image_base64": png_base64("blue")}])
    send_packet(api, job_id, [{"id": "copy", "image_base64": png_base64("red")}])

    data = api.post(f"/jobs/{job_id}/finish").get_json()

    first, other, copy = data["results"]
    assert [r["id"] for r in data["results"]] == ["first", "other", "copy"]
    assert copy["duplicate"] == 1 and copy["creative_variant_of"] == "first"
    assert first["creative_group_id"] == copy["creative_group_id"] != other["creative_group_id"]
    assert data["summary"] == {"received": 3, "analyzed": 3, "failed": 0, "unique": 2, "duplicates": 1}


def test_packet_reply_reports_progress_and_failures(api):
    job_id = start_job(api)
    send_packet(api, job_id, [{"id": "a", "image_base64": png_base64("red")}])

    reply = send_packet(api, job_id, [
        {"id": "b", "image_base64": png_base64("blue")},
        {"id": "bad", "image_base64": base64.b64encode(b"nope").decode()},
    ]).get_json()

    assert reply == {
        "job_id": job_id, "received": 2, "analyzed": 1, "total_in_job": 3,
        "failed": [{"id": "bad", "error": "image_base64 is not a readable image."}],
    }


def test_ad_details_are_returned_unchanged(api):
    job_id = start_job(api)
    send_packet(api, job_id, [{
        "id": "a", "image_url": "https://cdn.example/a.jpg", "brand": "Dot & Key",
        "body": "Glow serum", "creative_index": 2,
    }])
    result = api.post(f"/jobs/{job_id}/finish").get_json()["results"][0]

    assert result["image_url"] == "https://cdn.example/a.jpg"
    assert result["details"] == {"brand": "Dot & Key", "body": "Glow serum", "creative_index": 2}


def test_image_bytes_are_not_kept_or_echoed(api):
    job_id = start_job(api)
    send_packet(api, job_id, [{"id": "a", "image_base64": png_base64("red")}])

    assert "image_base64" not in analysis_api._jobs[job_id]["scored"][0]["ad"]
    result = api.post(f"/jobs/{job_id}/finish").get_json()["results"][0]
    assert "image_base64" not in result and "image_base64" not in result["details"]


def test_same_ad_id_twice_in_a_job_is_rejected(api):
    job_id = start_job(api)
    send_packet(api, job_id, [{"id": "a", "image_base64": png_base64("red")}])

    res = send_packet(api, job_id, [{"id": "a", "image_base64": png_base64("blue")}])

    assert res.status_code == 400
    assert "already sent" in res.get_json()["error"]


def test_job_size_limit(api, monkeypatch):
    monkeypatch.setattr(analysis_api, "MAX_ADS_PER_JOB", 2)
    job_id = start_job(api)
    send_packet(api, job_id, [{"id": "a", "image_base64": png_base64("red")}])

    res = send_packet(api, job_id, [{"id": n, "image_base64": png_base64("blue")} for n in ("b", "c")])

    assert res.status_code == 400
    assert "at most 2" in res.get_json()["error"]


def test_finished_or_unknown_jobs_are_gone(api):
    job_id = start_job(api)
    send_packet(api, job_id, [{"id": "a", "image_base64": png_base64("red")}])
    assert api.post(f"/jobs/{job_id}/finish").status_code == 200

    assert api.post(f"/jobs/{job_id}/finish").status_code == 404
    res = send_packet(api, job_id, [{"id": "b", "image_base64": png_base64("red")}])
    assert res.status_code == 404
    assert "Start a new job" in res.get_json()["error"]


def test_idle_jobs_expire(api, monkeypatch):
    job_id = start_job(api)
    now = analysis_api.time.time()
    monkeypatch.setattr(analysis_api.time, "time", lambda: now + analysis_api.JOB_IDLE_EXPIRY_SECONDS + 1)

    assert api.post(f"/jobs/{job_id}/finish").status_code == 404


# --- the shared library ------------------------------------------------------

def test_finishing_a_job_with_a_category_saves_unique_ads(api, fresh_library):
    job_id = api.post("/jobs", json={"category": "Skincare", "country": "in"}).get_json()["job_id"]
    send_packet(api, job_id, [
        {"id": "a", "image_base64": png_base64("red"), "brand": "Dot & Key"},
        {"id": "copy", "image_base64": png_base64("red")},
        {"id": "b", "image_base64": png_base64("blue")},
    ])

    data = api.post(f"/jobs/{job_id}/finish").get_json()

    assert data["library"] == {"new": 2, "seen_again": 0}
    rows = fresh_library.browse("skincare", "IN", design_led_only=False)
    assert sorted(r["image_hash"][:6] for r in rows) == ["0000ff", "ff0000"]
    assert all(r["thumbnail_url"] for r in rows)


def test_job_without_category_is_not_saved(api, fresh_library):
    job_id = start_job(api)
    send_packet(api, job_id, [{"id": "a", "image_base64": png_base64("red")}])

    assert "library" not in api.post(f"/jobs/{job_id}/finish").get_json()
    assert fresh_library.browse(design_led_only=False) == []


def test_library_can_be_browsed_without_the_key(api, monkeypatch):
    job_id = api.post("/jobs", json={"category": "skincare"}).get_json()["job_id"]
    send_packet(api, job_id, [{"id": "a", "image_base64": png_base64("red")}])
    api.post(f"/jobs/{job_id}/finish")
    monkeypatch.setenv("CREATIVE_LENS_API_KEY", "secret")

    rows = api.get("/library?category=skincare&design_led_only=false").get_json()["ads"]
    picture = api.get(rows[0]["thumbnail_url"])
    categories = api.get("/library/categories").get_json()["categories"]

    assert len(rows) == 1
    assert picture.status_code == 200 and picture.mimetype == "image/jpeg"
    assert Image.open(io.BytesIO(picture.data)).format == "JPEG"
    assert categories == [{"category": "skincare", "country": "IN", "active": 1, "archived": 0}]
    assert api.post("/jobs").status_code == 401  # writing still needs the key


def test_unknown_thumbnail_is_404(api):
    assert api.get("/thumbnails/nope.jpg").status_code == 404
