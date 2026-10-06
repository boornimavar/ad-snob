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

Starts on http://127.0.0.1:8000. One search is one job; its ads are sent
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

## Laptop collector (Step 2)

With the analysis API running (above), open the collector window:

```bash
python collector_app.py
```

Type a category, click **Collect**, and when it's done click **Open
results** for a gallery of the design-led ads, best first. (Command-line
version: `python collector.py skincare`.)

It searches Meta from this laptop, sends the ads to the API, and shows how
many were collected, unique, and design-led. Results are saved in
`collector_results/` (a preview page and the full JSON) until the shared
library exists. Use `--api` (or
`CREATIVE_LENS_API_URL`) for a service that isn't on this laptop. The
collector needs only `requirements-collector.txt` plus `meta_ads_collector`
1.4.0, not the AI libraries.

## Notes

- Use `meta_ads_collector` **1.4.0** from `meta_ads_collector_source.zip`.
  Do not `pip install meta-ads-collector` (1.5.x breaks searches).
- The collector reads Meta's Ad Library website, not an official API.
  Confirm with your manager that this is OK for team use.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests
```
