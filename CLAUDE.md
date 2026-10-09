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
- **Collection is at the original 1.4.0 behaviour on purpose (reverted
  2026-10-08).** The site shows far more image ads than we collect (skincare
  ~290 / food ~1,600-3,900 vs our ~60-120). Two fixes were tried the same day
  and reverted at the user's request:
  - Passing media_type=IMAGE to Meta: Meta's IMAGE excludes carousel /
    multi-version ads made of images (the big-brand, design-led ones). Beauty
    fell from 62% to 5% design-led. If retried, request ALL.
  - Stepping past empty pages (cursor still present): Meta then sends tiny
    pages (~4 ads instead of 30) with 1-3 empty pages between, mostly videos:
    351 requests in 21 min for 175 image ads. Far too many requests
    (rate-limit risk), and the 180 s stall watchdog (counts kept ads, not
    pages) wrongly failed a search as "Meta stopped responding".
  Any future attempt needs a hard cap on requests per search, a watchdog
  that counts pages, and one real search before stacking more changes. The
  dashboard's poll used to give up after 900 polls (~18 min, hiding Stop);
  since 2026-10-09 it only gives up after ~3 min of the app not answering
  (a colleague's slower laptop needs 30+ min for ~390 images).
- Smart App Control on a colleague's laptop (2026-10-08) blocked scipy's
  `_propack` DLL (pulled in by scikit-learn). Result: the ranking model AND
  CLIP failed (load_models crashed half-way, leaving prompt features None),
  so every score came from the heuristic (max ~0.42, never design-led). Fix:
  the app reads both models from `rank_model_v1.json` / `design_classifier.json`
  with numpy (`linear_model.py`; identical scores), the build excludes
  sklearn, and load_models only marks CLIP loaded once it fully succeeds.
  `--retrain` still uses sklearn and writes the JSON too.
- Failed searches must never auto-restart (makes rate limiting worse).
- The collector reads Meta's website, not an official API: a terms-of-service
  grey area. Don't suggest proxies or other ways around Meta's blocking.

## Working environment
- Windows. Project env: `C:\creative-lens\.venv\Scripts\python.exe` (Python
  3.13, torch CPU, CLIP from git, scikit-learn 1.8.0 to match
  rank_model_v1.pkl, collector 1.4.0 unzipped from the zip into
  site-packages). `design_classifier.pkl` (sklearn 1.6.1) still warns; it is
  diagnostics only.
- Tests: `.venv\Scripts\python.exe -m pytest tests` (142 pass; they never contact Meta or Supabase).
  Window tests share one Tk root: creating many in one process intermittently
  fails here with "Can't find a usable init.tcl".
- Don't run real Meta searches yourself; ask the user (it can rate-limit
  their network). The user may have their own analysis_api running on port
  8000; run test services on another port and don't stop theirs.
- The old prototype repo `C:\indian-meta-ad-scraper` stays untouched.
- No GitHub remote yet - ask before creating one.
