"""Creative Lens on one laptop: the gallery page with its own search bar.

Double-click the "Creative Lens" shortcut (or run `.venv\\Scripts\\pythonw.exe
demo.py`). The app starts in the background and opens the page in the
browser; searches typed there are scored on this laptop and saved to the
local library (library.db + thumbnails/) for a day. The app stops by itself
IDLE_MINUTES after the page was last open; double-clicking the shortcut while
it runs just opens the page again. Output goes to demo.log (no console).
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREFERRED_PORT = 8050  # not 8000, which a separately started analysis_api may be using
IDLE_MINUTES = 15


def running_app_url(port=PREFERRED_PORT):
    """The address of a Creative Lens already running on this laptop, or None."""
    url = f"http://127.0.0.1:{port}/"
    try:
        with urllib.request.urlopen(url + "health", timeout=2) as res:
            return url if json.load(res).get("app") == "creative-lens" else None
    except Exception:
        return None


def free_port(preferred=PREFERRED_PORT, tries=20):
    for port in range(preferred, preferred + tries):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port between {preferred} and {preferred + tries - 1}.")


def idle_for(api, now=None):
    """Seconds since the page last checked in, or 0 while a search is running."""
    search = api._search
    if search is not None and search.runner.running:
        return 0
    return (now or time.time()) - api.last_page_contact


def stop_when_idle(api, idle_seconds, check_every=60):
    while True:
        time.sleep(check_every)
        if idle_for(api) > idle_seconds:
            print("[DEMO] The page has been closed for a while; stopping.")
            os._exit(0)


def main():
    if sys.stdout is None or sys.stderr is None:  # pythonw has no console; keep a log instead
        log = open(ROOT / "demo.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    os.chdir(ROOT)
    existing = running_app_url()
    if existing:
        webbrowser.open(existing)
        return

    import collector
    collector.load_settings()
    os.environ["CREATIVE_LENS_LIBRARY"] = "local"  # everything stays on this laptop
    os.environ["CREATIVE_LENS_LOCAL_SEARCH"] = "1"  # the page's search bar
    import analysis_api  # loads the AI libraries: the slow part of starting
    from werkzeug.serving import make_server

    removed = analysis_api.get_library().remove_expired()  # results last a day
    print(f"[DEMO] Removed {removed} ads older than a day")
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    server = make_server("127.0.0.1", port, analysis_api.app, threaded=True)
    analysis_api.last_page_contact = time.time()
    threading.Thread(target=stop_when_idle, args=(analysis_api, IDLE_MINUTES * 60), daemon=True).start()
    webbrowser.open(url)
    print(f"[DEMO] Creative Lens at {url}")
    server.serve_forever()


if __name__ == "__main__":
    main()
