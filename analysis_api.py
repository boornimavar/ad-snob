"""Creative Lens analysis API.

Takes ad images (as URLs or as base64 bytes) and returns design scores and
duplicate groups. It never contacts Meta's Ad Library: the laptop collector
finds the ads and sends them here.

Run locally:
    python analysis_api.py            # http://127.0.0.1:8000

One search is one job, so duplicates are found across the whole search
(like the old pipeline), even though ads arrive in packets:
    POST /jobs                 -> {"job_id": "..."}
    POST /jobs/<job_id>/ads    -> score one packet of up to 50 ads
    POST /jobs/<job_id>/finish -> group duplicates across every packet and
                                  return the final results for the search
A packet looks like:
    {"ads": [{"id": "...", "image_url": "https://..."}
             or {"id": "...", "image_base64": "..."},
             optional "is_probable_video_frame": true,
             any other fields (brand, ad text, ...) are returned unchanged
             under "details"]}

POST /analyze takes one packet and returns final results straight away
(a job of one packet). GET /health checks the server is up.

If the CREATIVE_LENS_API_KEY environment variable is set, every request
except /health must send the same value in an X-API-Key header.
"""
import base64
import binascii
import hmac
import io
import os
import secrets
import threading
import time

from flask import Flask, jsonify, request
from PIL import Image

import meta_design_scraper_session as scraper

MAX_ADS_PER_PACKET = 50
MAX_ADS_PER_JOB = 500  # matches the 500-ad search cap; real searches yield ~230-390
JOB_IDLE_EXPIRY_SECONDS = 2 * 60 * 60  # forget abandoned searches
MAX_REQUEST_BYTES = 64 * 1024 * 1024  # 50 base64 images of ~1 MB each fit comfortably
IMAGE_FIELDS = ("image_url", "image_base64")
CONTROL_FIELDS = ("id", "is_probable_video_frame") + IMAGE_FIELDS

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES

# CLIP is large; load it once and run one packet at a time so a burst of
# requests can't exhaust memory on a small free host. Job contents are only
# changed while holding this lock.
_analysis_lock = threading.Lock()
_models_loaded = False

# In memory only: a job lives for the few minutes one search takes. If the
# server restarts mid-search, the collector gets "unknown job" and starts over.
_jobs = {}
_jobs_lock = threading.Lock()


def _ensure_models_loaded():
    global _models_loaded
    if not _models_loaded:
        scraper.load_models(scraper.ML_MODEL_PATH)
        _models_loaded = True


def _api_key_ok():
    expected = os.environ.get("CREATIVE_LENS_API_KEY", "")
    if not expected:
        return True
    return hmac.compare_digest(request.headers.get("X-API-Key", ""), expected)


@app.before_request
def _require_api_key():
    if request.endpoint not in ("health", None) and not _api_key_ok():
        return jsonify({"error": "Missing or wrong X-API-Key header."}), 401


def _error(message, status=400):
    return jsonify({"error": message}), status


def _validate_packet(body):
    """Return (ads, None) or (None, error message)."""
    if not isinstance(body, dict) or not isinstance(body.get("ads"), list):
        return None, 'Send JSON like {"ads": [{"id": "...", "image_url": "..."}]}'
    ads = body["ads"]
    if not ads:
        return None, "The ads list is empty."
    if len(ads) > MAX_ADS_PER_PACKET:
        return None, f"Too many ads in one packet ({len(ads)}); send at most {MAX_ADS_PER_PACKET}."
    seen_ids = set()
    for n, ad in enumerate(ads, start=1):
        if not isinstance(ad, dict):
            return None, f"Ad #{n} is not an object."
        ad_id = ad.get("id")
        if not isinstance(ad_id, (str, int)) or isinstance(ad_id, bool) or str(ad_id) == "":
            return None, f"Ad #{n} needs an id (text or number)."
        if str(ad_id) in seen_ids:
            return None, f"Ad id {ad_id!r} appears more than once."
        seen_ids.add(str(ad_id))
        url, data = ad.get("image_url"), ad.get("image_base64")
        if bool(url) == bool(data):
            return None, f"Ad {ad_id!r} needs exactly one of image_url or image_base64."
        if url and not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            return None, f"Ad {ad_id!r} has an image_url that is not an http(s) link."
        if data and not isinstance(data, str):
            return None, f"Ad {ad_id!r} has an image_base64 that is not text."
    return ads, None


def _decode_base64_image(data):
    try:
        raw = base64.b64decode(data, validate=True)
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        image.load()
        return image
    except (binascii.Error, ValueError, OSError, Image.DecompressionBombError):
        return None


def _load_images(ads):
    """Return (images, errors), one entry per ad, in input order."""
    images = [None] * len(ads)
    errors = [None] * len(ads)

    url_positions = [i for i, ad in enumerate(ads) if ad.get("image_url")]
    fetched = scraper.fetch_images_in_memory([ads[i]["image_url"] for i in url_positions])
    for i, image in zip(url_positions, fetched):
        images[i] = image
        if image is None:
            errors[i] = "Could not download the image from image_url."

    for i, ad in enumerate(ads):
        if ad.get("image_base64"):
            images[i] = _decode_base64_image(ad["image_base64"])
            if images[i] is None:
                errors[i] = "image_base64 is not a readable image."
    return images, errors


def score_ads(ads):
    """Run the full image analysis on one packet.

    Returns one entry per ad: {"ad", "row", "error"}. "ad" is the request
    entry without the image bytes; "row" is None when the ad failed.
    """
    images, errors = _load_images(ads)
    try:
        analyses = []
        for start in range(0, len(images), scraper.CLIP_BATCH_SIZE):
            analyses.extend(scraper.analyze_images_batch(images[start:start + scraper.CLIP_BATCH_SIZE]))
    finally:
        for image in images:
            if image is not None:
                image.close()

    scored = []
    for ad, analysis, error in zip(ads, analyses, errors):
        row = None
        if analysis is None:
            error = error or "The image could not be analyzed."
        else:
            scores, image_hash, frame_hash, sift_signature = analysis
            scraper.apply_candidate_ranking(scores, bool(ad.get("is_probable_video_frame")))
            row = {
                "image_file": str(ad["id"]),  # deduplication names representatives by image_file
                "image_hash": image_hash,
                "frame_variant_hash": frame_hash,
                "_sift_descriptors": sift_signature,
                "duplicate": 0,
                **scores,
            }
        scored.append({"ad": {k: v for k, v in ad.items() if k != "image_base64"}, "row": row, "error": error})
    return scored


def final_results(scored):
    """Group duplicates across every scored ad and build the response body."""
    analyzed_rows = [entry["row"] for entry in scored if entry["row"] is not None]
    _representatives, duplicate_count, _groups = scraper.deduplicate_analyzed_rows(analyzed_rows)

    results = []
    for entry in scored:
        ad, row = entry["ad"], entry["row"]
        result = {"id": ad["id"]}
        if ad.get("image_url"):
            result["image_url"] = ad["image_url"]
        result["details"] = {k: v for k, v in ad.items() if k not in CONTROL_FIELDS}
        if row is None:
            result.update(ok=False, error=entry["error"])
        else:
            result["ok"] = True
            result.update({k: v for k, v in row.items() if k not in ("_sift_descriptors", "image_file")})
        results.append(result)

    return {
        "results": results,
        "summary": {
            "received": len(scored),
            "analyzed": len(analyzed_rows),
            "failed": len(scored) - len(analyzed_rows),
            "unique": len(analyzed_rows) - duplicate_count,
            "duplicates": duplicate_count,
        },
    }


def _expire_idle_jobs_locked():
    cutoff = time.time() - JOB_IDLE_EXPIRY_SECONDS
    for job_id in [j for j, job in _jobs.items() if job["last_activity"] < cutoff]:
        del _jobs[job_id]


def _get_job(job_id):
    with _jobs_lock:
        _expire_idle_jobs_locked()
        return _jobs.get(job_id)


UNKNOWN_JOB = "Unknown job: it finished already, expired, or the server restarted. Start a new job."


@app.route("/health")
def health():
    return jsonify({"status": "ok", "models_loaded": _models_loaded})


@app.route("/jobs", methods=["POST"])
def create_job():
    job_id = secrets.token_urlsafe(16)
    with _jobs_lock:
        _expire_idle_jobs_locked()
        _jobs[job_id] = {"scored": [], "ids": set(), "last_activity": time.time()}
    return jsonify({"job_id": job_id}), 201


@app.route("/jobs/<job_id>/ads", methods=["POST"])
def add_packet(job_id):
    ads, error = _validate_packet(request.get_json(silent=True))
    if error:
        return _error(error)
    with _analysis_lock:
        job = _get_job(job_id)
        if job is None:
            return _error(UNKNOWN_JOB, 404)
        repeated = [ad["id"] for ad in ads if str(ad["id"]) in job["ids"]]
        if repeated:
            return _error(f"Ad id {repeated[0]!r} was already sent in this job.")
        if len(job["scored"]) + len(ads) > MAX_ADS_PER_JOB:
            return _error(f"A job holds at most {MAX_ADS_PER_JOB} ads.")

        _ensure_models_loaded()
        scored = score_ads(ads)
        job["scored"].extend(scored)
        job["ids"].update(str(ad["id"]) for ad in ads)
        job["last_activity"] = time.time()

    failed = [{"id": e["ad"]["id"], "error": e["error"]} for e in scored if e["row"] is None]
    return jsonify({
        "job_id": job_id,
        "received": len(scored),
        "analyzed": len(scored) - len(failed),
        "failed": failed,
        "total_in_job": len(job["scored"]),
    })


@app.route("/jobs/<job_id>/finish", methods=["POST"])
def finish_job(job_id):
    with _analysis_lock:
        with _jobs_lock:
            _expire_idle_jobs_locked()
            job = _jobs.pop(job_id, None)
        if job is None:
            return _error(UNKNOWN_JOB, 404)
        return jsonify({"job_id": job_id, **final_results(job["scored"])})


@app.route("/analyze", methods=["POST"])
def analyze():
    ads, error = _validate_packet(request.get_json(silent=True))
    if error:
        return _error(error)
    with _analysis_lock:
        _ensure_models_loaded()
        return jsonify(final_results(score_ads(ads)))


@app.errorhandler(413)
def too_large(_exc):
    return _error(f"Request is larger than {MAX_REQUEST_BYTES // (1024 * 1024)} MB; send fewer ads at once.", 413)


@app.errorhandler(500)
def server_error(exc):
    original = getattr(exc, "original_exception", None) or exc
    return _error(f"Analysis failed: {type(original).__name__}: {original}", 500)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))
