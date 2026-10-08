"""Searching Meta's Ad Library for static image ads.

Kept separate from the scoring code so the laptop collector can run it
without torch/CLIP. Needs meta_ads_collector 1.4.0 (see CLAUDE.md).
"""
import json

from meta_ads_collector import MetaAdsCollector, FilterConfig

# Consecutive empty pages to step past while Meta still offers a next page
# (1.5.1 allows 25; each request counts towards Meta's rate limit).
MAX_EMPTY_PAGES = 5


class MediaTypeClient:
    """Sits between collector 1.4.0 and its Meta client, fixing two gaps:

    - collector.search() never passes media_type on, so Meta returned every
      ad type (mostly videos) and the images were a small share of each page.
      Here every request asks Meta for `media_type` directly.
    - the collector stops at the first empty page even when Meta offers a
      next one. Here up to MAX_EMPTY_PAGES empty pages in a row are stepped
      past while a next cursor exists; the collector sees the next non-empty page.

    Rate-limit and session-expired answers are passed straight back, so the
    collector's own retry handling is unchanged. Everything else is the client's.
    """

    def __init__(self, client, media_type, between_pages=None, max_empty_pages=MAX_EMPTY_PAGES):
        self._client = client
        self._media_type = media_type
        self._between_pages = between_pages or (lambda: None)
        self._max_empty_pages = max_empty_pages
        self.empty_pages_skipped = 0

    def search_ads(self, **kwargs):
        kwargs["media_type"] = self._media_type
        empty_in_a_row = 0
        while True:
            response, next_cursor = self._client.search_ads(**kwargs)
            if response.get("rate_limited") or response.get("session_expired"):
                return response, next_cursor
            if response.get("ads") or not next_cursor:
                return response, next_cursor
            empty_in_a_row += 1
            if empty_in_a_row > self._max_empty_pages:
                return response, None  # give up: the collector treats this as the end
            self.empty_pages_skipped += 1
            kwargs["cursor"] = next_cursor
            self._between_pages()

    def __getattr__(self, name):
        return getattr(self._client, name)


def classify_failure(exc):
    """Map a collector exception to "rate_limited", "no_response" or "error"."""
    text = str(exc).lower()
    if "rate limit" in text:
        return "rate_limited"
    if any(marker in text for marker in ("timed out", "timeout", "connection was reset", "connection reset", "recv failure", "could not connect")):
        return "no_response"
    return "error"


def safe_name(value):
    value = str(value or "").strip()

    output = []

    for char in value:
        if char.isalnum() or char in (" ", "-", "_"):
            output.append(char)

    value = "".join(output)
    value = "_".join(value.split())

    return value or "Unknown"


def clean_text(value):
    if value is None:
        return ""

    if isinstance(value, (list, tuple)):
        return " ".join(str(x) for x in value)

    if isinstance(value, dict):
        return json.dumps(
            value,
            ensure_ascii=False
        )

    return str(value)


def get_attr(obj, *names, default=""):
    """
    Safely get an attribute from collector model objects.
    Also supports dictionaries.
    """

    for name in names:

        try:
            value = getattr(
                obj,
                name,
                None
            )

            if value is not None:
                return value

        except Exception:
            pass

        if isinstance(obj, dict):

            value = obj.get(name)

            if value is not None:
                return value

    return default


def search_ads_broad(
    query="",
    country="IN",
    status="ACTIVE",
    search_type="KEYWORD_UNORDERED",
    max_results=100,
    page_size=10,
    media_type=None,
    publisher_platforms=None,
    start_date=None,
    end_date=None,
    static_only=False,
    sort_by=None,
    on_collect=None,
    stop_check=None,
    on_interrupted=None,
):
    """
    Broad Meta Ad Library acquisition for the future internal app.

    It searches by keyword without requiring a predefined brand/page ID and
    returns one normalized record per creative.

    No images are written to disk here. The existing intelligence pipeline can
    consume the returned image_url values later.

    on_collect(ad_count) is called after every ad Meta returns, so callers can
    show live collection progress and notice when Meta stops responding.
    stop_check() is checked before each ad; when it returns True collection
    ends early and the creatives gathered so far are returned.

    If Meta cuts collection off partway (rate limiting, timeouts), the
    creatives gathered so far are returned and on_interrupted(exc) is called,
    rather than discarding them. With nothing collected the error is raised.
    """
    collector = MetaAdsCollector()

    # Broad design search is a STATIC image library. Do not let the collector
    # return video ads whose preview frame happens to expose an image URL.
    # The user-facing media selector may still be ALL for acquisition tests,
    # but the design pipeline itself must request IMAGE + no video.
    if static_only:
        effective_media_type = "IMAGE"
        effective_has_image = True
        effective_has_video = False
    else:
        effective_media_type = media_type
        effective_has_image = True if media_type == "IMAGE" else None
        effective_has_video = None

    filter_config = FilterConfig(
        start_date=start_date,
        end_date=end_date,
        media_type=effective_media_type,
        publisher_platforms=publisher_platforms,
        has_image=effective_has_image,
        has_video=effective_has_video,
    )
    # Ask Meta for this media type directly and step past empty pages (see MediaTypeClient).
    # filter_config still runs on the laptop as a safety net for videos that slip through.
    paging = MediaTypeClient(collector.client, effective_media_type or "ALL", between_pages=collector._delay)
    collector.client = paging

    print("\n" + "=" * 70)
    print("BROAD META AD SEARCH — APP MODE")
    print("=" * 70)
    print(f"Query:       {query or '[ALL ADS]'}")
    print(f"Country:     {country}")
    print(f"Status:      {status}")
    print(f"Media:       {'IMAGE (static only)' if static_only else (media_type or 'ALL')}")
    print(f"Platforms:   {publisher_platforms or 'ALL'}")
    print(f"Max results: {max_results}")
    print(f"Sort by:     {sort_by or 'RELEVANCY (not impressions - avoids always surfacing the same big campaigns)'}")
    print("=" * 70)

    ads = collector.search(
        query=query,
        country=country,
        status=status,
        search_type=search_type,
        max_results=max_results,
        page_size=page_size,
        filter_config=filter_config,
        sort_by=sort_by,
    )

    records = []
    ad_count = 0
    interruption = []

    def until_interrupted(stream):
        try:
            yield from stream
        except Exception as exc:
            interruption.append(exc)

    for ad in until_interrupted(ads):
        if stop_check is not None and stop_check():
            print(f"\n[STOPPED] Collection ended early after {ad_count} ad(s).")
            break
        ad_count += 1
        if on_collect is not None:
            on_collect(ad_count)
        page = get_attr(ad, "page", default=None)
        page_name = clean_text(get_attr(page, "name", "page_name", default=""))
        page_id = clean_text(get_attr(page, "id", "page_id", default=""))
        creatives = get_attr(ad, "creatives", "creative", default=[]) or []
        if not isinstance(creatives, (list, tuple)):
            creatives = [creatives]

        for creative_index, creative in enumerate(creatives):
            image_url = clean_text(get_attr(creative, "image_url", "image", "image_uri", default=""))
            thumbnail_url = clean_text(get_attr(creative, "thumbnail_url", "thumbnail", default=""))
            video_url = clean_text(get_attr(creative, "video_url", "video", default=""))
            preview_url = clean_text(get_attr(creative, "video_preview_image_url", default=""))

            # STATIC means static. Never turn a video thumbnail/preview into a
            # design-library image. Some Meta collector responses expose an image
            # URL even when the underlying creative is video, so checking only
            # image_url is not sufficient.
            if static_only:
                creative_type = clean_text(get_attr(
                    creative, "type", "creative_type", "format", "media_type", "display_format", default=""
                )).upper()
                ad_type = clean_text(get_attr(ad, "ad_type", default="")).upper()

                raw = get_attr(ad, "raw_data", default={}) or {}
                raw_display = clean_text(get_attr(raw, "display_format", "format", default="")).upper()
                raw_videos = get_attr(raw, "videos", default=[]) or []

                video_markers = ("VIDEO", "REELS", "VIDEO_SLIDESHOW", "VIDEO_COLLECTION")
                looks_like_video = (
                    bool(video_url)
                    or bool(preview_url)
                    or any(marker in creative_type for marker in video_markers)
                    or any(marker in ad_type for marker in video_markers)
                    or any(marker in raw_display for marker in video_markers)
                )

                # If the collector gives us a raw videos payload but no explicit
                # static creative type, treat the creative as video unless it has
                # a real image asset and no video-specific fields. This prevents
                # video previews from slipping through while preserving genuine
                # image creatives in mixed responses.
                if raw_videos and not creative_type and not raw_display:
                    looks_like_video = True

                if looks_like_video:
                    continue

            # For the static pipeline we require the actual image asset.
            # Thumbnail/video-preview fallback is deliberately disabled.
            if not image_url:
                if not static_only:
                    image_url = thumbnail_url or (preview_url if video_url else "")
            if not image_url:
                continue

            records.append({
                "ad_id": clean_text(get_attr(ad, "id", "ad_id", default="")),
                "page_id": page_id,
                "page_name": page_name,
                "creative_index": creative_index,
                "image_url": image_url,
                "video_url": video_url,
                "body": clean_text(get_attr(creative, "body", default="")),
                "title": clean_text(get_attr(creative, "title", default="")),
                "cta": clean_text(get_attr(creative, "cta_text", "cta", default="")),
                "link_url": clean_text(get_attr(creative, "link_url", default="")),
                "is_active": get_attr(ad, "is_active", default=None),
                "delivery_start_time": clean_text(get_attr(ad, "delivery_start_time", default="")),
                "delivery_stop_time": clean_text(get_attr(ad, "delivery_stop_time", default="")),
                "publisher_platforms": clean_text(get_attr(ad, "publisher_platforms", default=[])),
                "impressions": clean_text(get_attr(ad, "impressions", default="")),
                "spend": clean_text(get_attr(ad, "spend", default="")),
                "ad_type": clean_text(get_attr(ad, "ad_type", default="")),
                "categories": clean_text(get_attr(ad, "categories", default=[])),
            })

    if interruption:
        if not records:
            raise interruption[0]
        print(f"\n[INTERRUPTED] Meta stopped the search after {ad_count} ad(s): {interruption[0]}")
        print("              Keeping the creatives collected so far.")
        if on_interrupted is not None:
            on_interrupted(interruption[0])

    print(f"\nAds received:      {ad_count}")
    print(f"Empty pages skipped: {paging.empty_pages_skipped}")
    if static_only:
        print("Static filter:    IMAGE + has_video=False; video previews/thumbnails excluded")
    print(f"Image creatives:   {len(records)}")

    return records
