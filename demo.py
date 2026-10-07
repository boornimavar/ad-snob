"""Creative Lens on one laptop: the Creative Lens dashboard, opened in the browser.

Double-click the "Creative Lens" shortcut (or run `.venv\\Scripts\\pythonw.exe
demo.py`). The app (dashboard_server_temporary) starts in the background and
opens the dashboard; searches run and are scored on this laptop, and results
last a day. The app stops by itself IDLE_MINUTES after the page was last
open; double-clicking the shortcut while it runs just opens the page again.
Output goes to demo.log (no console).
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
PREFERRED_PORT = 8050  # not 8000/5000, which a separately started server may be using
IDLE_MINUTES = 15


def running_app_url(port=PREFERRED_PORT):
    """The address of a Creative Lens already running on this laptop, or None."""
    url = f"http://127.0.0.1:{port}/"
    try:
        with urllib.request.urlopen(url + "api/health", timeout=2) as res:
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


def idle_for(server, now=None):
    """Seconds since the page last checked in, or 0 while a search is running."""
    if server._scrapes_in_progress:
        return 0
    return (now or time.time()) - server.last_page_contact


def stop_when_idle(server, idle_seconds, check_every=60):
    while True:
        time.sleep(check_every)
        if idle_for(server) > idle_seconds:
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

    import dashboard_server_temporary as server  # loads the AI libraries: the slow part of starting
    from werkzeug.serving import make_server

    server.prepare_session_db()  # results last a day
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    http = make_server("127.0.0.1", port, server.app, threaded=True)
    server.last_page_contact = time.time()
    threading.Thread(target=stop_when_idle, args=(server, IDLE_MINUTES * 60), daemon=True).start()
    webbrowser.open(url)
    print(f"[DEMO] Creative Lens at {url}")
    http.serve_forever()


if __name__ == "__main__":
    main()
