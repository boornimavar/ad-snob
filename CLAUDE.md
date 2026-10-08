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
**2026-10-07: switched to "Option A".** Hugging Face now charges for Docker
and Gradio Spaces (only Static Spaces are free), and the user won't pay or
put a card on file. So the laptop collector also does the scoring
(`collector.LocalAnalysis` runs the same `analysis_api` job functions
in-process) and saves straight to Supabase; the gallery becomes a static
page reading Supabase. The Flask service stays for local use and tests.
Meta's official Ad Library API can't replace the collector: outside the EU
it only returns political/social-issue ads.

1. **Analysis API** (built, local): `analysis_api.py`. One search = one job:
   `POST /jobs`, then packets of up to 50 ads (`image_url` or `image_base64`)
   to `/jobs/<id>/ads`, then `/jobs/<id>/finish` groups duplicates across the
   whole search (same as the old pipeline) and returns the same score columns
   as the `ads` table. Packets are only a delivery size, never a cap on ads.
   The library then matches ads against earlier searches (same image hash
   or crop pHash <= 6), without SIFT.
2. **Laptop app** (in progress): `demo.py` (Desktop shortcut, pythonw) runs
   the ORIGINAL dashboard (`dashboard_server_temporary.py` + `dashboard.html`)
   on port 8050 and opens it. The user wants the old UI (hero, industry list,
   filters, live search panel with counts, saved ads, rich cards), so a
   stripped-down gallery search bar and the Tk window were dropped
   (2026-10-07). Added to the old server: results last a day
   (`expire_old_results`, `has_fresh_results`, kept across restarts), the
   "0 ads = probably rate-limited" message, `/api/health` + a page heartbeat
   so the app exits 15 min after the page closes. `collector.py` stays as
   the command-line version. **Windows app built (2026-10-07):** `build_app.py`
   -> dist/Creative-Lens-Windows.zip (0.71 GB zip, 1.41 GB unpacked;
   PyInstaller onedir, no console). Bundles CLIP weights, ranking models,
   dashboard.html and Tesseract (exe + DLLs + tessdata) and points the app at
   them via env vars in `demo.use_packaged_files`; user data goes to
   %LOCALAPPDATA%/Creative Lens. Gotchas fixed: torchvision 0.29's
   `*_stable.pyd` must be added by hand ("operator torchvision::nms does not
   exist"); torch's deep license folders broke Extract All (260-char paths),
   so they're zipped and the build fails above 130 chars. `Creative Lens.exe
   --self-test` checks model + OCR without Meta (passed with Python, PATH and
   the CLIP cache hidden: 41 s first run, 14 s after). Shared via a PUBLIC
   Hugging Face repo (user's choice) with `publish_app.py`; this build holds
   no keys. Next: a colleague tests it on a laptop without Python.
3. **Free hosting** (in progress, Option A): `library.py` has the matching
   rules once (`_LibraryRules`) and two stores: `Library` (SQLAlchemy;
   SQLite locally) and `SupabaseLibrary` (Supabase REST + Storage, signed in
   as a "collector" Auth user; RLS lets it insert/update, never delete;
   anyone reads). `supabase_setup.sql` creates the table, policies and the
   `ad_categories` view. The collector reads its 4 settings from the private
   `.env` (`collector.load_settings`; never print it). `gallery.html` reads
   Supabase when `config.js` exists (public URL + publishable key only;
   `deploy_space.py` builds it and refuses secret keys) and is published to a
   free HF Static Space. Only unique ads are stored, one row per ad per
   category+country, each with a ~600px JPEG copy (Meta image links expire
   within days). **Results last a day (decided 2026-10-07, replacing the
   earlier 14-day Archive):** ads no search found in the last 24 hours are
   hidden and deleted with their pictures (`Library.remove_expired`, run
   before each save and at demo start). The user doesn't want old ads that
   may no longer run; no Archive tab, no Save button (could add later). Still to do: user's Supabase setup +
   first real search into it, publish the gallery, then the `.exe`.
**Next (agreed 2026-10-07):** Phase 1 = package the demo (Collector +
gallery, local library) as one Windows download (PyInstaller, ~1-1.5 GB:
torch, CLIP weights, Tesseract must be bundled) + a user guide; share via
the team's Google Drive (not public HF: Phase 2 builds would hold a key).
Phase 2 = shared library on Hugging Face (Supabase is blocked by ACT).
4. **Improvements**: 👍/👎 labels in the gallery feeding `--retrain`.

## Hard-won facts
- `meta_ads_collector` must be **1.4.0** (from `meta_ads_collector_source.zip`).
  1.5.1 raises on rate limiting and pages through empty results; that broke all
  searches on 2026-10-01. Never `pip install meta-ads-collector`.
- With 1.4.0 a rate-limited search ends quietly with 0 ads; the dashboard now
  says so (NO_ADS_MESSAGE) instead of just "Search complete".
- Request 30 ads per page (`SCRAPE_PAGE_SIZE`), 500 ads max per search. The user
  does not want a lower cap (it drops ads; searches can't resume). The 500
  counts Meta *ads*; each ad can hold several images. Real search: 67 ads ->
  368 images -> 142 unique -> 88 design-led. The old dashboard's "Ads
  collected" box actually counts images (~230-390 typical).
- The OCR "no text -> 0.05" veto misses photos where Tesseract reads texture
  as junk words (conf >= 30). A stricter veto (no words >= 80% conf) was
  tried on 2026-10-06: 4 of 14 ads it would bury had real text, so rejected.
  Plan is 👍/👎 retraining instead.
- Collector 1.4.0 got only a small share of ads (2026-10-08: site shows
  skincare ~290 / fashion ~1,600 / food ~3,900 image ads; we got 59/29/62).
  Two gaps, fixed in `meta_collection.MediaTypeClient` (wraps
  `collector.client`; the vendored zip is untouched): `collector.search()`
  never passed `media_type`, so Meta sent mostly videos; and it stopped at the
  first empty page despite a next cursor (now up to MAX_EMPTY_PAGES=5 in a
  row are skipped). First real run (food, 2026-10-08): 72 -> 122 ads, but
  Meta sent 63 empty pages and the search ended on a run of >5. Raised to
  MAX_EMPTY_PAGES=10 with a `[PAGES]` log line per page plus "Collection
  ended:" in demo.log, to see where ads run out before going higher (more
  requests = more rate-limit risk; 1.5.1's 25 helped break searches on 10-01).
- Failed searches must never auto-restart (makes rate limiting worse).
- The collector reads Meta's website, not an official API: a terms-of-service
  grey area. Don't suggest proxies or other ways around Meta's blocking.

## Working environment
- Windows. Project env: `C:\creative-lens\.venv\Scripts\python.exe` (Python
  3.13, torch CPU, CLIP from git, scikit-learn 1.8.0 to match
  rank_model_v1.pkl, collector 1.4.0 unzipped from the zip into
  site-packages). `design_classifier.pkl` (sklearn 1.6.1) still warns; it is
  diagnostics only.
- Tests: `.venv\Scripts\python.exe -m pytest tests` (129 pass; they never contact Meta or Supabase).
  Window tests share one Tk root: creating many in one process intermittently
  fails here with "Can't find a usable init.tcl".
- Don't run real Meta searches yourself; ask the user (it can rate-limit
  their network). The user may have their own analysis_api running on port
  8000; run test services on another port and don't stop theirs.
- The old prototype repo `C:\indian-meta-ad-scraper` stays untouched.
- No GitHub remote yet - ask before creating one.
