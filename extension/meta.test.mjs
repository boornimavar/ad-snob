// Run: node extension/meta.test.mjs   (no network; fixtures only)
import assert from "node:assert/strict";
import { extractTokens, jazoest, encodeRequestId, searchVariables, buildPayload, parseSearchResponse, adImages, parseSetCookie } from "./meta.js";

const html = `["LSD",[],{"token":"AVq1bC2d"}] "__spin_r":1012345678 "__spin_t":1790000000 "__spin_b":"trunk"
  "hsi":"7412345678901234567" "__hs":"20476.HYP:comet" "__comet_req":15 "v":"fbece7" "asbd_id":"359341"`;
const tokens = extractTokens(html);
assert.deepEqual(tokens, {
  lsd: "AVq1bC2d", __rev: "1012345678", __spin_r: "1012345678", __spin_t: "1790000000", __spin_b: "trunk",
  __hsi: "7412345678901234567", __hs: "20476.HYP:comet", __comet_req: "15", v: "fbece7", "x-asbd-id": "359341",
});

assert.equal(jazoest("AVq1bC2d"), String(2 + [..."AVq1bC2d"].reduce((s, c) => s + c.charCodeAt(0), 0)));
assert.equal(jazoest(""), "2893");
assert.equal(encodeRequestId(7), "7");
assert.equal(encodeRequestId(36), "10");

const vars = searchVariables({ query: "skincare", sessionId: "s", collationToken: "c", v: "fbece7" });
assert.equal(vars.queryString, "skincare");
assert.deepEqual(vars.countries, ["IN"]);
assert.equal(vars.first, 30);
assert.equal(vars.cursor, undefined);
const payload = buildPayload(tokens, vars, 1);
assert.equal(payload.lsd, "AVq1bC2d");
assert.equal(payload.doc_id, "25464068859919530");
assert.ok(!("__dyn" in payload), "never send __dyn unless found on the page");

const answer = "for (;;);" + JSON.stringify({ data: { ad_library_main: { search_results_connection: {
  page_info: { has_next_page: true, end_cursor: "NEXT" },
  edges: [{ node: { collated_results: [
    { ad_archive_id: "1", snapshot: { page_name: "Dot & Key", images: [{ original_image_url: "https://x/1.jpg" }], videos: [] } },
    { ad_archive_id: "2", snapshot: { page_name: "Video brand", videos: [{ video_hd_url: "https://x/v.mp4" }], images: [] } },
    { ad_archive_id: "3", snapshot: { page_name: "Carousel", cards: [{ resized_image_url: "https://x/c1.jpg" }, { original_image_url: "https://x/c2.jpg" }] } },
  ] } }] } } } });
const parsed = parseSearchResponse(answer);
assert.equal(parsed.error, null);
assert.equal(parsed.nextCursor, "NEXT");
assert.equal(parsed.ads.length, 3);
assert.equal(parsed.ads[0].page_name, "Dot & Key");
assert.deepEqual(adImages(parsed.ads[0]), ["https://x/1.jpg"]);
assert.deepEqual(adImages(parsed.ads[1]), []); // videos are skipped
assert.deepEqual(adImages(parsed.ads[2]), ["https://x/c1.jpg", "https://x/c2.jpg"]);

const limited = parseSearchResponse(JSON.stringify({ errors: [{ code: 1675004, message: "Rate limit exceeded" }] }));
assert.equal(limited.rateLimited, true);
assert.equal(parseSearchResponse("<html>").error.startsWith("Not JSON"), true);

assert.deepEqual(parseSetCookie("datr=abc123; expires=Fri; path=/; domain=.facebook.com; secure"), ["datr", "abc123"]);

// --- collectAds: the paging rules (fake Meta, no waiting) ---
const { collectAds, simplifyAd } = await import("./meta.js");
const noWait = async () => {};
const img = (id) => ({ id, page: "B", images: [`https://x/${id}.jpg`], start: null });
const video = (id) => ({ id, page: "V", images: [], start: null });
function fakeMeta(pagesList) {
  const calls = [];
  return { calls, fetchPage: async (cursor) => { calls.push(cursor); return pagesList[calls.length - 1]; } };
}

{ // pages until Meta has no next page; videos and repeats skipped; pauses between pages
  const meta = fakeMeta([
    { ads: [img("1"), video("2"), img("3")], nextCursor: "c1" },
    { ads: [img("3"), img("4")], nextCursor: null },
  ]);
  const waits = [];
  const seenPages = [];
  const r = await collectAds({ fetchPage: meta.fetchPage, sleep: async (ms) => waits.push(ms), random: () => 0.5,
    onPage: (fresh, total) => seenPages.push([fresh.map((a) => a.id), total]) });
  assert.deepEqual(r.ads.map((a) => a.id), ["1", "3", "4"]);
  assert.equal(r.stoppedBecause, "last_page");
  assert.deepEqual(meta.calls, [null, "c1"]);
  assert.deepEqual(seenPages, [[["1", "3"], 2], [["4"], 3]]);
  assert.deepEqual(waits, [2500]); // one 2-3 s pause between the two pages
}
{ // an empty page stops the search (the original 1.4.0 behaviour)
  const meta = fakeMeta([{ ads: [img("1")], nextCursor: "c1" }, { ads: [], nextCursor: "c2" }, { ads: [img("9")], nextCursor: null }]);
  const r = await collectAds({ fetchPage: meta.fetchPage, sleep: noWait });
  assert.equal(r.stoppedBecause, "empty_page");
  assert.equal(meta.calls.length, 2);
}
{ // "slow down" is retried with growing waits, then the search stops after 3
  const meta = fakeMeta([{ ads: [img("1")], nextCursor: "c1" }, { rateLimited: true, ads: [] }, { rateLimited: true, ads: [] }, { rateLimited: true, ads: [] }]);
  const waits = [];
  const r = await collectAds({ fetchPage: meta.fetchPage, sleep: async (ms) => waits.push(ms), random: () => 0 });
  assert.equal(r.stoppedBecause, "rate_limited");
  assert.deepEqual(r.ads.map((a) => a.id), ["1"]); // keeps what it has
  assert.deepEqual(waits, [2000, 6000, 11000]);
}
{ // one "slow down" then fine: carries on
  const meta = fakeMeta([{ rateLimited: true, ads: [] }, { ads: [img("1")], nextCursor: null }]);
  const r = await collectAds({ fetchPage: meta.fetchPage, sleep: noWait });
  assert.equal(r.stoppedBecause, "last_page");
  assert.equal(r.ads.length, 1);
}
{ // stops at the image-ad cap, mid-page
  const meta = fakeMeta([{ ads: [img("1"), img("2"), img("3")], nextCursor: "c1" }]);
  const r = await collectAds({ fetchPage: meta.fetchPage, sleep: noWait, maxImageAds: 2 });
  assert.equal(r.stoppedBecause, "max_ads");
  assert.deepEqual(r.ads.map((a) => a.id), ["1", "2"]);
}
{ // Stop button
  let stop = false;
  const meta = fakeMeta([{ ads: [img("1")], nextCursor: "c1" }, { ads: [img("2")], nextCursor: null }]);
  const r = await collectAds({ fetchPage: meta.fetchPage, sleep: async () => { stop = true; }, shouldStop: () => stop });
  assert.equal(r.stoppedBecause, "stopped");
  assert.equal(meta.calls.length, 1);
}
{ // simplifyAd keeps the ad text the detail view will need
  const ad = simplifyAd({ ad_archive_id: 5, snapshot: { page_name: "P", title: "T", body: { text: "Body" }, cta_text: "Shop now",
    images: [{ original_image_url: "https://x/a.jpg" }], videos: [] }, start_date: 1790000000 });
  assert.deepEqual(ad, { id: "5", page: "P", images: ["https://x/a.jpg"], texts: [{ title: "T", body: "Body", cta: "Shop now" }],
    start: 1790000000, title: "T", body: "Body", cta: "Shop now" });
}
{ // a carousel: each card keeps its own text, like the app's records
  const ad = simplifyAd({ ad_archive_id: 6, snapshot: { page_name: "P", title: "Ad title", body: { text: "Ad body" }, cta_text: "Buy",
    cards: [{ title: "Card 1", body: "Hair oil", resized_image_url: "https://x/c1.jpg" }, { title: null, body: { text: "Serum" }, original_image_url: "https://x/c2.jpg", cta_text: "Shop" },
      { title: "No picture" }] } });
  assert.deepEqual(ad.images, ["https://x/c1.jpg", "https://x/c2.jpg"]);
  assert.deepEqual(ad.texts, [{ title: "Card 1", body: "Hair oil", cta: "Buy" }, { title: "", body: "Serum", cta: "Shop" }]);
}
assert.equal(parseSearchResponse(JSON.stringify({ errors: [{ code: 1357004, message: "Please try again" }] })).sessionExpired, true);

console.log("meta.js: all checks passed");
