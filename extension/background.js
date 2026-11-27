// Ad Snob helper: runs Ad Library searches from the user's own browser.
//
// It searches LOGGED OUT, like the laptop app: requests are sent without the
// browser's own Facebook cookies (credentials: "omit"), so the user's Facebook
// account is never used. The helper keeps its own small cookie jar instead
// (datr, fr, sb from the logged-out homepage, captured with webRequest) and
// adds it to its own requests with a declarativeNetRequest rule that only
// matches requests this extension makes.
import {
  HOME_URL, AD_LIBRARY_URL, GRAPHQL_URL, extractTokens, searchVariables, buildPayload,
  parseSearchResponse, adImages, parseSetCookie, uuid,
} from "./meta.js";

const EXTENSION_ORIGIN = `chrome-extension://${chrome.runtime.id}`;
const jar = new Map([["wd", "1536x864"], ["dpr", "1"]]); // viewport cookies a browser would have
let busy = false;

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (details.initiator !== EXTENSION_ORIGIN) return; // only our own requests, never the user's tabs
    for (const header of details.responseHeaders || []) {
      if (header.name.toLowerCase() !== "set-cookie") continue;
      for (const line of String(header.value).split("\n")) {
        const cookie = parseSetCookie(line);
        if (cookie) jar.set(cookie[0], cookie[1]);
      }
    }
  },
  { urls: ["https://www.facebook.com/*"] },
  ["responseHeaders", "extraHeaders"],
);

async function applyRules(referer) {
  const cookie = [...jar].map(([k, v]) => `${k}=${v}`).join("; ");
  const ours = { initiatorDomains: [chrome.runtime.id], resourceTypes: ["xmlhttprequest", "other"] };
  await chrome.declarativeNetRequest.updateSessionRules({
    removeRuleIds: [1, 2],
    addRules: [
      { id: 1, priority: 1, condition: { urlFilter: "||www.facebook.com/", ...ours },
        action: { type: "modifyHeaders", requestHeaders: [{ header: "cookie", operation: "set", value: cookie }] } },
      { id: 2, priority: 2, condition: { urlFilter: "||www.facebook.com/api/graphql", ...ours },
        action: { type: "modifyHeaders", requestHeaders: [
          { header: "cookie", operation: "set", value: cookie },
          { header: "origin", operation: "set", value: "https://www.facebook.com" },
          { header: "referer", operation: "set", value: referer },
        ] } },
    ],
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** One small search: homepage for tokens, then one page of results. */
async function search(query, country = "IN") {
  const diag = { steps: [] };
  const note = (s) => diag.steps.push(s);
  const referer = `${AD_LIBRARY_URL}?active_status=active&ad_type=all&country=${country}&q=${encodeURIComponent(query)}`;

  await applyRules(referer);
  const home = await fetch(HOME_URL, { credentials: "omit", cache: "no-store" });
  const html = await home.text();
  note(`homepage: HTTP ${home.status}, ${Math.round(html.length / 1024)} KB`);
  const tokens = extractTokens(html);
  note(`tokens found: ${Object.keys(tokens).join(", ") || "none"}`);
  note(`helper cookies: ${[...jar.keys()].join(", ")}`);
  if (!tokens.lsd) return { ok: false, error: "Couldn't get a token from facebook.com (no lsd).", diag };

  await sleep(1500 + Math.random() * 1500); // a pause like a person, as the app does
  await applyRules(referer); // now with the cookies the homepage set
  const variables = searchVariables({ query, country, sessionId: uuid(), collationToken: uuid(), v: tokens.v });
  const headers = {
    "content-type": "application/x-www-form-urlencoded",
    "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
    "x-fb-lsd": tokens.lsd,
    "x-asbd-id": tokens["x-asbd-id"] || "359341",
  };
  const response = await fetch(GRAPHQL_URL, {
    method: "POST", credentials: "omit", cache: "no-store", headers,
    body: new URLSearchParams(buildPayload(tokens, variables, 1)),
  });
  const text = await response.text();
  note(`search: HTTP ${response.status}, ${Math.round(text.length / 1024)} KB`);
  const parsed = parseSearchResponse(text);
  if (parsed.error) {
    note(`Meta said: ${parsed.error}`);
    return { ok: false, error: parsed.rateLimited ? "Meta is rate-limiting this connection." : parsed.error, diag };
  }
  const ads = parsed.ads.map((ad) => ({
    id: String(ad.ad_archive_id || ad.adArchiveID || ad.id || ""),
    page: ad.page_name || (ad.snapshot || {}).page_name || "",
    images: adImages(ad),
    start: ad.start_date || ad.ad_delivery_start_time || null,
  }));
  note(`ads: ${ads.length}, with images: ${ads.filter((a) => a.images.length).length}, more pages: ${parsed.nextCursor ? "yes" : "no"}`);
  return { ok: true, ads, diag };
}

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (message?.type !== "search") return false;
  if (busy) {
    reply({ ok: false, error: "A search is already running." });
    return false;
  }
  busy = true;
  search(String(message.query || "skincare").slice(0, 60), message.country || "IN")
    .then(reply)
    .catch((err) => reply({ ok: false, error: String(err), diag: { steps: [] } }))
    .finally(() => { busy = false; });
  return true; // reply comes later
});
