from types import SimpleNamespace

import meta_collection
import meta_design_scraper_session as scraper


def fake_ad(i):
    return {
        "id": str(i),
        "page": {"name": f"Brand {i}", "id": f"p{i}"},
        "creatives": [{"image_url": f"https://example.com/{i}.jpg", "body": "copy"}],
    }


class FakeCollector:
    """Stands in for MetaAdsCollector; never touches the network.

    With `fail_with`, the search raises that error after yielding `ads`,
    like the real collector does when Meta rate-limits partway through.
    """

    def __init__(self, ads, fail_with=None):
        self.ads = ads
        self.fail_with = fail_with
        self.client = None  # wrapped by MediaTypeClient, never called here

    def _delay(self):
        pass

    def search(self, **kwargs):
        yield from self.ads
        if self.fail_with:
            raise self.fail_with


def use_fake_collector(monkeypatch, ads, fail_with=None):
    monkeypatch.setattr(meta_collection, "MetaAdsCollector", lambda: FakeCollector(ads, fail_with))


def test_rate_limit_partway_keeps_collected_ads(monkeypatch):
    error = RuntimeError("Max retries exceeded due to rate limiting")
    use_fake_collector(monkeypatch, [fake_ad(i) for i in range(3)], fail_with=error)
    interrupted = []

    records = scraper.search_ads_broad(query="beauty", static_only=True, on_interrupted=interrupted.append)

    assert len(records) == 3
    assert interrupted == [error]


def test_error_before_any_ad_is_raised(monkeypatch):
    use_fake_collector(monkeypatch, [], fail_with=RuntimeError("Max retries exceeded due to rate limiting"))

    try:
        scraper.search_ads_broad(query="beauty", static_only=True)
    except RuntimeError as exc:
        assert "rate limiting" in str(exc)
    else:
        raise AssertionError("expected the collector error to be raised")


def test_on_collect_reports_each_ad(monkeypatch):
    use_fake_collector(monkeypatch, [fake_ad(i) for i in range(3)])
    seen = []

    records = scraper.search_ads_broad(query="beauty", static_only=True, on_collect=seen.append)

    assert seen == [1, 2, 3]
    assert [r["ad_id"] for r in records] == ["0", "1", "2"]


def test_stop_check_ends_collection_early_and_keeps_partial_results(monkeypatch):
    use_fake_collector(monkeypatch, [fake_ad(i) for i in range(10)])
    seen = []

    records = scraper.search_ads_broad(
        query="beauty", static_only=True, on_collect=seen.append, stop_check=lambda: len(seen) >= 4,
    )

    assert seen == [1, 2, 3, 4]
    assert len(records) == 4


def test_pipeline_skips_analysis_when_stopped_during_collection(monkeypatch, tmp_path):
    use_fake_collector(monkeypatch, [fake_ad(i) for i in range(5)])
    monkeypatch.setattr(scraper, "load_models", lambda path: (_ for _ in ()).throw(AssertionError("analysis should not start")))
    stages = []
    args = SimpleNamespace(
        search_query="beauty", search_country="IN", search_status="ACTIVE", search_type="KEYWORD_UNORDERED",
        search_max_results=500, search_page_size=10, search_media_type="IMAGE", platforms=None,
        search_start_date=None, search_end_date=None, model="unused", search_sort_by=None,
    )

    scraper.run_broad_search_pipeline(
        args,
        progress_callback=lambda done, total, stage: stages.append((stage, done)),
        stop_check=lambda: len(stages) >= 3,
        db_path=tmp_path / "session.db",
        save_run_artifacts=False,
    )

    assert stages[0] == ("collecting", 0)
    assert stages[-1][0] == "stopped"


# --- collector 1.4.0 gaps: media type and empty pages (2026-10-08) ------------

class FakeMetaClient:
    """Stands in for MetaAdsClient. `pages` maps a cursor (None = first page) to
    (ads on that page, next cursor); records every request it gets."""

    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def search_ads(self, **kwargs):
        self.requests.append(kwargs)
        ads, next_cursor = self.pages[kwargs.get("cursor")]
        return {"ads": ads}, next_cursor


def image_ad(n):
    return {"ad_archive_id": str(n), "page_name": f"Brand {n}", "page_id": f"p{n}",
            "cards": [{"original_image_url": f"https://img.example/{n}.jpg", "body": {"text": f"copy {n}"}}]}


def video_ad(n):
    return {"ad_archive_id": str(n), "page_name": f"Brand {n}", "page_id": f"p{n}",
            "cards": [{"video_hd_url": f"https://vid.example/{n}.mp4",
                       "video_preview_image_url": f"https://img.example/{n}.jpg"}]}


def test_every_request_asks_meta_for_this_media_type():
    client = FakeMetaClient({None: ([image_ad(1)], None)})
    paging = meta_collection.MediaTypeClient(client, "IMAGE")

    paging.search_ads(query="beauty", media_type="ALL", first=30)

    assert client.requests == [{"query": "beauty", "media_type": "IMAGE", "first": 30}]


def test_empty_pages_are_stepped_past_while_meta_offers_more():
    client = FakeMetaClient({None: ([], "c1"), "c1": ([], "c2"), "c2": ([image_ad(1)], "c3")})
    pauses = []
    paging = meta_collection.MediaTypeClient(client, "IMAGE", between_pages=lambda: pauses.append(1))

    response, next_cursor = paging.search_ads(cursor=None)

    assert [a["ad_archive_id"] for a in response["ads"]] == ["1"] and next_cursor == "c3"
    assert [r["cursor"] for r in client.requests] == [None, "c1", "c2"]
    assert paging.empty_pages_skipped == 2 and len(pauses) == 2


def test_too_many_empty_pages_ends_the_search():
    pages = {None: ([], "c1")}
    pages.update({f"c{n}": ([], f"c{n + 1}") for n in range(1, 20)})
    client = FakeMetaClient(pages)
    paging = meta_collection.MediaTypeClient(client, "IMAGE", max_empty_pages=3)

    response, next_cursor = paging.search_ads(cursor=None)

    assert response["ads"] == [] and next_cursor is None
    assert len(client.requests) == 4  # the first empty page + 3 more


def test_an_empty_last_page_ends_normally():
    client = FakeMetaClient({None: ([], None)})
    response, next_cursor = meta_collection.MediaTypeClient(client, "IMAGE").search_ads(cursor=None)
    assert response["ads"] == [] and next_cursor is None and len(client.requests) == 1


def test_rate_limit_answers_go_straight_back_to_the_collector():
    class LimitedClient:
        calls = 0

        def search_ads(self, **kwargs):
            self.calls += 1
            return {"rate_limited": True, "ads": []}, "c1"

    client = LimitedClient()
    response, next_cursor = meta_collection.MediaTypeClient(client, "IMAGE").search_ads(cursor=None)

    assert response["rate_limited"] and next_cursor == "c1" and client.calls == 1


def run_real_collector(monkeypatch, pages, **search_kwargs):
    """search_ads_broad() with the real collector 1.4.0 search loop, Meta faked."""
    from meta_ads_collector import MetaAdsCollector
    client = FakeMetaClient(pages)

    def make_collector():
        collector = MetaAdsCollector(rate_limit_delay=0, jitter=0)
        collector.client = client
        return collector

    monkeypatch.setattr(meta_collection, "MetaAdsCollector", make_collector)
    records = meta_collection.search_ads_broad(query="beauty", static_only=True, **search_kwargs)
    return records, client


def test_real_collector_keeps_going_past_empty_pages(monkeypatch):
    # Before the fix the real loop stopped at page 2 (empty) with only ads 1-2.
    pages = {None: ([image_ad(1), image_ad(2)], "c1"), "c1": ([], "c2"), "c2": ([], "c3"),
             "c3": ([image_ad(3), image_ad(4)], None)}

    records, client = run_real_collector(monkeypatch, pages)

    assert [r["ad_id"] for r in records] == ["1", "2", "3", "4"]
    assert {r["media_type"] for r in client.requests} == {"IMAGE"}


def test_real_collector_still_drops_videos_that_slip_through(monkeypatch):
    pages = {None: ([image_ad(1), video_ad(2), image_ad(3)], None)}

    records, _client = run_real_collector(monkeypatch, pages)

    assert [r["ad_id"] for r in records] == ["1", "3"]


def test_real_collector_still_stops_at_the_500_ad_limit(monkeypatch):
    pages = {None: ([image_ad(n) for n in range(30)], "c1")}
    pages.update({f"c{p}": ([image_ad(p * 100 + n) for n in range(30)], f"c{p + 1}") for p in range(1, 30)})

    records, client = run_real_collector(monkeypatch, pages, max_results=500, page_size=30)

    assert len(records) == 500
    assert len(client.requests) == 17  # 17 pages of 30 reach 500; no extra requests


def test_every_page_and_the_reason_it_ended_are_logged(capsys):
    pages = {None: ([image_ad(1)], "c1"), "c1": ([], "c2"), "c2": ([], "c3"), "c3": ([], "c4")}
    paging = meta_collection.MediaTypeClient(FakeMetaClient(pages), "IMAGE", max_empty_pages=2)

    paging.search_ads(cursor=None)
    paging.search_ads(cursor="c1")

    lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith("[PAGES]")]
    assert lines == [
        "[PAGES] page 1: 1 ads",
        "[PAGES] page 2: empty (1 in a row), Meta says there's more - continuing",
        "[PAGES] page 3: empty (2 in a row), Meta says there's more - continuing",
        "[PAGES] page 4: empty (3 in a row) - giving up",
    ]
    assert paging.pages_fetched == 4 and paging.empty_pages_skipped == 2
    assert paging.ended_because == "3 empty pages in a row (limit 2)"


def test_last_page_reason_is_recorded():
    paging = meta_collection.MediaTypeClient(FakeMetaClient({None: ([image_ad(1)], None)}), "IMAGE")
    paging.search_ads(cursor=None)
    assert paging.ended_because == "Meta said there were no more pages"


def test_ten_empty_pages_in_a_row_are_allowed_by_default():
    pages = {None: ([], "c1")}
    pages.update({f"c{n}": ([], f"c{n + 1}") for n in range(1, 10)})
    pages["c10"] = ([image_ad(1)], None)
    response, _ = meta_collection.MediaTypeClient(FakeMetaClient(pages), "IMAGE").search_ads(cursor=None)
    assert [a["ad_archive_id"] for a in response["ads"]] == ["1"]
