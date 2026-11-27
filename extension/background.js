// Ad Snob helper: runs Ad Library searches from the user's own browser.
//
// It searches LOGGED OUT, like the laptop app: requests are sent without the
// browser's own Facebook cookies (credentials: "omit"), so the user's Facebook
// account is never used. The helper keeps its own small cookie jar instead
// (datr, fr, sb from the logged-out homepage, captured with webRequest) and
// adds it to its own requests with a declarativeNetRequest rule that only
// matches requests this extension makes.
//
// The website talks to it through bridge.js over a "search" port: it sends
// {query, country}; the helper sends {type: "progress"} after every page and
// {type: "done"} at the end. Closing the port (the Stop button) stops it.
import {
  HOME_URL, AD_LIBRARY_URL, GRAPHQL_URL, extractTokens, searchVariables, buildPayload,
  parseSearchResponse, parseSetCookie, uuid, collectAds, simplifyAd, PAGE_SIZE,
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

/** Load the logged-out homepage for fresh tokens (client.py initialize). */
async function startSession(referer, note) {
  await applyRules(referer);
  const home = await fetch(HOME_URL, { credentials: "omit", cache: "no-store" });
  const html = await home.text();
  const tokens = extractTokens(html);
  note(`homepage: HTTP ${home.status}, tokens: ${Object.keys(tokens).join(", ") || "none"}`);
  if (!tokens.lsd) throw new Error("Couldn't get a token from facebook.com (no lsd).");
  await sleep(1500 + Math.random() * 1500); // a pause like a person, as the app does
  await applyRules(referer); // now with the cookies the homepage set
  return tokens;
}

/** A full search: pages until done (see collectAds), reporting each page. */
async function search({ query, country = "IN", maxImageAds, onProgress = () => {}, shouldStop = () => false }) {
  const diag = { steps: [] };
  const note = (s) => diag.steps.push(s);
  const referer = `${AD_LIBRARY_URL}?active_status=active&ad_type=all&country=${country}&q=${encodeURIComponent(query)}`;
  const sessionId = uuid();
  const collationToken = uuid();
  let tokens = await startSession(referer, note);
  let counter = 0;
  let refreshed = false;

  async function fetchPage(cursor) {
    const variables = searchVariables({ query, country, cursor, first: PAGE_SIZE, sessionId, collationToken, v: tokens.v });
    const response = await fetch(GRAPHQL_URL, {
      method: "POST", credentials: "omit", cache: "no-store",
      headers: {
        "content-type": "application/x-www-form-urlencoded",
        "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
        "x-fb-lsd": tokens.lsd,
        "x-asbd-id": tokens["x-asbd-id"] || "359341",
      },
      body: new URLSearchParams(buildPayload(tokens, variables, ++counter)),
    });
    if (response.status === 403 && !refreshed) { // expired tokens: refresh once, like the app
      refreshed = true;
      note("search: HTTP 403, refreshing the session once");
      tokens = await startSession(referer, note);
      return fetchPage(cursor);
    }
    const parsed = parseSearchResponse(await response.text());
    const ads = parsed.ads.map(simplifyAd);
    note(`page ${counter}: HTTP ${response.status}, ${ads.length} ads${parsed.rateLimited ? ", Meta said slow down" : ""}`
      + `${parsed.sessionExpired ? ", session expired" : ""}${parsed.error ? `, error: ${parsed.error}` : ""}`);
    if (parsed.sessionExpired) tokens = await startSession(referer, note);
    return { ...parsed, ads };
  }

  const result = await collectAds({ fetchPage, shouldStop, maxImageAds,
    onPage: (fresh, total) => onProgress({ ads: fresh, total }) });
  note(`finished: ${result.ads.length} image ads from ${result.pages} pages (${result.stoppedBecause})`);
  return { ok: result.stoppedBecause !== "error" || result.ads.length > 0, ...result, diag };
}

function describe(stoppedBecause) {
  return {
    rate_limited: "Meta asked us to slow down, so the search stopped early. Wait a while before searching again.",
    session_expired: "Meta's session kept expiring, so the search stopped early.",
  }[stoppedBecause] || "";
}

// The website (through bridge.js): streams pages, stops when the port closes.
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "search") return;
  let stopped = false;
  port.onDisconnect.addListener(() => { stopped = true; });
  port.onMessage.addListener(async (message) => {
    if (busy) { port.postMessage({ type: "done", ok: false, error: "A search is already running in this browser." }); return; }
    busy = true;
    try {
      const result = await search({
        query: String(message.query || "").slice(0, 60), country: message.country || "IN",
        shouldStop: () => stopped,
        onProgress: (p) => { if (!stopped) port.postMessage({ type: "progress", ...p }); },
      });
      if (!stopped) port.postMessage({ type: "done", ok: result.ok, total: result.ads.length, pages: result.pages,
        stoppedBecause: result.stoppedBecause, warning: describe(result.stoppedBecause), error: result.error, diag: result.diag });
    } catch (err) {
      if (!stopped) port.postMessage({ type: "done", ok: false, error: String(err.message || err), diag: { steps: [] } });
    } finally {
      busy = false;
    }
  });
});

/** An ad picture from Facebook's picture servers, as base64 (websites can't read them directly). */
async function fetchImage(url) {
  let host;
  try { host = new URL(url).hostname; } catch { return { ok: false, error: "Not a link." }; }
  if (!(host === "fbcdn.net" || host.endsWith(".fbcdn.net"))) return { ok: false, error: "Only Facebook picture links are allowed." };
  const response = await fetch(url, { credentials: "omit", cache: "force-cache" });
  if (!response.ok) return { ok: false, error: `Picture link answered HTTP ${response.status} (Meta's picture links expire after a few days).` };
  const bytes = new Uint8Array(await response.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return { ok: true, type: response.headers.get("content-type") || "image/jpeg", base64: btoa(binary) };
}

// The pop-up's quick test: one small search (up to 30 image ads).
chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (message?.type === "image") {
    fetchImage(String(message.url || "")).then(reply).catch((err) => reply({ ok: false, error: String(err.message || err) }));
    return true;
  }
  if (message?.type !== "search") return false;
  if (busy) { reply({ ok: false, error: "A search is already running." }); return false; }
  busy = true;
  search({ query: String(message.query || "skincare").slice(0, 60), country: message.country || "IN", maxImageAds: 30 })
    .then(reply)
    .catch((err) => reply({ ok: false, error: String(err.message || err), diag: { steps: [] } }))
    .finally(() => { busy = false; });
  return true; // reply comes later
});
