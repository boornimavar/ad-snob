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
import json
import os
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

    def start_job(self):
        return self._post("/jobs")["job_id"]

    def send_packet(self, job_id, ads):
        return self._post(f"/jobs/{job_id}/ads", {"ads": ads}, timeout=PACKET_TIMEOUT_SECONDS)

    def finish(self, job_id):
        return self._post(f"/jobs/{job_id}/finish", timeout=PACKET_TIMEOUT_SECONDS)


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


def collect(category, country, service, on_status=print, search=search_ads_broad):
    """Search Meta, send every ad for analysis, and return the final results.

    Raises CollectorError with a plain-language message when something fails.
    """
    on_status(f"Searching Meta for '{category}' in {country} (up to {SEARCH_MAX_ADS} ads)...")
    interrupted = []

    def on_collect(count):
        if count % 30 == 0:
            on_status(f"  {count} ads received from Meta so far")

    try:
        records = search(
            query=category, country=country, status="ACTIVE", search_type="KEYWORD_UNORDERED",
            max_results=SEARCH_MAX_ADS, page_size=SEARCH_PAGE_SIZE, static_only=True,
            on_collect=on_collect, on_interrupted=interrupted.append,
        )
    except Exception as exc:
        print(f"[COLLECTOR] Meta search failed: {exc}", file=sys.stderr)
        raise CollectorError(SEARCH_FAILURE_MESSAGES[classify_failure(exc)]) from exc
    if not records:
        raise CollectorError(NO_ADS_MESSAGE)

    ads = ads_for_service(records, category, country)
    on_status(f"Collected {len(ads)} image ads. Sending them for analysis in packets of {PACKET_SIZE}...")

    job_id = service.start_job()
    for start in range(0, len(ads), PACKET_SIZE):
        reply = service.send_packet(job_id, ads[start:start + PACKET_SIZE])
        on_status(f"  analyzed {reply['total_in_job']}/{len(ads)}"
                  + (f" ({len(reply['failed'])} images could not be read)" if reply["failed"] else ""))
    on_status("Removing duplicates across the whole search...")
    final = service.finish(job_id)

    final["category"] = category
    final["country"] = country
    final["warning"] = PARTIAL_MESSAGES[classify_failure(interrupted[0])] if interrupted else ""
    final["summary"]["design_led"] = sum(
        1 for r in final["results"]
        if r["ok"] and r.get("is_creative_representative") == 1
        and isinstance(r.get("rank_score"), (int, float)) and r["rank_score"] >= DESIGN_LED_MIN_SCORE
    )
    return final


def save_results(final, results_dir=None):
    results_dir = results_dir or RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{safe_name(final['category'])}_{final['country']}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(final, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Search Meta for image ads and send them to Creative Lens.")
    parser.add_argument("category", help='what to search for, e.g. "skincare"')
    parser.add_argument("--country", default="IN", help="two-letter country code (default IN)")
    parser.add_argument("--api", default=os.environ.get("CREATIVE_LENS_API_URL", DEFAULT_API_URL),
                        help=f"analysis service address (default {DEFAULT_API_URL})")
    args = parser.parse_args(argv)

    service = AnalysisService(args.api, os.environ.get("CREATIVE_LENS_API_KEY"))
    try:
        final = collect(args.category.strip(), args.country.strip().upper(), service)
    except CollectorError as exc:
        print(f"\nSearch failed: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped. This search was not completed.")
        return 1

    s = final["summary"]
    print("\nDone.")
    if final["warning"]:
        print(f"Note: {final['warning']}")
    print(f"  Image ads collected:     {s['received']}")
    print(f"  Could not be analyzed:   {s['failed']}")
    print(f"  Unique after duplicates: {s['unique']}  ({s['duplicates']} duplicates removed)")
    print(f"  Design-led (score 0.5+): {s['design_led']}")
    print(f"Results saved to {save_results(final)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
