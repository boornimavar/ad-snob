# Creative Lens - notes for Claude

## Goal
A tool the team uses online: browse Meta ads sorted by design quality, with
live collection. The user does not want to spend money and wants no manual
data steps in the final product. Explain things plainly, one step at a time.

## Why the architecture is split
- Meta's Ad Library rate-limits/blocks requests from cloud servers (Render
  was refused on the very first request, every search). Normal office Wi-Fi
  and mobile hotspots work.
- So: **collect on teammates' laptops**, **analyze + show online**.

## Plan
1. **Analysis API** (built, local): `analysis_api.py`. One search = one job:
   `POST /jobs`, then packets of up to 50 ads (`image_url` or `image_base64`)
   to `/jobs/<id>/ads`, then `/jobs/<id>/finish` groups duplicates across the
   whole search (same as the old pipeline) and returns the same score columns
   as the `ads` table. Packets are only a delivery size, never a cap on ads.
   Dedup against the whole shared library needs the DB (step 3).
2. **Laptop collector** (in progress): `collector_app.py` (tkinter window:
   Collect / Stop / Open results) around `collector.py`, which searches Meta
   via `meta_collection.py` (no torch/CLIP), sends ads to the API as one job,
   and saves a preview page + JSON in `collector_results/`. Still to do: a
   single .exe (PyInstaller).
3. **Free hosting** (in progress): analysis + dashboard on Hugging Face Spaces
   (enough RAM for CLIP), shared library in Supabase (Postgres + storage)
   since free hosts lose files on restart. Built so far: `library.py`
   (SQLAlchemy; SQLite locally via `CREATIVE_LENS_DATABASE_URL`), saved on
   `/jobs/<id>/finish` when the job has a category; `GET /library`,
   `/library/categories`, `/thumbnails/<key>`. Decided with the user
   (2026-10-06): only unique ads are stored, one row per ad per
   category+country; an ad unseen for 14 days moves to an Archive (never
   deleted); each unique ad keeps a ~600px JPEG copy because Meta image
   links expire within days. Still to do: Supabase image store, online
   dashboard reading the library, deploy. The `.exe` waits until the
   service is online.
4. **Improvements**: 👍/👎 labels in the gallery feeding `--retrain`.

## Hard-won facts
- `meta_ads_collector` must be **1.4.0** (from `meta_ads_collector_source.zip`).
  1.5.1 raises on rate limiting and pages through empty results; that broke all
  searches on 2026-10-01. Never `pip install meta-ads-collector`.
- With 1.4.0 a rate-limited search ends quietly with 0 ads; the dashboard should
  explain that instead of just "Search complete" (not fixed yet).
- Request 30 ads per page (`SCRAPE_PAGE_SIZE`), 500 ads max per search. The user
  does not want a lower cap (it drops ads; searches can't resume). The 500
  counts Meta *ads*; each ad can hold several images. Real search: 67 ads ->
  368 images -> 142 unique -> 88 design-led. The old dashboard's "Ads
  collected" box actually counts images (~230-390 typical).
- The OCR "no text -> 0.05" veto misses photos where Tesseract reads texture
  as junk words (conf >= 30). A stricter veto (no words >= 80% conf) was
  tried on 2026-10-06: 4 of 14 ads it would bury had real text, so rejected.
  Plan is 👍/👎 retraining instead.
- Failed searches must never auto-restart (makes rate limiting worse).
- The collector reads Meta's website, not an official API: a terms-of-service
  grey area. Don't suggest proxies or other ways around Meta's blocking.

## Working environment
- Windows. Project env: `C:\creative-lens\.venv\Scripts\python.exe` (Python
  3.13, torch CPU, CLIP from git, scikit-learn 1.8.0 to match
  rank_model_v1.pkl, collector 1.4.0 unzipped from the zip into
  site-packages). `design_classifier.pkl` (sklearn 1.6.1) still warns; it is
  diagnostics only.
- Tests: `.venv\Scripts\python.exe -m pytest tests` (105 pass; they never contact Meta).
  Window tests share one Tk root: creating many in one process intermittently
  fails here with "Can't find a usable init.tcl".
- Don't run real Meta searches yourself; ask the user (it can rate-limit
  their network). The user may have their own analysis_api running on port
  8000; run test services on another port and don't stop theirs.
- The `.pkl` models were saved with scikit-learn 1.6/1.8; the borrowed venv
  has 1.9.1 and warns on load. Pin a matching version in the hosted env.
- The old prototype repo `C:\indian-meta-ad-scraper` stays untouched.
- No GitHub remote yet - ask before creating one.
