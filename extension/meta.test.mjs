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

console.log("meta.js: all checks passed");
