"""Tiny test service: does Meta's Ad Library answer searches from Render?

Deploy on Render's free plan (see render.yaml), open the page and press the
button. It runs ONE small search (one page, up to 30 ads, no AI) from Render's
servers and shows how many ads came back. In October 2026 every search from
Render was refused; this checks whether that is still true.

Only one search per 5 minutes, so nobody can hammer Meta through the link.
"""
import html
import sys
import threading
import time
from pathlib import Path

from flask import Flask, request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root: meta_collection, meta_ads_collector
from meta_collection import classify_failure, search_ads_broad  # noqa: E402

app = Flask(__name__)
COOLDOWN_SECONDS = 300
_lock = threading.Lock()
_last = {"at": 0.0, "result": None}

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Render test</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#1b1b1b}}
button{{font:inherit;padding:10px 18px;border-radius:8px;border:0;background:#1b1b1b;color:#fff;cursor:pointer}}
.box{{margin-top:20px;padding:16px;border-radius:10px;background:#f2f2ee}}.ok{{color:#0a6b3d}}.bad{{color:#a3222f}}</style></head>
<body><h1>Does Meta answer searches from Render?</h1>
<p>Press the button to run one small search (about 30 ads, no AI) from this server. It takes up to a minute.</p>
<form method="post" action="/test"><input type="hidden" name="q" value="skincare"><button type="submit">Run the test</button></form>
{result}</body></html>"""


def render_result(result):
    if not result:
        return ""
    when = time.strftime("%d %b %Y, %H:%M UTC", time.gmtime(result["at"]))
    if result["ok"]:
        line = (f'<p class="ok"><strong>Meta answered.</strong> {result["ads"]} ads came back, '
                f'{result["images"]} of them image ads.</p>')
        if not result["ads"]:
            line = ('<p class="bad"><strong>0 ads came back.</strong> The search ran but Meta sent '
                    'nothing, which is how a quiet block looks.</p>')
    else:
        line = (f'<p class="bad"><strong>Meta refused or failed</strong> ({html.escape(result["kind"])}).</p>'
                f'<p><small>{html.escape(result["error"])}</small></p>')
    return f'<div class="box">{line}<p><small>Search: "{html.escape(result["query"])}" · {when} · took {result["seconds"]} s</small></p></div>'


@app.get("/")
def index():
    return PAGE.format(result=render_result(_last["result"]))


@app.post("/test")
def test():
    with _lock:
        wait = COOLDOWN_SECONDS - (time.time() - _last["at"])
        if _last["result"] and wait > 0:
            note = f'<div class="box"><p>Please wait {int(wait // 60) + 1} more minute(s) before testing again.</p></div>'
            return PAGE.format(result=note + render_result(_last["result"]))
        query = (request.form.get("q") or "skincare").strip()[:40]
        started = time.time()
        ads = {"n": 0}
        try:
            records = search_ads_broad(query=query, country="IN", status="ACTIVE", max_results=30, page_size=30,
                                       static_only=True, on_collect=lambda n: ads.__setitem__("n", n))
            result = {"ok": True, "ads": ads["n"], "images": len(records)}
        except Exception as exc:  # the point of the test is to see this
            result = {"ok": False, "kind": classify_failure(exc), "error": str(exc)[:300]}
        result.update(query=query, at=started, seconds=round(time.time() - started))
        print(f"[RENDER TEST] {result}", flush=True)
        _last.update(at=started, result=result)
    return PAGE.format(result=render_result(result))
