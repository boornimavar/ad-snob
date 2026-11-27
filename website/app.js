// Ad Snob website, step 3: search through the helper add-on, then score every
// picture with the app's AI in the browser (website/ai/) and show the best first.
// The page never contacts Meta itself: the helper (extension/) searches and
// fetches the ad pictures, from the visitor's own browser, logged out.
// Add ?demo=1 to try the page with made-up ads when the helper isn't installed.
import { Analyzer, decodeImage } from "./ai/analyzer.js";

const CATEGORIES = [
  ["Beauty & personal care", ["skincare", "beauty", "haircare", "makeup", "fragrance"]],
  ["Retail & lifestyle", ["fashion", "food", "home decor", "electronics"]],
  ["Other industries", ["supplements", "fitness", "software", "pet care", "travel"]],
];
const NEEDS_HELPER = "0.5.0"; // picks ad pictures exactly like the app (0.4.0 took different ones)
const DESIGN_LED = 0.5;       // same line as the app's Design-led filter
const SCORING_LANES = 3;      // pictures scored at the same time
const DEMO = new URLSearchParams(location.search).has("demo");
const $ = (id) => document.getElementById(id);
const label = (q) => q.charAt(0).toUpperCase() + q.slice(1);

let analyzer = null;
let search = null;        // {id, collecting, total}
let creatives = [];       // one per picture: {key, ad, url, card, record, state}
let queue = [];
let lanesBusy = 0;
let designLedOnly = true;

// ---------- helpers ----------
function helperVersion() {
  return DEMO ? NEEDS_HELPER : document.documentElement.dataset.adsnobHelper || "";
}
function olderThan(a, b) {
  const x = a.split(".").map(Number);
  const y = b.split(".").map(Number);
  for (let i = 0; i < 3; i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) < (y[i] || 0);
  return false;
}
function setStatus(kind, text, spinning = false) {
  $("status").className = `status ${kind}`;
  $("status").replaceChildren();
  if (spinning) { const dot = document.createElement("span"); dot.className = "spinner"; $("status").append(dot); }
  $("status").append(text);
}
function runningFor(start) {
  if (!start) return "";
  const ms = typeof start === "number" ? start * 1000 : Date.parse(start);
  if (!ms) return "";
  const days = Math.max(0, Math.floor((Date.now() - ms) / 86400000));
  if (days === 0) return "Started today";
  if (days < 60) return `Running ${days}d`;
  if (days < 730) return `Running ${Math.floor(days / 30)} mo`;
  return `Running ${Math.floor(days / 365)} yrs`;
}

/** An ad picture as a Blob, through the helper (websites can't read Facebook's pictures directly). */
function fetchPicture(url) {
  if (DEMO || url.startsWith("blob:")) return fetch(url).then((r) => r.blob());
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { window.removeEventListener("message", onReply); reject(new Error("The helper took too long to fetch the picture.")); }, 60000);
    function onReply(event) {
      if (event.source !== window || event.data?.type !== "adsnob:image-result" || event.data.id !== id) return;
      clearTimeout(timer);
      window.removeEventListener("message", onReply);
      if (!event.data.ok) return reject(new Error(event.data.error || "Couldn't fetch the picture."));
      const bytes = Uint8Array.from(atob(event.data.base64), (c) => c.charCodeAt(0));
      resolve(new Blob([bytes], { type: event.data.imageType || "image/jpeg" }));
    }
    window.addEventListener("message", onReply);
    window.postMessage({ type: "adsnob:image", id, url }, location.origin);
  });
}

// ---------- the AI ----------
function startAI() {
  if (analyzer) return analyzer.ready;
  analyzer = new Analyzer();
  return analyzer.init((loaded, total) => {
    const pct = Math.round((100 * loaded) / total);
    $("aiStatus").textContent = `Getting the AI ready (one-time download, ${Math.round(total / 1e6)} MB): ${pct}%`;
  }).then(() => { $("aiStatus").textContent = ""; });
}

async function scoreNext() {
  if (lanesBusy >= SCORING_LANES || !queue.length) return;
  const item = queue.shift();
  if (item.searchId !== search?.id) return scoreNext(); // from an older search
  lanesBusy++;
  try {
    await startAI();
    const picture = await decodeImage(await fetchPicture(item.url));
    item.record = await analyzer.analyze(picture.rgb, picture.width, picture.height);
    item.state = "scored";
  } catch (err) {
    item.state = "failed";
    item.error = String(err.message || err);
  } finally {
    lanesBusy--;
  }
  if (item.searchId === search?.id) {
    updateCard(item);
    updateProgress();
  }
  scoreNext();
}

function enqueue(item) {
  queue.push(item);
  for (let i = 0; i < SCORING_LANES; i++) scoreNext();
}

// ---------- cards ----------
function makeCard(item) {
  const { ad } = item;
  const card = document.createElement("article");
  card.className = "card";
  const img = document.createElement("img");
  img.src = item.url;
  img.alt = `Ad by ${ad.page || "unknown advertiser"}`;
  img.loading = "lazy";
  img.referrerPolicy = "no-referrer";
  const body = document.createElement("div");
  body.className = "card-body";
  const brand = document.createElement("div");
  brand.className = "brand-line";
  brand.textContent = ad.page || "Unknown advertiser";
  const meta = document.createElement("div");
  meta.className = "meta";
  const verdict = document.createElement("span");
  verdict.className = "verdict";
  verdict.textContent = "Scoring…";
  const score = document.createElement("span");
  score.className = "score";
  meta.append(verdict, score);
  const meta2 = document.createElement("div");
  meta2.className = "meta";
  const when = document.createElement("span");
  when.textContent = runningFor(ad.start);
  meta2.append(when);
  if (ad.id) {
    const link = document.createElement("a");
    link.href = `https://www.facebook.com/ads/library/?id=${encodeURIComponent(ad.id)}`;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "View on Meta ↗";
    meta2.append(link);
  }
  body.append(brand, meta, meta2);
  card.append(img, body);
  item.card = card;
  return card;
}

function updateCard(item) {
  const verdict = item.card.querySelector(".verdict");
  const score = item.card.querySelector(".score");
  if (item.state === "failed") {
    verdict.textContent = "Couldn't score";
    verdict.title = item.error;
  } else if (item.state === "scored") {
    const r = item.record.rank_score;
    verdict.textContent = r >= DESIGN_LED ? "Design-led" : "Not design-led";
    score.textContent = `${Math.round(r * 100)}%`;
    item.card.classList.toggle("design-led", r >= DESIGN_LED);
  }
  applyFilter(item);
}

function applyFilter(item) {
  const hide = designLedOnly && item.state === "scored" && item.record.rank_score < DESIGN_LED;
  item.card.hidden = hide;
}

function sortBestFirst() {
  const grid = $("grid");
  const order = [...creatives].sort((a, b) => (b.record?.rank_score ?? -1) - (a.record?.rank_score ?? -1));
  for (const item of order) grid.append(item.card);
}

function updateProgress() {
  const scored = creatives.filter((c) => c.state !== "waiting").length;
  const designLed = creatives.filter((c) => c.state === "scored" && c.record.rank_score >= DESIGN_LED).length;
  const shown = designLedOnly ? designLed : creatives.length;
  $("count").textContent = `${shown} ${designLedOnly ? "design-led " : ""}creative${shown === 1 ? "" : "s"}`
    + `${designLedOnly ? ` (of ${scored} scored)` : ""}${DEMO ? " · made-up demo ads" : ""}`;
  const allScored = scored === creatives.length;
  if (search?.collecting) {
    setStatus("", `Collecting ads… ${creatives.length} pictures so far, ${scored} scored.`, true);
  } else if (search && !allScored) {
    setStatus("", `Scoring pictures… ${scored} of ${creatives.length} done. The best ones move to the top when it finishes.`, true);
  } else if (search && allScored && !search.sorted) {
    search.sorted = true;
    sortBestFirst();
    const failed = creatives.filter((c) => c.state === "failed").length;
    setStatus(creatives.length ? "ok" : "bad", search.endNote || (creatives.length
      ? `Done: ${creatives.length} pictures scored, ${designLed} design-led, best first.${failed ? ` ${failed} couldn't be scored.` : ""}`
        + (designLedOnly && designLed < creatives.length ? " Turn off ✳ Design-led to see the rest." : "")
      : "Meta answered, but with 0 image ads. It may be limiting this connection; try again later."));
  }
}

// ---------- searching ----------
function addAds(ads) {
  const grid = $("grid");
  for (const ad of ads) {
    ad.images.forEach((url, i) => {
      const item = { key: `${ad.id}:${i}`, ad, url, state: "waiting", searchId: search.id };
      creatives.push(item);
      grid.append(makeCard(item));
      enqueue(item);
    });
  }
  updateProgress();
}

function finishCollecting(result) {
  if (!search) return;
  search.collecting = false;
  $("go").hidden = false;
  $("stop").hidden = true;
  $("diag").textContent = ((result.diag || {}).steps || []).join("\n") || "No details.";
  if (!result.ok && !creatives.length) { setStatus("bad", `Didn't work: ${result.error}`); return; }
  if (result.warning) search.endNote = result.warning;
  if (result.stoppedBecause === "stopped") search.endNote = `Stopped. ${creatives.length} pictures collected; scores shown, best first.`;
  updateProgress();
}

window.addEventListener("message", (event) => {
  if (event.source !== window || !search || event.data?.id !== search.id) return;
  if (event.data.type === "adsnob:progress") addAds(event.data.ads);
  if (event.data.type === "adsnob:result") finishCollecting(event.data.result);
});

function runSearch() {
  const query = $("q").value.trim();
  if (!query || search?.collecting) return;
  const version = helperVersion();
  if (!version) { $("helperMissing").hidden = false; return setStatus("bad", "Install the helper add-on first (steps above)."); }
  if (olderThan(version, NEEDS_HELPER)) return setStatus("bad", `Your helper add-on is out of date (${version}). Open chrome://extensions, click ↻ on Ad Snob helper, then reload this page.`);
  search = { id: crypto.randomUUID(), collecting: true };
  creatives = [];
  queue = [];
  $("go").hidden = true;
  $("stop").hidden = false;
  document.querySelectorAll("#categories button").forEach((b) => b.classList.toggle("active", b.dataset.query === query.toLowerCase()));
  $("grid").replaceChildren();
  $("count").textContent = "";
  setStatus("", `Searching Meta for “${query}”… the first ads take about 10–30 seconds.`, true);
  startAI().catch((err) => { $("aiStatus").textContent = `The AI couldn't start: ${err.message || err}`; });
  if (DEMO) return demoSearch(query, search.id);
  window.postMessage({ type: "adsnob:search", id: search.id, query, country: $("country").value }, location.origin);
}

function stopSearch() {
  if (!search?.collecting) return;
  if (DEMO) { search.stopped = true; return; }
  window.postMessage({ type: "adsnob:stop", id: search.id }, location.origin);
}

function toggleDesignLed() {
  designLedOnly = !designLedOnly;
  $("designLed").classList.toggle("active", designLedOnly);
  creatives.forEach(applyFilter);
  updateProgress();
}

async function demoSearch(query, id) {
  const colours = ["#e9c46a", "#f4a261", "#2a9d8f", "#e76f51", "#8ab17d", "#b5838d"];
  const run = search;
  for (let page = 0; page < 2; page++) {
    await new Promise((r) => setTimeout(r, 700));
    if (run.stopped) return window.postMessage({ type: "adsnob:result", id, result: { ok: true, stoppedBecause: "stopped", diag: { steps: ["demo: stopped"] } } }, location.origin);
    const ads = await Promise.all(colours.map(async (c, i) => {
      const n = page * 6 + i + 1;
      const canvas = new OffscreenCanvas(480, 480);
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = c;
      ctx.fillRect(0, 0, 480, 480);
      if (i % 2) { // every other made-up ad has ad text, so the scores differ
        ctx.fillStyle = "#1b1b1b";
        ctx.textAlign = "center";
        ctx.font = "700 44px sans-serif";
        ctx.fillText(`FLAT ${10 * n}% OFF`, 240, 200);
        ctx.font = "26px sans-serif";
        ctx.fillText(`${query} sale this week`, 240, 290);
      }
      const url = URL.createObjectURL(await canvas.convertToBlob({ type: "image/png" }));
      return { id: "", page: `Demo brand ${n}`, start: Math.floor(Date.now() / 1000) - n * 30 * 86400, images: [url] };
    }));
    window.postMessage({ type: "adsnob:progress", id, ads, total: (page + 1) * 6 }, location.origin);
  }
  window.postMessage({ type: "adsnob:result", id, result: { ok: true, pages: 2, stoppedBecause: "last_page", diag: { steps: ["demo mode: made-up ads, Meta not contacted"] } } }, location.origin);
}

// ---------- categories + start ----------
function renderCategories() {
  const nav = $("categories");
  for (const [group, words] of CATEGORIES) {
    const heading = document.createElement("div");
    heading.className = "group-label";
    heading.textContent = group;
    nav.append(heading);
    for (const word of words) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.query = word;
      button.textContent = label(word);
      button.addEventListener("click", () => { $("q").value = word; runSearch(); });
      nav.append(button);
    }
  }
}

$("searchForm").addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });
$("stop").addEventListener("click", stopSearch);
$("designLed").addEventListener("click", toggleDesignLed);
renderCategories();
// The helper marks the page as soon as it loads; check once the page is ready.
setTimeout(() => { if (!helperVersion()) $("helperMissing").hidden = false; }, 800);
