"""Tests for the laptop collector, talking to the real analysis service.

Meta and the image scoring are faked: nothing here touches the network.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import requests
from PIL import Image

import analysis_api
import collector
import meta_design_scraper_session as scraper

ROOT = Path(__file__).resolve().parent.parent


class FlaskSession:
    """Lets AnalysisService talk to the in-process Flask test client."""

    def __init__(self, client):
        self.client = client

    def post(self, url, json=None, headers=None, timeout=None):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        return FlaskResponse(self.client.post(path, json=json, headers=headers))


class FlaskResponse:
    def __init__(self, res):
        self.status_code = res.status_code
        self.text = res.get_data(as_text=True)
        self._res = res

    def json(self):
        return self._res.get_json()


def record(n, color="red", **overrides):
    """A Meta record as search_ads_broad() returns it. The image URL names its colour."""
    rec = {"ad_id": str(1000 + n), "page_id": "p1", "page_name": "Dot & Key", "creative_index": 0,
           "image_url": f"https://img.example/{color}.jpg", "body": f"Ad copy {n}", "title": "Glow",
           "cta": "Shop now", "link_url": "https://shop.example"}
    rec.update(overrides)
    return rec


class FakeSearch:
    def __init__(self, records=(), fail_with=None, interrupt_with=None):
        self.records, self.fail_with, self.interrupt_with = list(records), fail_with, interrupt_with
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_with:
            raise self.fail_with
        for n in range(1, len(self.records) + 1):
            kwargs["on_collect"](n)
        if self.interrupt_with:
            kwargs["on_interrupted"](self.interrupt_with)
        return self.records


@pytest.fixture
def service(monkeypatch):
    """A real analysis service with image download and scoring faked.

    Each image is a solid colour named in its URL; the fake score for a
    colour is fixed, and same colour = same image hash = duplicate.
    """
    colours = {"red": (255, 0, 0), "blue": (0, 0, 255), "green": (0, 128, 0)}
    rank = {"red": 0.9, "blue": 0.7, "green": 0.2}
    packets = []

    def fake_fetch(urls):
        packets.append(len(urls))
        return [Image.new("RGB", (40, 40), colours[u.rsplit("/", 1)[1][:-4]]) for u in urls]

    def fake_analyze(images):
        out = []
        for image in images:
            colour = next(name for name, rgb in colours.items() if rgb == image.getpixel((0, 0)))
            image_hash = {"red": "ff00000000000000", "blue": "00ff000000000000", "green": "0000ff0000000000"}[colour]
            out.append(({"ocr_word_count": 5, "image_width": 40, "image_height": 40, "aspect_ratio": 1.0,
                         "ocr_fingerprint": colour, "creative_score": 0.5, "filter_status": "KEEP",
                         "filter_reason": "", "_colour": colour}, image_hash, f'["{image_hash}"]', None))
        return out

    monkeypatch.setattr(scraper, "fetch_images_in_memory", fake_fetch)
    monkeypatch.setattr(scraper, "analyze_images_batch", fake_analyze)
    monkeypatch.setattr(scraper, "load_models", lambda _path: None)
    monkeypatch.setattr(scraper, "rank_score", lambda scores: rank[scores["_colour"]])
    monkeypatch.setattr(scraper, "classify_broad_static", lambda scores: ("", "note"))
    monkeypatch.setattr(analysis_api, "_jobs", {})
    monkeypatch.delenv("CREATIVE_LENS_API_KEY", raising=False)
    svc = collector.AnalysisService("http://api.test", session=FlaskSession(analysis_api.app.test_client()))
    svc.packets = packets
    return svc


def test_collects_sends_in_packets_and_dedups_across_the_whole_search(service):
    # 120 image ads -> packets of 50, 50, 20. Ad 0 and ad 110 are the same picture.
    records = [record(n, "red" if n in (0, 110) else ("blue" if n % 2 else "green")) for n in range(120)]
    search = FakeSearch(records)
    statuses = []

    final = collector.collect("skincare", "IN", service, on_status=statuses.append, search=search)

    assert service.packets == [50, 50, 20]
    s = final["summary"]
    assert (s["received"], s["analyzed"], s["failed"]) == (120, 120, 0)
    # red, blue and green are each one creative; everything else is a copy of one of them
    assert s["unique"] == 3 and s["duplicates"] == 117
    assert s["design_led"] == 2  # red (0.9) and blue (0.7); green scores 0.2
    by_id = {r["id"]: r for r in final["results"]}
    assert by_id["1110_0"]["creative_variant_of"] == "1000_0"
    assert by_id["1000_0"]["details"]["brand"] == "Dot & Key"
    assert by_id["1000_0"]["details"]["body"] == "Ad copy 0"
    assert final["warning"] == ""
    assert "  30 ads received from Meta so far" in statuses


def test_search_uses_the_agreed_meta_settings(service):
    search = FakeSearch([record(0)])
    collector.collect("skincare", "IN", service, on_status=lambda _m: None, search=search)

    call = search.calls[0]
    assert (call["max_results"], call["page_size"], call["static_only"]) == (500, 30, True)
    assert (call["query"], call["country"], call["status"]) == ("skincare", "IN", "ACTIVE")


def test_rate_limited_search_explains_and_does_not_retry(service):
    search = FakeSearch(fail_with=RuntimeError("Max retries exceeded due to rate limiting"))

    with pytest.raises(collector.CollectorError, match="rate-limiting"):
        collector.collect("skincare", "IN", service, on_status=lambda _m: None, search=search)

    assert len(search.calls) == 1
    assert service.packets == []


def test_no_ads_explains_possible_rate_limit(service):
    with pytest.raises(collector.CollectorError, match="wait a few hours"):
        collector.collect("skincare", "IN", service, on_status=lambda _m: None, search=FakeSearch([]))


def test_search_cut_short_still_analyzes_what_was_collected(service):
    search = FakeSearch([record(0), record(1, "blue")], interrupt_with=RuntimeError("rate limiting"))

    final = collector.collect("skincare", "IN", service, on_status=lambda _m: None, search=search)

    assert final["summary"]["analyzed"] == 2
    assert "rate-limiting this network partway" in final["warning"]


def test_unreachable_service_is_explained():
    class Down:
        def post(self, *args, **kwargs):
            raise requests.ConnectionError("refused")

    svc = collector.AnalysisService("http://nowhere.test", session=Down())
    with pytest.raises(collector.CollectorError, match="Could not reach the analysis service"):
        collector.collect("skincare", "IN", svc, on_status=lambda _m: None, search=FakeSearch([record(0)]))


def test_service_refusal_shows_its_reason(service, monkeypatch):
    monkeypatch.setenv("CREATIVE_LENS_API_KEY", "secret")  # the service now wants a key we don't send

    with pytest.raises(collector.CollectorError, match="401.*X-API-Key"):
        collector.collect("skincare", "IN", service, on_status=lambda _m: None, search=FakeSearch([record(0)]))


def test_repeated_meta_records_are_sent_once():
    ads = collector.ads_for_service([record(0), record(0), record(0, creative_index=1)], "skincare", "IN")

    assert [a["id"] for a in ads] == ["1000_0", "1000_1"]
    assert ads[0]["category"] == "skincare" and ads[0]["brand"] == "Dot & Key"


def test_command_line_prints_summary_and_saves_results(monkeypatch, tmp_path, capsys):
    final = {"category": "skin care", "country": "IN", "warning": "",
             "summary": {"received": 3, "analyzed": 3, "failed": 0, "unique": 2, "duplicates": 1, "design_led": 1},
             "results": []}
    monkeypatch.setattr(collector, "collect", lambda *args, **kwargs: final)
    monkeypatch.setattr(collector, "RESULTS_DIR", tmp_path)

    assert collector.main(["skin care"]) == 0

    out = capsys.readouterr().out
    assert "Unique after duplicates: 2  (1 duplicates removed)" in out
    assert "Design-led (score 0.5+): 1" in out
    saved = list(tmp_path.glob("skin_care_IN_*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text(encoding="utf-8"))["summary"]["unique"] == 2


def test_command_line_reports_failure(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise collector.CollectorError(collector.NO_ADS_MESSAGE)

    monkeypatch.setattr(collector, "collect", fail)

    assert collector.main(["skincare"]) == 1
    assert "Search failed: Meta returned no image ads" in capsys.readouterr().out


def test_collector_does_not_need_the_ai_libraries():
    code = "import sys, collector; print('torch' in sys.modules, 'clip' in sys.modules, 'cv2' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False False False"


# --- stop, preview page --------------------------------------------------------

def test_stop_during_meta_search_sends_nothing(service):
    with pytest.raises(collector.CollectorError, match="Stopped"):
        collector.collect("skincare", "IN", service, on_status=lambda _m: None,
                          search=FakeSearch([record(0)]), stop_check=lambda: True)
    assert service.packets == []


def test_stop_between_packets_stops_sending(service):
    sent = []
    records = [record(n, "blue" if n % 2 else "green") for n in range(120)]

    with pytest.raises(collector.CollectorError, match="Stopped"):
        collector.collect("skincare", "IN", service, on_status=sent.append,
                          search=FakeSearch(records), stop_check=lambda: len(service.packets) >= 1)

    assert service.packets == [50]
    assert not any("Removing duplicates" in s for s in sent)


def test_preview_shows_only_design_led_best_first(service):
    records = [record(0, "red"), record(1, "blue"), record(2, "green"), record(3, "red")]
    final = collector.collect("skin <care>", "IN", service, on_status=lambda _m: None, search=FakeSearch(records))

    page = collector.preview_html(final)

    # red (0.9) before blue (0.7); green (0.2) and the red copy are left out
    assert page.index("red.jpg") < page.index("blue.jpg")
    assert "green.jpg" not in page and page.count("red.jpg") == 2  # link + image of one card
    assert "1 copies hidden" in page
    assert "skin &lt;care&gt;" in page and "<care>" not in page


def test_save_results_writes_json_and_preview(service, tmp_path):
    final = collector.collect("skincare", "IN", service, on_status=lambda _m: None, search=FakeSearch([record(0)]))

    json_path, preview_path = collector.save_results(final, tmp_path)

    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"]["design_led"] == 1
    assert "red.jpg" in preview_path.read_text(encoding="utf-8")
