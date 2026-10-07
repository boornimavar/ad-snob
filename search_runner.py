"""Running a search in the background for the gallery page's search bar.

SearchRunner runs one collector.collect() on a thread and reports through a
queue; LocalSearch keeps the latest search's state for the page to poll.
A failed search is never restarted automatically (it makes Meta's rate
limiting worse); the person starts it again when they choose.
"""
import queue
import threading
import time

import collector

STATUS_LINES_KEPT = 40


class SearchRunner:
    """Runs one search on a background thread and reports through a queue.

    Events: ("status", text), ("done", final) or ("failed", text).
    """

    def __init__(self, service=None, collect=collector.collect, make_service=collector.default_service):
        self.service = service  # created on the first search if not given (loads the AI libraries)
        self._make_service = make_service
        self.events = queue.Queue()
        self._collect = collect
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, category, country):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(category, country), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self, category, country):
        try:
            if self.service is None:
                self.events.put(("status", "Getting ready..."))
                self.service = self._make_service()
            final = self._collect(category, country, self.service,
                                  on_status=lambda text: self.events.put(("status", text)),
                                  stop_check=self._stop.is_set)
            self.events.put(("done", final))
        except collector.CollectorError as exc:
            self.events.put(("failed", str(exc)))
        except Exception as exc:  # anything unexpected still ends the search cleanly
            self.events.put(("failed", f"Something went wrong: {type(exc).__name__}: {exc}"))


class SearchBusy(Exception):
    pass


class LocalSearch:
    """The latest search's state, as the gallery's search bar shows it."""

    def __init__(self, runner):
        self.runner = runner
        self._lock = threading.Lock()
        self._state = {"state": "idle", "category": "", "country": "", "lines": [],
                       "started_at": None, "finished_at": None, "summary": None,
                       "library": None, "warning": "", "error": ""}

    def start(self, category, country):
        with self._lock:
            self._drain_locked()
            if self.runner.running or self._state["state"] == "running":
                raise SearchBusy(f"A search for '{self._state['category']}' is still running.")
            self._state.update(state="running", category=category, country=country, lines=[],
                               started_at=time.time(), finished_at=None, summary=None, library=None,
                               warning="", error="")
            self.runner.start(category, country)

    def stop(self):
        with self._lock:
            if self._state["state"] == "running":
                self.runner.stop()
                self._add_line_locked("Stopping after the current step...")

    def snapshot(self):
        with self._lock:
            self._drain_locked()
            return {**self._state, "lines": list(self._state["lines"])}

    def _add_line_locked(self, text):
        lines = self._state["lines"]
        lines.append(text)
        del lines[:-STATUS_LINES_KEPT]

    def _drain_locked(self):
        while True:
            try:
                event = self.runner.events.get_nowait()
            except queue.Empty:
                return
            kind = event[0]
            if kind == "status":
                self._add_line_locked(event[1])
            elif kind == "done":
                final = event[1]
                self._state.update(state="done", finished_at=time.time(), summary=final.get("summary"),
                                   library=final.get("library"), warning=final.get("warning", ""),
                                   error=final.get("library_error", ""))
            elif kind == "failed":
                stopped = event[1] == collector.STOPPED_MESSAGE
                self._state.update(state="stopped" if stopped else "failed", finished_at=time.time(),
                                   error="" if stopped else event[1])
