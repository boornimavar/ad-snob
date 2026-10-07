"""Creative Lens on one laptop: the Creative Lens dashboard, opened in the browser.

Double-click the "Creative Lens" shortcut (or run `.venv\\Scripts\\pythonw.exe
demo.py`). The app (dashboard_server_temporary) starts in the background and
opens the dashboard; searches run and are scored on this laptop, and results
last a day. The app stops by itself IDLE_MINUTES after the page was last
open; double-clicking the shortcut while it runs just opens the page again.
Output goes to demo.log (no console).

Packaged as an .exe (build_app.py), the AI model weights, Tesseract and the
ranking models come from inside the package, and searches, saved ads and the
log live in the user's %LOCALAPPDATA% folder, under "Creative Lens".
"""
import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREFERRED_PORT = int(os.environ.get("CREATIVE_LENS_PORT", 8050))  # not 8000/5000, used by other servers
IDLE_MINUTES = 15


def use_packaged_files(bundle, data):
    """Point the app at the files shipped inside the package (bundle) and keep
    what it writes in a per-user folder (data). Returns data."""
    data.mkdir(parents=True, exist_ok=True)
    for name in ("design_classifier.pkl", "rank_model_v1.pkl"):  # the models this version shipped with
        shutil.copy2(bundle / "models" / name, data / name)
    os.environ.setdefault("CREATIVE_LENS_DATA_DIR", str(data))
    os.environ.setdefault("CREATIVE_LENS_CLIP_DIR", str(bundle / "clip"))
    os.environ.setdefault("CREATIVE_LENS_TESSERACT", str(bundle / "tesseract" / "tesseract.exe"))
    os.environ.setdefault("TESSDATA_PREFIX", str(bundle / "tesseract" / "tessdata"))
    return data


def self_test():
    """Check the AI model, the text reader and the Meta collector library all
    load and work, without contacting Meta. Prints a report; returns 0 if OK."""
    from PIL import Image, ImageDraw, ImageFont
    problems = []
    import meta_design_scraper_session as scraper  # torch, CLIP, Tesseract, the Meta collector
    from curl_cffi.requests import Session
    Session().close()
    print(f"[SELF-TEST] Text reader: {scraper.pytesseract.pytesseract.tesseract_cmd}")
    scraper.load_models(scraper.ML_MODEL_PATH)
    print(f"[SELF-TEST] Ranking model loaded: {scraper._load_rank_model() is not None}")
    image = Image.new("RGB", (800, 800), (250, 228, 210))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 800, 200], fill=(120, 30, 60))
    draw.text((40, 50), "GLOW SERUM", font=ImageFont.load_default(size=90), fill="white")
    draw.text((40, 600), "FLAT 40% OFF TODAY", font=ImageFont.load_default(size=60), fill=(120, 30, 60))
    [result] = scraper.analyze_images_batch([image])
    scores = result[0] if result else {}
    print(f"[SELF-TEST] Words read: {scores.get('ocr_word_count')} ({scores.get('ocr_fingerprint', '')!r})")
    print(f"[SELF-TEST] Design score (CLIP): {scores.get('clip_design_score')}; rank score: {scraper.rank_score(scores)}")
    if not scores.get("ocr_word_count"):
        problems.append("the text reader found no words")
    if not isinstance(scores.get("clip_design_score"), float):
        problems.append("the AI model gave no design score")
    if scraper._load_rank_model() is None:
        problems.append("the ranking model didn't load")
    print("[SELF-TEST] " + ("PASSED" if not problems else "FAILED: " + "; ".join(problems)))
    return 0 if not problems else 1


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
    log_dir = ROOT
    if getattr(sys, "frozen", False):  # the packaged .exe
        log_dir = use_packaged_files(Path(sys._MEIPASS),
                                     Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Creative Lens")
    if sys.stdout is None or sys.stderr is None:  # no console window; keep a log instead
        log = open(log_dir / "demo.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    os.chdir(ROOT)
    if "--self-test" in sys.argv:
        sys.exit(self_test())
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
