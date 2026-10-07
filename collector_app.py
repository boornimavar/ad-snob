"""Creative Lens collector: a small window around collector.py.

Type what to search for, click Collect, watch progress, then open the
results. Run with:  python collector_app.py
"""
import os
import queue
import threading
import tkinter as tk
import webbrowser
from urllib.parse import urlencode
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

import collector


class SearchRunner:
    """Runs one search on a background thread and reports through a queue.

    Events: ("status", text), ("done", final, preview_path) or ("failed", text).
    Kept free of tkinter so it can be tested without a window.
    """

    def __init__(self, service=None, collect=collector.collect, save=collector.save_results,
                 make_service=collector.default_service):
        self.service = service  # created on the first search if not given, off the window's thread
        self._make_service = make_service
        self.events = queue.Queue()
        self._collect = collect
        self._save = save
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
            _json_path, preview_path = self._save(final)
            self.events.put(("done", final, preview_path))
        except collector.CollectorError as exc:
            self.events.put(("failed", str(exc)))
        except Exception as exc:  # anything unexpected still ends the search cleanly
            self.events.put(("failed", f"Something went wrong: {type(exc).__name__}: {exc}"))


class CollectorWindow:
    POLL_MS = 150

    def __init__(self, root, runner):
        self.root = root
        self.runner = runner
        self.preview_path = None
        self.last_search = None

        root.title("Creative Lens Collector")
        root.minsize(560, 420)
        frame = ttk.Frame(root, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="What to search for").grid(row=0, column=0, sticky="w")
        self.category = ttk.Entry(frame)
        self.category.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0))
        self.category.bind("<Return>", lambda _e: self.start())
        self.category.focus()

        ttk.Label(frame, text="Country").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.country = ttk.Entry(frame, width=6)
        self.country.insert(0, "IN")
        self.country.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=3, sticky="w", pady=12)
        self.collect_button = ttk.Button(buttons, text="Collect", command=self.start)
        self.collect_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(buttons, text="Open results", command=self.open_results, state="disabled")
        self.open_button.pack(side="left")

        self.log = ScrolledText(frame, height=14, wrap="word", state="disabled")
        self.log.grid(row=3, column=0, columnspan=3, sticky="nsew")
        frame.rowconfigure(3, weight=1)

        self.write("Type a category (for example: skincare) and click Collect.\n"
                   "A search takes a few minutes. Don't run the same search again if it fails - "
                   "wait first, or Meta limits this network for longer.")
        root.after(self.POLL_MS, self.poll)

    def write(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_running(self, running):
        self.collect_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.category.configure(state="disabled" if running else "normal")
        self.country.configure(state="disabled" if running else "normal")

    def start(self):
        if self.runner.running:
            return
        category = self.category.get().strip()
        country = self.country.get().strip().upper() or "IN"
        if not category:
            self.write("\nType what to search for first.")
            return
        self.preview_path = None
        self.open_button.configure(state="disabled")
        self.set_running(True)
        self.write(f"\n--- {category} ({country}) ---")
        self.runner.start(category, country)

    def stop(self):
        self.runner.stop()
        self.stop_button.configure(state="disabled")
        self.write("Stopping after the current step...")

    def poll(self):
        try:
            while True:
                self.handle(self.runner.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(self.POLL_MS, self.poll)

    def handle(self, event):
        kind = event[0]
        if kind == "status":
            self.write(event[1])
        elif kind == "failed":
            self.write(f"\n{event[1]}")
            self.set_running(False)
        elif kind == "done":
            final, self.preview_path = event[1], event[2]
            self.last_search = (final.get("category", ""), final.get("country", ""))
            s = final["summary"]
            if final.get("warning"):
                self.write(f"\nNote: {final['warning']}")
            self.write(f"\nDone. {s['received']} images collected, {s['unique']} unique after removing "
                       f"duplicates, {s['design_led']} design-led (score 0.5+).")
            if final.get("library"):
                self.write(f"Team library: {final['library']['new']} new ads added, "
                           f"{final['library']['seen_again']} were already there (marked as still active).")
            if final.get("library_error"):
                self.write(f"Team library: NOT saved. {final['library_error']} "
                           "Your results are still saved on this laptop.")
            self.write("Click Open results to see them.")
            self.open_button.configure(state="normal")
            self.set_running(False)

    def open_results(self):
        """The gallery at this search's category when one is running
        (CREATIVE_LENS_GALLERY_URL, set by demo.py), else the saved preview page."""
        gallery = os.environ.get("CREATIVE_LENS_GALLERY_URL")
        if gallery and self.last_search:
            category, country = self.last_search
            webbrowser.open(f"{gallery}#{urlencode({'category': category, 'country': country})}")
        elif self.preview_path:
            webbrowser.open(self.preview_path.resolve().as_uri())


def main():
    collector.load_settings()
    root = tk.Tk()
    CollectorWindow(root, SearchRunner())
    root.mainloop()


if __name__ == "__main__":
    main()
