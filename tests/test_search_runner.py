"""The background search behind the gallery page's search bar."""
import time

import pytest

import collector
import search_runner

FINAL = {"summary": {"received": 3, "unique": 2, "duplicates": 1, "design_led": 1, "failed": 0},
         "warning": "", "results": [], "category": "skincare", "country": "IN", "library": {"new": 2, "seen_again": 0}}


def wait_until(check, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("condition never became true")


def finished(search):
    return wait_until(lambda: (s := search.snapshot())["state"] != "running" and s)


def runner_with(collect, **kwargs):
    return search_runner.SearchRunner(service="svc", collect=collect, **kwargs)


def test_a_search_reports_progress_then_its_results():
    def fake_collect(category, country, service, on_status, stop_check):
        on_status(f"searching {category} {country}")
        return FINAL

    search = search_runner.LocalSearch(runner_with(fake_collect))
    search.start("skincare", "IN")
    s = finished(search)

    assert s["state"] == "done" and s["lines"] == ["searching skincare IN"]
    assert s["summary"]["design_led"] == 1 and s["library"] == {"new": 2, "seen_again": 0}
    assert s["finished_at"] >= s["started_at"]


@pytest.mark.parametrize("error, message", [
    (collector.CollectorError("Meta is rate-limiting searches from this network."),
     "Meta is rate-limiting searches from this network."),
    (ValueError("boom"), "Something went wrong: ValueError: boom"),
])
def test_a_failed_search_says_why_and_is_not_retried(error, message):
    calls = []

    def fake_collect(*args, **kwargs):
        calls.append(1)
        raise error

    search = search_runner.LocalSearch(runner_with(fake_collect))
    search.start("skincare", "IN")
    s = finished(search)

    assert (s["state"], s["error"]) == ("failed", message)
    time.sleep(0.05)
    assert calls == [1]


def test_stop_ends_the_search_as_stopped_not_failed():
    def fake_collect(category, country, service, on_status, stop_check):
        wait_until(stop_check)
        raise collector.CollectorError(collector.STOPPED_MESSAGE)

    search = search_runner.LocalSearch(runner_with(fake_collect))
    search.start("skincare", "IN")
    search.stop()
    s = finished(search)

    assert s["state"] == "stopped" and s["error"] == ""
    assert "Stopping after the current step..." in s["lines"]


def test_only_one_search_at_a_time():
    def fake_collect(category, country, service, on_status, stop_check):
        wait_until(stop_check)
        return FINAL

    search = search_runner.LocalSearch(runner_with(fake_collect))
    search.start("skincare", "IN")
    with pytest.raises(search_runner.SearchBusy, match="skincare"):
        search.start("food", "IN")
    search.stop()
    finished(search)
    search.start("food", "IN")  # fine once the first has ended
    search.stop()


def test_the_service_is_created_on_the_first_search_only():
    made = []
    runner = search_runner.SearchRunner(collect=lambda *a, **k: FINAL, make_service=lambda: made.append(1) or "svc")
    search = search_runner.LocalSearch(runner)

    search.start("skincare", "IN")
    assert finished(search)["lines"] == ["Getting ready..."]
    search.start("food", "IN")
    finished(search)

    assert made == [1]


def test_status_keeps_only_the_latest_lines():
    def fake_collect(category, country, service, on_status, stop_check):
        for n in range(100):
            on_status(f"line {n}")
        return FINAL

    search = search_runner.LocalSearch(runner_with(fake_collect))
    search.start("skincare", "IN")
    lines = finished(search)["lines"]

    assert len(lines) == search_runner.STATUS_LINES_KEPT and lines[-1] == "line 99"
