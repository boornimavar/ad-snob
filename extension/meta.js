// Meta Ad Library search, ported from meta_ads_collector 1.4.0 (client.py),
// the version the laptop app uses. Pure functions: no Chrome APIs, so this
// file can be tested with plain Node (see extension/meta.test.mjs).

export const HOME_URL = "https://www.facebook.com/";
export const AD_LIBRARY_URL = "https://www.facebook.com/ads/library/";
export const GRAPHQL_URL = "https://www.facebook.com/api/graphql/";
export const DOC_ID_SEARCH = "25464068859919530"; // AdLibrarySearchPaginationQuery
const FALLBACK_REV = "1033837939";

/** Tokens from the logged-out facebook.com homepage (client.py _extract_tokens). */
export function extractTokens(html) {
  const first = (patterns) => {
    for (const p of patterns) {
      const m = html.match(p);
      if (m) return m[1];
    }
    return undefined;
  };
  const tokens = {};
  const set = (key, value) => { if (value !== undefined) tokens[key] = value; };
  set("lsd", first([/"LSD",\[\],\{"token":"([^"]+)"\}/, /\["LSD",\[\],\{"token":"([^"]+)"/, /"lsd":"([^"]+)"/, /name="lsd" value="([^"]+)"/]));
  const rev = first([/"__spin_r":(\d+)/, /"server_revision":(\d+)/, /"revision":(\d+)/]);
  if (rev) { tokens.__rev = rev; tokens.__spin_r = rev; }
  set("__spin_t", first([/"__spin_t":(\d+)/]));
  set("__spin_b", first([/"__spin_b":"([^"]+)"/]));
  set("__hsi", first([/"__hsi":"(\d+)"/, /"hsi":"(\d+)"/]));
  set("fb_dtsg", first([/"DTSGInitialData",\[\],\{"token":"([^"]+)"/]));
  set("__dyn", first([/"__dyn":"([^"]+)"/]));
  set("__csr", first([/"__csr":"([^"]+)"/]));
  set("__hs", first([/"__hs":"([^"]+)"/]));
  set("__hsdp", first([/"__hsdp":"([^"]+)"/]));
  set("__hblp", first([/"__hblp":"([^"]+)"/]));
  set("__comet_req", first([/"__comet_req":(\d+)/]));
  set("jazoest", first([/"jazoest["\s:]+(\d+)/]));
  set("v", first([/"v"\s*:\s*"([a-f0-9]{4,10})"/]));
  set("x-asbd-id", first([/"asbd_id"\s*:\s*"?(\d+)"?/, /x-asbd-id["\s:]+(\d+)/]));
  return tokens;
}

export function jazoest(lsd) {
  if (!lsd) return "2893";
  let total = 0;
  for (const ch of lsd) total += ch.codePointAt(0);
  return String(2 + total);
}

export function encodeRequestId(counter) {
  return counter < 10 ? String(counter) : counter.toString(36);
}

function randomId(length) {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  for (let i = 0; i < length; i++) out += chars[Math.floor(Math.random() * chars.length)];
  return out;
}

export function uuid() {
  return crypto.randomUUID();
}

/** Variables for AdLibrarySearchPaginationQuery (client.py search_ads), relevancy sort. */
export function searchVariables({ query, country = "IN", cursor, first = 30, sessionId, collationToken, v }) {
  const variables = {
    activeStatus: "ACTIVE", adType: "ALL", bylines: [], collationToken, contentLanguages: [],
    countries: [country], excludedIDs: [], first, isTargetedCountry: false, location: null,
    mediaType: "ALL", multiCountryFilterMode: null, pageIDs: [], potentialReachInput: [],
    publisherPlatforms: [], queryString: query, regions: [], searchType: "KEYWORD_UNORDERED",
    sessionID: sessionId, source: null, startDate: null, v: v || "fbece7", viewAllPageID: "0",
  };
  if (cursor) variables.cursor = cursor;
  return variables;
}

/** Form fields for a GraphQL POST (client.py _build_graphql_payload). */
export function buildPayload(tokens, variables, counter) {
  const lsd = tokens.lsd || "";
  const payload = {
    av: "0", __aaid: "0", __user: "0", __a: "1", __req: encodeRequestId(counter),
    __hs: tokens.__hs || "20476.HYP:comet_plat_default_pkg.2.1...0", dpr: "1", __ccg: "GOOD",
    __rev: tokens.__rev || FALLBACK_REV, __s: [randomId(6), randomId(6), randomId(6)].join(":"),
    __hsi: tokens.__hsi || String(Date.now()), __comet_req: tokens.__comet_req || "94",
    lsd, jazoest: tokens.jazoest || jazoest(lsd), __spin_r: tokens.__spin_r || FALLBACK_REV,
    __spin_b: tokens.__spin_b || "trunk", __spin_t: tokens.__spin_t || String(Math.floor(Date.now() / 1000)),
    __jssesw: "1", fb_api_caller_class: "RelayModern",
    fb_api_req_friendly_name: "AdLibrarySearchPaginationQuery", server_timestamps: "true",
    variables: JSON.stringify(variables), doc_id: DOC_ID_SEARCH,
  };
  // Only send these when really found on the page: stale values make Meta
  // answer with a misleading "rate limit" error (code 1675004).
  for (const key of ["__dyn", "__csr", "fb_dtsg", "__hsdp", "__hblp"]) {
    if (tokens[key]) payload[key] = tokens[key];
  }
  return payload;
}

/** Parse a GraphQL search answer into ads + the next-page cursor (client.py _parse_search_response). */
export function parseSearchResponse(text) {
  const body = text.startsWith("for (;;);") ? text.slice(9) : text;
  let data;
  try {
    data = JSON.parse(body);
  } catch {
    return { ads: [], nextCursor: null, error: `Not JSON: ${body.slice(0, 160)}` };
  }
  if (data.errors && data.errors.length) {
    const err = data.errors[0];
    const msg = err.message || err.summary || "Unknown error";
    const rateLimited = err.code === 1675004 || /rate limit/i.test(msg);
    const sessionExpired = !rateLimited && (err.code === 1357004 || err.code === 1357001 || /session/i.test(msg));
    if (rateLimited || sessionExpired) return { ads: [], nextCursor: null, error: null, rateLimited, sessionExpired };
    if (!data.data) return { ads: [], nextCursor: null, error: `${msg} (code ${err.code})` };
  }
  const root = data.data || {};
  const results = (root.ad_library_main || {}).search_results_connection
    || (root.adLibraryMain || {}).searchResultsConnection || root;
  const pageInfo = results.page_info || results.pageInfo || {};
  const nextCursor = (pageInfo.has_next_page || pageInfo.hasNextPage) ? (pageInfo.end_cursor || pageInfo.endCursor || null) : null;
  const ads = [];
  for (const edge of results.edges || []) {
    const node = edge.node || edge;
    for (const ad of node.collated_results || []) {
      const flat = { ...ad };
      for (const [k, v] of Object.entries(ad.snapshot || {})) if (!(k in flat)) flat[k] = v;
      ads.push(flat);
    }
  }
  return { ads, nextCursor, error: null };
}

const VIDEO_MARKERS = ["VIDEO", "REELS", "VIDEO_SLIDESHOW", "VIDEO_COLLECTION"];

/**
 * The ad's pictures exactly as the laptop app picks them (models.py
 * Ad.from_graphql_response + the static-only filters in search_ads_broad):
 * a carousel gives one picture per card, the RESIZED one first; a normal ad
 * gives only its FIRST picture, the ORIGINAL one first. The whole ad is
 * skipped if any part of it is a video or Meta's display_format says video.
 * `ad` is the flattened collated result (snapshot fields merged in).
 */
export function adImages(ad) {
  return adCreatives(ad).map((c) => c.image);
}

// models.py _extract_body_text + meta_collection.clean_text
const bodyText = (b) => (b == null ? "" : typeof b === "object" && !Array.isArray(b) ? String(b.text ?? "") : String(b));
const plain = (v) => (v == null ? "" : String(v));

/**
 * The ad's picture creatives with each one's own text, as the app keeps them:
 * a carousel card has its own title/body (the app's off-topic check reads
 * those), a normal ad the ad's. [{image, title, body, cta}]
 */
export function adCreatives(ad) {
  const cards = ad.cards || [];
  const videos = ad.videos || [];
  const images = ad.images || [];
  let creatives;
  if (cards.length) {
    creatives = cards.map((c) => ({
      image: c.resized_image_url || c.original_image_url, video: c.video_hd_url || c.video_sd_url,
      title: plain(c.title), body: bodyText(c.body), cta: plain(c.cta_text || ad.cta_text),
    }));
  } else if (ad.body != null || ad.title != null || ad.videos != null || ad.images != null) {
    const v = videos[0] || {};
    const img = images[0] || {};
    creatives = [{
      image: img.original_image_url || img.resized_image_url, video: v.video_hd_url || v.video_sd_url,
      title: plain(ad.title), body: bodyText(ad.body), cta: plain(ad.cta_text),
    }];
  } else {
    creatives = [];
  }
  if (creatives.some((c) => c.video) || videos.length) return []; // has_video=False: no part may be a video
  const display = String(ad.display_format || "").toUpperCase();
  if (VIDEO_MARKERS.some((m) => display.includes(m))) return [];
  return creatives.filter((c) => c.image).map(({ image, title, body, cta }) => ({ image, title, body, cta }));
}

export const PAGE_SIZE = 30; // ads per request, as the app (SCRAPE_PAGE_SIZE)
export const MAX_IMAGE_ADS = 500; // per search, as the app

/**
 * Page through a search the way the 1.4.0 collector does (collector.py search):
 * stop at an empty page, at the last page, at MAX_IMAGE_ADS image ads, or after
 * 3 rate-limit answers in a row (waiting 5*n + 1-3 s between them). Pauses
 * 2-3 s between pages. Never restarts on its own.
 *
 * fetchPage(cursor) -> {ads, nextCursor, error, rateLimited, sessionExpired}
 * onPage(newImageAds, totalImageAds) is called after each page.
 * Returns {ads, pages, stoppedBecause}.
 */
export async function collectAds({ fetchPage, onPage = () => {}, shouldStop = () => false,
  sleep = (ms) => new Promise((r) => setTimeout(r, ms)), maxImageAds = MAX_IMAGE_ADS, random = Math.random }) {
  const seen = new Set();
  const ads = [];
  let cursor = null;
  let pages = 0;
  for (;;) {
    if (shouldStop()) return { ads, pages, stoppedBecause: "stopped" };
    let page;
    for (let attempt = 1; ; attempt++) {
      page = await fetchPage(cursor);
      if (!page.rateLimited && !page.sessionExpired) break;
      if (attempt >= 3) return { ads, pages, stoppedBecause: page.rateLimited ? "rate_limited" : "session_expired" };
      await sleep(page.rateLimited ? (5 * attempt + 1 + random() * 2) * 1000 : 2000);
      if (shouldStop()) return { ads, pages, stoppedBecause: "stopped" };
    }
    if (page.error) return { ads, pages, stoppedBecause: "error", error: page.error };
    pages++;
    if (!page.ads.length) return { ads, pages, stoppedBecause: "empty_page" };
    const fresh = [];
    for (const ad of page.ads) {
      if (ads.length + fresh.length >= maxImageAds) break;
      if (!ad.images.length || (ad.id && seen.has(ad.id))) continue; // image ads only, each once
      if (ad.id) seen.add(ad.id);
      fresh.push(ad);
    }
    ads.push(...fresh);
    onPage(fresh, ads.length);
    if (ads.length >= maxImageAds) return { ads, pages, stoppedBecause: "max_ads" };
    if (!page.nextCursor) return { ads, pages, stoppedBecause: "last_page" };
    cursor = page.nextCursor;
    await sleep((2 + random()) * 1000); // like the app's rate-limit delay (2 s + up to 1 s)
  }
}

/** One ad as the website needs it. */
export function simplifyAd(raw) {
  const ad = { ...(raw.snapshot || {}), ...raw }; // fields may sit at the top or inside snapshot
  const creatives = adCreatives(ad);
  return {
    id: String(ad.ad_archive_id || ad.adArchiveID || ad.id || ""),
    page: ad.page_name || (ad.snapshot || {}).page_name || "",
    images: creatives.map((c) => c.image),
    texts: creatives.map(({ title, body, cta }) => ({ title, body, cta })), // one per picture, like the app's records
    start: ad.start_date || ad.ad_delivery_start_time || null,
    title: ad.title || (ad.snapshot || {}).title || "",
    body: ((ad.body || (ad.snapshot || {}).body || {}).text) || (typeof ad.body === "string" ? ad.body : ""),
    cta: ad.cta_text || (ad.snapshot || {}).cta_text || "",
  };
}

/** [name, value] from one Set-Cookie header value. */
export function parseSetCookie(value) {
  const pair = String(value).split(";", 1)[0];
  const eq = pair.indexOf("=");
  return eq > 0 ? [pair.slice(0, eq).trim(), pair.slice(eq + 1).trim()] : null;
}
