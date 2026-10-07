"""Tests for the collector window's background search runner and window."""
import time
from pathlib import Path

import pytest

import collector
import collector_app


def wait_for_event(runner, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not runner.events.empty():
            return runner.events.get()
        time.sleep(0.01)
    raise AssertionError("no event from the runner")


def events_until_end(runner):
    events = []
    while True:
        event = wait_for_event(runner)
        events.append(event)
        if event[0] in ("done", "failed"):
            return events


FINAL = {"summary": {"received": 3, "unique": 2, "duplicates": 1, "design_led": 1, "failed": 0},
         "warning": "", "results": [], "category": "skincare", "country": "IN"}


def test_runner_reports_progress_then_done():
    def fake_collect(category, country, service, on_status, stop_check):
        on_status(f"searching {category} {country}")
        return FINAL

    runner = collector_app.SearchRunner("svc", collect=fake_collect, save=lambda final: ("a.json", Path("a.html")))
    runner.start("skincare", "IN")

    assert events_until_end(runner) == [("status", "searching skincare IN"), ("done", FINAL, Path("a.html"))]


@pytest.mark.parametrize("error, message", [
    (collector.CollectorError("Meta is rate-limiting"), "Meta is rate-limiting"),
    (ValueError("boom"), "Something went wrong: ValueError: boom"),
])
def test_runner_reports_failures(error, message):
    def fake_collect(*args, **kwargs):
        raise error

    runner = collector_app.SearchRunner("svc", collect=fake_collect)
    runner.start("skincare", "IN")

    assert events_until_end(runner) == [("failed", message)]


def test_runner_stop_reaches_the_search():
    def fake_collect(category, country, service, on_status, stop_check):
        deadline = time.time() + 5
        while not stop_check() and time.time() < deadline:
            time.sleep(0.01)
        raise collector.CollectorError(collector.STOPPED_MESSAGE)

    runner = collector_app.SearchRunner("svc", collect=fake_collect)
    runner.start("skincare", "IN")
    assert runner.running
    runner.stop()

    assert events_until_end(runner) == [("failed", collector.STOPPED_MESSAGE)]


@pytest.fixture(scope="module")
def tk_root():
    """One Tk root for the module. Creating many in one process intermittently
    fails on this Windows Python ("Can't find a usable init.tcl"); the app
    itself only ever creates one."""
    tk = pytest.importorskip("tkinter")
    error = None
    for _attempt in range(3):
        try:
            root = tk.Tk()
            break
        except tk.TclError as exc:
            error = exc
    else:
        pytest.skip(f"no display available: {error}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def window(tk_root):
    import tkinter as tk
    top = tk.Toplevel(tk_root)
    top.withdraw()

    class FakeRunner:
        def __init__(self):
            self.started, self.stopped, self.running = [], False, False
            self.events = collector_app.queue.Queue()

        def start(self, category, country):
            self.started.append((category, country))

        def stop(self):
            self.stopped = True

    win = collector_app.CollectorWindow(top, FakeRunner())
    yield win
    top.destroy()


def log_text(win):
    return win.log.get("1.0", "end")


def test_window_needs_a_category(window):
    window.start()
    assert window.runner.started == []
    assert "Type what to search for first." in log_text(window)


def test_window_full_search_cycle(window):
    window.category.insert(0, "  skincare ")
    window.country.delete(0, "end")
    window.country.insert(0, "in")

    window.start()
    assert window.runner.started == [("skincare", "IN")]
    assert str(window.collect_button["state"]) == "disabled"
    assert str(window.stop_button["state"]) == "normal"

    window.handle(("status", "Collected 3 image ads."))
    window.handle(("done", FINAL, Path("x.html")))

    text = log_text(window)
    assert "Collected 3 image ads." in text
    assert "3 images collected, 2 unique after removing duplicates, 1 design-led" in text
    assert str(window.open_button["state"]) == "normal"
    assert str(window.collect_button["state"]) == "normal"


def test_window_failure_lets_you_search_again(window):
    window.category.insert(0, "skincare")
    window.start()
    window.handle(("failed", "Meta is rate-limiting searches from this network."))

    assert "Meta is rate-limiting" in log_text(window)
    assert str(window.collect_button["state"]) == "normal"
    assert str(window.open_button["state"]) == "disabled"


def test_window_stop_button(window):
    window.category.insert(0, "skincare")
    window.start()
    window.stop()

    assert window.runner.stopped
    assert str(window.stop_button["state"]) == "disabled"


def test_runner_creates_the_service_on_the_first_search_only():
    made = []

    def fake_collect(category, country, service, on_status, stop_check):
        return FINAL

    runner = collector_app.SearchRunner(collect=fake_collect, save=lambda final: ("a.json", Path("a.html")),
                                        make_service=lambda: made.append(1) or "svc")
    runner.start("skincare", "IN")
    assert events_until_end(runner)[0] == ("status", "Getting ready...")
    runner.start("skincare", "IN")
    events_until_end(runner)

    assert made == [1] and runner.service == "svc"
