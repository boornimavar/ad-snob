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
1. **Analysis API** (next): endpoint that takes ad images/URLs and returns
   design scores (rank_score etc.) and duplicate groups. Build and test locally.
2. **Laptop collector**: lightweight app (no torch/CLIP) that searches Meta and
   sends ads to the API. Later packaged as a single .exe (PyInstaller).
3. **Free hosting**: analysis + dashboard on Hugging Face Spaces (enough RAM for
   CLIP), shared library in a free hosted DB (Supabase/Neon) since free hosts
   lose files on restart.
4. **Improvements**: 👍/👎 labels in the gallery feeding `--retrain`.

## Hard-won facts
- `meta_ads_collector` must be **1.4.0** (from `meta_ads_collector_source.zip`).
  1.5.1 raises on rate limiting and pages through empty results; that broke all
  searches on 2026-10-01. Never `pip install meta-ads-collector`.
- With 1.4.0 a rate-limited search ends quietly with 0 ads; the dashboard should
  explain that instead of just "Search complete" (not fixed yet).
- Request 30 ads per page (`SCRAPE_PAGE_SIZE`), 500 ads max per search. The user
  does not want a lower cap (it drops ads; searches can't resume).
- Failed searches must never auto-restart (makes rate limiting worse).
- The collector reads Meta's website, not an official API: a terms-of-service
  grey area. Don't suggest proxies or other ways around Meta's blocking.

## Working environment
- Windows. Python env currently borrowed from the old repo:
  `C:\indian-meta-ad-scraper\.venv313\Scripts\python.exe` (has torch, CLIP,
  collector 1.4.0). A dedicated venv for this repo is still to do.
- Tests: `python -m pytest tests` (43 pass; they never contact Meta).
- The old prototype repo `C:\indian-meta-ad-scraper` stays untouched.
- No GitHub remote yet - ask before creating one.
