# Creative Lens

A shared library of design-led Meta ads for the team, sorted by an AI model
that tells designed ads apart from plain product photos.

Started from the `indian-meta-ad-scraper` prototype, which runs everything
on one laptop. Meta blocks ad collection from cloud servers, so this version
splits the work:

```
Teammates' laptops              Online (free hosting)            Database
──────────────────              ─────────────────────            ────────
Collector app          ──────►  Analysis + dashboard    ◄──────► Shared library
(searches Meta on a              (design scores,                  (saved ads)
 normal connection,               duplicates, the link
 where Meta allows it)            the team opens)
```

## Teammate workflow

1. **First time:** run the Collector (planned: a single `.exe`).
2. **For fresh ads:** type a category (e.g. "skincare"), click Collect. Ads
   are sent to the team library automatically.
3. **Every day:** open the team link to browse all collected ads, sorted by
   design quality, with duplicates removed.

## Roadmap

1. **Analysis API** - send ad images, get design scores and duplicates back.
2. **Laptop collector** - searches Meta locally and sends ads to the API.
3. **Go online for free** - analysis + dashboard on Hugging Face Spaces,
   shared library in a free hosted database.
4. **Improvements** - 👍/👎 labels in the gallery to retrain the model;
   one-click `.exe` collector.

## Current state

The code is the working prototype copied from `indian-meta-ad-scraper`
(dashboard, scoring, deduplication, collector 1.4.0, tests). It still runs
as a single local app:

```bash
python dashboard_server_temporary.py
```

## Analysis API (Step 1)

```bash
python analysis_api.py
```

Starts on http://127.0.0.1:8000 (for local testing). One search is one job; its ads are sent
in packets of up to 50 (a delivery size, not a limit on the search):

```
POST /jobs                  -> {"job_id": "..."}
POST /jobs/<job_id>/ads     {"ads": [{"id": "123", "image_url": "https://...", "brand": "..."},
                                     {"id": "456", "image_base64": "<image bytes, base64>"}]}
POST /jobs/<job_id>/finish  -> final results for the whole search
```

Duplicates are found across the whole search at `finish`. Each result has
`rank_score` (higher = more design-led), the individual scores, duplicate
info (`creative_group_id`, `duplicate`, `creative_variant_of`), and any
extra fields you sent under `details`. `POST /analyze` does one packet in
one step. Set `CREATIVE_LENS_API_KEY` to require an `X-API-Key` header.
`GET /health` checks the server is up.

Start a job with `{"category": "skincare", "country": "IN"}` and finishing
it saves the search's unique ads to the shared library (with a small copy
of each picture, since Meta's links expire). Browse it with
`GET /library?category=skincare` (ads found in the last 24 hours;
`&design_led_only=false` for everything) and `GET /library/categories`.
By default the library is `library.db` and `thumbnails/` next to the code;
set `CREATIVE_LENS_DATABASE_URL` to use a hosted database.

## Using it (everything on one laptop)

Double-click the **Creative Lens** shortcut on the Desktop (it runs
`.venv\Scripts\pythonw.exe demo.py`). After a few seconds the dashboard
opens in the browser. Pick an industry or type a search and the search runs
on this laptop, with live progress; the ads appear best first. Results last
a day: anything no search has found in the last 24 hours is deleted, and a
category whose results expired is searched fresh. Saved ads are kept. The
app stops by itself 15 minutes after the page is closed. Log: `demo.log`.

(The same dashboard without the shortcut: `python dashboard_server_temporary.py`,
then http://127.0.0.1:5000. Command-line search: `python collector.py skincare`.)

## Building the Windows app

```bash
.venv\Scripts\python.exe -m pip install "pyinstaller>=6.10,<7"
.venv\Scripts\python.exe build_app.py
```

Makes `dist/Creative-Lens-Windows.zip` (about 0.7 GB): unzip, double-click
`Creative Lens.exe`, nothing else to install. `Creative Lens.exe --self-test`
checks the AI model and text reader work. To share it on Hugging Face:

```bash
.venv\Scripts\hf auth login
.venv\Scripts\python.exe publish_app.py YOUR-USERNAME/creative-lens-app
```

## Notes

- Use `meta_ads_collector` **1.4.0** from `meta_ads_collector_source.zip`.
  Do not `pip install meta-ads-collector` (1.5.x breaks searches).
- The collector reads Meta's Ad Library website, not an official API.
  Confirm with your manager that this is OK for team use.

## Tests

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests
```

(The AI model also needs `torch`, `torchvision` and OpenAI's `CLIP`; see the
Dockerfile. Install `meta_ads_collector` 1.4.0 from the zip, not from pip.)
