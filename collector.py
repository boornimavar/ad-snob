"""Creative Lens laptop collector.

Searches Meta's Ad Library from this laptop (Meta blocks cloud servers),
then sends the ads to the analysis service, which scores them and removes
duplicates. No AI libraries are needed here.

    python collector.py skincare
    python collector.py "vitamin gummies" --country IN --api http://127.0.0.1:8000

The service address can also be set with CREATIVE_LENS_API_URL, and its
password (if it has one) with CREATIVE_LENS_API_KEY.

A failed search is never restarted automatically: that only makes Meta's
rate limiting last longer. Wait, then run it again yourself.
"""
import argparse
import html
import json
import os
import secrets
import sys
import time
from pathlib import Path

import requests

from meta_collection import classify_failure, safe_name, search_ads_broad

DEFAULT_API_URL = "http://127.0.0.1:8000"
SEARCH_MAX_ADS = 500  # ask Meta for up to this many ads; filters leave ~230-390
SEARCH_PAGE_SIZE = 30  # ads per request to Meta (collector max ~30)
PACKET_SIZE = 50  # ads per upload to the analysis service
PACKET_TIMEOUT_SECONDS = 15 * 60  # scoring 50 ads on a free CPU host can take minutes
RESULTS_DIR = Path(__file__).resolve().parent / "collector_results"
DESIGN_LED_MIN_SCORE = 0.5  # same cut-off as the dashboard's "design-led only"

SEARCH_FAILURE_MESSAGES = {
    "rate_limited": "Meta is rate-limiting searches from this network. Wait a while - ideally a few hours - before searching again.",
    "no_response": "Meta stopped responding on this network. Try again later or from a different connection.",
    "error": "The search could not finish because of an unexpected error (details above).",
}
PARTIAL_MESSAGES = {
    "rate_limited": "Meta started rate-limiting this network partway, so the search stopped early. The ads collected so far were still analyzed.",
    "no_response": "Meta stopped responding partway, so the search stopped early. The ads collected so far were still analyzed.",
    "error": "The search stopped early because of an error. The ads collected so far were still analyzed.",
}
NO_ADS_MESSAGE = (
    "Meta returned no image ads. If this category usually has ads, Meta is probably "
    "limiting searches from this network: wait a few hours before trying again."
)

# Fields from each Meta record that travel with the ad and come back in the results.
DETAIL_FIELDS = (
    "page_name", "page_id", "ad_id", "creative_index", "body", "title", "cta", "link_url",
    "delivery_start_time", "delivery_stop_time", "publisher_platforms", "ad_type",
)


class CollectorError(Exception):
    """A problem to show the person as-is; the message is already plain language."""


class AnalysisService:
    """Talks to analysis_api.py: one search is one job, sent in packets."""

    def __init__(self, base_url, api_key=None, session=None):
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-API-Key": api_key} if api_key else {}
        self.session = session or requests.Session()

    def _post(self, path, payload=None, timeout=60):
        try:
            res = self.session.post(self.base_url + path, json=payload, headers=self.headers, timeout=timeout)
        except requests.RequestException as exc:
            raise CollectorError(
                f"Could not reach the analysis service at {self.base_url} ({type(exc).__name__}). "
                "Check the address and that the service is running."
            ) from exc
        try:
            body = res.json()
        except ValueError:
            body = {}
        if res.status_code >= 400:
            raise CollectorError(f"The analysis service refused the request ({res.status_code}): "
                                 f"{body.get('error') or res.text[:200]}")
        return body

    def start_job(self, category, country):
        return self._post("/jobs", {"category": category, "country": country})["job_id"]

    def send_packet(self, job_id, ads):
        return self._post(f"/jobs/{job_id}/ads", {"ads": ads}, timeout=PACKET_TIMEOUT_SECONDS)

    def finish(self, job_id):
        return self._post(f"/jobs/{job_id}/finish", timeout=PACKET_TIMEOUT_SECONDS)


class LocalAnalysis:
    """Scores on this laptop with exactly the code the analysis service runs,
    then saves to the team library - no server needed. The AI model is loaded
    on the first search (about a minute)."""

    def __init__(self, library=None):
        import analysis_api  # heavy (torch, CLIP), so only loaded when scoring locally
        self._api = analysis_api
        self._library = library
        self._jobs = {}

    def start_job(self, category, country):
        job_id = secrets.token_urlsafe(8)
        self._jobs[job_id] = self._api.new_job(category, country)
        return job_id

    def send_packet(self, job_id, ads):
        _ads, error = self._api._validate_packet({"ads": ads})
        if error:
            raise CollectorError(error)
        try:
            with self._api._analysis_lock:
                return self._api.add_packet_to_job(job_id, self._jobs[job_id], ads)
        except self._api.JobError as exc:
            raise CollectorError(str(exc)) from exc

    def finish(self, job_id):
        job = self._jobs.pop(job_id)
        with self._api._analysis_lock:
            return self._api.finish_job_results(job_id, job, self._library)


def default_service():
    """Score on this laptop, unless CREATIVE_LENS_API_URL points at a service."""
    url = os.environ.get("CREATIVE_LENS_API_URL")
    if url:
        return AnalysisService(url, os.environ.get("CREATIVE_LENS_API_KEY"))
    return LocalAnalysis()


def ads_for_service(records, category, country):
    """Turn Meta records into the service's ad format, one entry per image."""
    ads, seen = [], set()
    for record in records:
        ad_id = f"{record.get('ad_id', '')}_{record.get('creative_index', 0)}"
        if ad_id in seen:  # Meta occasionally repeats an ad across pages
            continue
        seen.add(ad_id)
        ad = {"id": ad_id, "image_url": record["image_url"], "category": category, "country": country,
              "brand": record.get("page_name", "")}
        ad.update({field: record.get(field, "") for field in DETAIL_FIELDS})
        ads.append(ad)
    return ads


STOPPED_MESSAGE = "Stopped. This search was not completed."


def collect(category, country, service, on_status=print, search=search_ads_broad, stop_check=None):
    """Search Meta, send every ad for analysis, and return the final results.

    Raises CollectorError with a plain-language message when something fails
    or when stop_check() turns true.
    """
    stop_check = stop_check or (lambda: False)
    on_status(f"Searching Meta for '{category}' in {country} (up to {SEARCH_MAX_ADS} ads)...")
    interrupted = []

    def on_collect(count):
        if count % 30 == 0:
            on_status(f"  {count} ads received from Meta so far")

    try:
        records = search(
            query=category, country=country, status="ACTIVE", search_type="KEYWORD_UNORDERED",
            max_results=SEARCH_MAX_ADS, page_size=SEARCH_PAGE_SIZE, static_only=True,
            on_collect=on_collect, on_interrupted=interrupted.append, stop_check=stop_check,
        )
    except Exception as exc:
        print(f"[COLLECTOR] Meta search failed: {exc}", file=sys.stderr)
        raise CollectorError(SEARCH_FAILURE_MESSAGES[classify_failure(exc)]) from exc
    if stop_check():
        raise CollectorError(STOPPED_MESSAGE)
    if not records:
        raise CollectorError(NO_ADS_MESSAGE)

    ads = ads_for_service(records, category, country)
    on_status(f"Collected {len(ads)} image ads. Scoring them in batches of {PACKET_SIZE} "
              "(the first search also loads the AI model, about a minute)...")

    job_id = service.start_job(category, country)
    for start in range(0, len(ads), PACKET_SIZE):
        if stop_check():
            raise CollectorError(STOPPED_MESSAGE)
        reply = service.send_packet(job_id, ads[start:start + PACKET_SIZE])
        on_status(f"  analyzed {reply['total_in_job']}/{len(ads)}"
                  + (f" ({len(reply['failed'])} images could not be read)" if reply["failed"] else ""))
    on_status("Removing duplicates and saving to the team library...")
    final = service.finish(job_id)

    final["category"] = category
    final["country"] = country
    final["warning"] = PARTIAL_MESSAGES[classify_failure(interrupted[0])] if interrupted else ""
    final["summary"]["design_led"] = len(design_led_results(final))
    return final


def design_led_results(final):
    """The ads the dashboard would show: no duplicates, score 0.5+, best first."""
    keep = [
        r for r in final["results"]
        if r["ok"] and r.get("is_creative_representative") == 1
        and isinstance(r.get("rank_score"), (int, float)) and r["rank_score"] >= DESIGN_LED_MIN_SCORE
    ]
    return sorted(keep, key=lambda r: -r["rank_score"])


def preview_html(final):
    """A simple gallery of the design-led ads, until the shared dashboard exists."""
    cards = "".join(
        f'<figure><a href="{html.escape(r["image_url"])}" target="_blank" rel="noreferrer">'
        f'<img loading="lazy" src="{html.escape(r["image_url"])}" alt=""></a>'
        f'<figcaption><b>{r["rank_score"]:.2f}</b> · {html.escape(str(r["details"].get("brand", "")))}'
        + (f' · {r["creative_variant_count"]} copies hidden' if r.get("creative_variant_count") else "")
        + "</figcaption></figure>"
        for r in design_led_results(final)
    )
    s = final["summary"]
    title = html.escape(f"{final['category']} ({final['country']})")
    note = f"<p class=note>{html.escape(final['warning'])}</p>" if final.get("warning") else ""
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Creative Lens: {title}</title>
<style>body{{font-family:sans-serif;margin:16px;background:#f4f4f4;color:#222}}h1{{font-size:20px;margin:0 0 4px}}
p{{margin:4px 0 14px;color:#555}}.note{{color:#8a5300}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}}
figure{{margin:0;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px #0002}}
img{{width:100%;aspect-ratio:1;object-fit:contain;background:#eee;display:block}}
figcaption{{padding:8px;font-size:13px}}</style>
<h1>{title}: {s['design_led']} design-led ads, best first</h1>
<p>{s['received']} images collected · {s['unique']} unique after removing duplicates ·
{s['design_led']} scored 0.5 or higher. Click a picture to open it full size.
Meta's picture links stop working after a few days.</p>{note}
<div class="grid">{cards}</div>"""


def save_results(final, results_dir=None):
    """Save the full results (JSON) and the preview page; return both paths."""
    results_dir = results_dir or RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{safe_name(final['category'])}_{final['country']}_{time.strftime('%Y%m%d_%H%M%S')}"
    json_path = results_dir / f"{stem}.json"
    json_path.write_text(json.dumps(final, ensure_ascii=False, indent=1), encoding="utf-8")
    preview_path = results_dir / f"{stem}.html"
    preview_path.write_text(preview_html(final), encoding="utf-8")
    return json_path, preview_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Search Meta for image ads and send them to Creative Lens.")
    parser.add_argument("category", help='what to search for, e.g. "skincare"')
    parser.add_argument("--country", default="IN", help="two-letter country code (default IN)")
    parser.add_argument("--api", default=os.environ.get("CREATIVE_LENS_API_URL"),
                        help=f"use an analysis service (e.g. {DEFAULT_API_URL}) instead of scoring on this laptop")
    args = parser.parse_args(argv)

    service = (AnalysisService(args.api, os.environ.get("CREATIVE_LENS_API_KEY")) if args.api
               else LocalAnalysis())
    try:
        final = collect(args.category.strip(), args.country.strip().upper(), service)
    except CollectorError as exc:
        print(f"\nSearch failed: {exc}")
        return 1
    except KeyboardInterrupt:
        print(f"\n{STOPPED_MESSAGE}")
        return 1

    s = final["summary"]
    print("\nDone.")
    if final["warning"]:
        print(f"Note: {final['warning']}")
    print(f"  Image ads collected:     {s['received']}")
    print(f"  Could not be analyzed:   {s['failed']}")
    print(f"  Unique after duplicates: {s['unique']}  ({s['duplicates']} duplicates removed)")
    print(f"  Design-led (score 0.5+): {s['design_led']}")
    if final.get("library"):
        print(f"  Team library:            {final['library']['new']} new, "
              f"{final['library']['seen_again']} already there (marked as still active)")
    if final.get("library_error"):
        print(f"  Team library:            NOT saved. {final['library_error']}")
    json_path, preview_path = save_results(final)
    print(f"Preview of the design-led ads: {preview_path}")
    print(f"Full results: {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
