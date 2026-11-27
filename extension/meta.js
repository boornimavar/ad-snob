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
    if (!data.data) return { ads: [], nextCursor: null, error: `${msg} (code ${err.code})`, rateLimited };
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

/** Static image URLs of an ad (skips videos), from cards[] or images[] (models.py). */
export function adImages(ad) {
  const urls = [];
  for (const card of ad.cards || []) {
    if (card.video_hd_url || card.video_sd_url) continue;
    const url = card.original_image_url || card.resized_image_url;
    if (url) urls.push(url);
  }
  if (!urls.length && !(ad.videos || []).length) {
    for (const img of ad.images || []) {
      const url = img.original_image_url || img.resized_image_url;
      if (url) urls.push(url);
    }
  }
  return urls;
}

/** [name, value] from one Set-Cookie header value. */
export function parseSetCookie(value) {
  const pair = String(value).split(";", 1)[0];
  const eq = pair.indexOf("=");
  return eq > 0 ? [pair.slice(0, eq).trim(), pair.slice(eq + 1).trim()] : null;
}
