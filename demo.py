"""Creative Lens on one laptop: the Collector window plus the team gallery.

Double-click the "Creative Lens" shortcut (or run `.venv\\Scripts\\pythonw.exe
demo.py`). The gallery opens in the browser; searches from the window are
scored here and saved to the local library (library.db + thumbnails/), and
"Open results" jumps to that category in the gallery. Output goes to
demo.log, since there's no console window.
"""
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREFERRED_PORT = 8050  # not 8000, which a separately started analysis_api may be using


def free_port(preferred=PREFERRED_PORT, tries=20):
    for port in range(preferred, preferred + tries):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port between {preferred} and {preferred + tries - 1}.")


def serve_gallery(port, ready):
    import analysis_api  # heavy (torch, CLIP), so loaded off the window's thread
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", port, analysis_api.app, threaded=True)
    ready.set()
    server.serve_forever()


def main():
    if sys.stdout is None or sys.stderr is None:  # pythonw has no console; keep a log instead
        log = open(ROOT / "demo.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    os.chdir(ROOT)
    import collector
    import collector_app
    collector.load_settings()
    os.environ["CREATIVE_LENS_LIBRARY"] = "local"  # this demo keeps everything on the laptop
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    os.environ["CREATIVE_LENS_GALLERY_URL"] = url
    ready = threading.Event()
    threading.Thread(target=serve_gallery, args=(port, ready), daemon=True).start()
    threading.Thread(target=lambda: ready.wait(180) and webbrowser.open(url), daemon=True).start()
    print(f"[DEMO] Gallery at {url}")
    collector_app.main()


if __name__ == "__main__":
    main()
