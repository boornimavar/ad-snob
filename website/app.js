// Ad Snob website, step 2: full searches through the helper add-on, with ads
// appearing page by page, a live count and a Stop button.
// The page never contacts Meta itself: the helper (extension/) does, from the
// visitor's own browser, logged out. Add ?demo=1 to the address to try the
// page with made-up ads when the helper isn't installed (for development).

const CATEGORIES = [
  ["Beauty & personal care", ["skincare", "beauty", "haircare", "makeup", "fragrance"]],
  ["Retail & lifestyle", ["fashion", "food", "home decor", "electronics"]],
  ["Other industries", ["supplements", "fitness", "software", "pet care", "travel"]],
];
const NEEDS_HELPER = "0.3.0"; // the first version that pages through results
const DEMO = new URLSearchParams(location.search).has("demo");
const $ = (id) => document.getElementById(id);
const label = (q) => q.charAt(0).toUpperCase() + q.slice(1);
let current = null; // {id, total}

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

const helperVersion = () => (DEMO ? NEEDS_HELPER : document.documentElement.dataset.adsnobHelper || "");
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

function addCards(ads) {
  const grid = $("grid");
  for (const ad of ads) {
    const card = document.createElement("article");
    card.className = "card";
    const img = document.createElement("img");
    img.src = ad.images[0];
    img.alt = `Ad by ${ad.page || "unknown advertiser"}`;
    img.loading = "lazy";
    const body = document.createElement("div");
    body.className = "card-body";
    const brand = document.createElement("div");
    brand.className = "brand-line";
    brand.textContent = ad.page || "Unknown advertiser";
    const meta = document.createElement("div");
    meta.className = "meta";
    const when = document.createElement("span");
    when.textContent = runningFor(ad.start);
    meta.append(when);
    if (ad.id) {
      const link = document.createElement("a");
      link.href = `https://www.facebook.com/ads/library/?id=${encodeURIComponent(ad.id)}`;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "View on Meta ↗";
      meta.append(link);
    }
    body.append(brand, meta);
    card.append(img, body);
    grid.append(card);
  }
}

function showCount(total, done) {
  $("count").textContent = `${total} image ad${total === 1 ? "" : "s"}${done ? "" : " so far"}${DEMO ? " (made-up demo ads)" : ""}`;
}

function finish(result) {
  const total = current ? current.total : 0;
  current = null;
  $("go").hidden = false;
  $("stop").hidden = true;
  $("diag").textContent = ((result.diag || {}).steps || []).join("\n") || "No details.";
  showCount(total, true);
  if (result.stoppedBecause === "stopped") return setStatus("", `Stopped. Showing the ${total} ads found so far.`);
  if (!result.ok) return setStatus("bad", `Didn't work: ${result.error}`);
  if (result.warning) return setStatus("bad", result.warning);
  if (DEMO) return setStatus("ok", "Demo mode: made-up ads, Meta wasn't contacted.");
  setStatus(total ? "ok" : "bad", total ? `Done: ${total} image ads from ${result.pages} pages.` : "Meta answered, but with 0 image ads. It may be limiting this connection; try again later.");
}

window.addEventListener("message", (event) => {
  if (event.source !== window || !current || event.data?.id !== current.id) return;
  if (event.data.type === "adsnob:progress") {
    current.total = event.data.total;
    addCards(event.data.ads);
    showCount(current.total, false);
    setStatus("", `Collecting… ${current.total} image ads so far. Ads appear as they arrive.`, true);
  }
  if (event.data.type === "adsnob:result") finish(event.data.result);
});

function runSearch() {
  const query = $("q").value.trim();
  if (!query || current) return;
  const version = helperVersion();
  if (!version) { $("helperMissing").hidden = false; return setStatus("bad", "Install the helper add-on first (steps above)."); }
  if (olderThan(version, NEEDS_HELPER)) return setStatus("bad", `Your helper add-on is out of date (${version}). Open chrome://extensions, click ↻ on Ad Snob helper, then reload this page.`);
  current = { id: crypto.randomUUID(), total: 0 };
  $("go").hidden = true;
  $("stop").hidden = false;
  document.querySelectorAll("#categories button").forEach((b) => b.classList.toggle("active", b.dataset.query === query.toLowerCase()));
  $("grid").replaceChildren();
  showCount(0, false);
  setStatus("", `Searching Meta for “${query}”… the first ads take about 10–30 seconds.`, true);
  if (DEMO) return demoSearch(query, current.id);
  window.postMessage({ type: "adsnob:search", id: current.id, query, country: $("country").value }, location.origin);
}

function stopSearch() {
  if (!current) return;
  if (DEMO) { current.stopped = true; return; }
  window.postMessage({ type: "adsnob:stop", id: current.id }, location.origin);
}

async function demoSearch(query, id) {
  const colours = ["#e9c46a", "#f4a261", "#2a9d8f", "#e76f51", "#8ab17d", "#b5838d"];
  const run = current;
  let total = 0;
  for (let page = 0; page < 3; page++) {
    await new Promise((r) => setTimeout(r, 700));
    if (run.stopped) return window.postMessage({ type: "adsnob:result", id, result: { ok: true, stoppedBecause: "stopped", diag: { steps: ["demo: stopped"] } } }, location.origin);
    const ads = colours.map((c, i) => ({
      id: "", page: `Demo brand ${page * 6 + i + 1}`, start: Math.floor(Date.now() / 1000) - (page * 6 + i) * 30 * 86400,
      images: [`data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="400" height="${400 + (i % 3) * 80}"><rect width="100%" height="100%" fill="${c}"/><text x="50%" y="50%" font-family="sans-serif" font-size="26" text-anchor="middle" fill="#1b1b1b">${query} ad ${page * 6 + i + 1}</text></svg>`)}`],
    }));
    total += ads.length;
    window.postMessage({ type: "adsnob:progress", id, ads, total }, location.origin);
  }
  window.postMessage({ type: "adsnob:result", id, result: { ok: true, pages: 3, stoppedBecause: "last_page", diag: { steps: ["demo mode: made-up ads, Meta not contacted"] } } }, location.origin);
}

$("searchForm").addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });
$("stop").addEventListener("click", stopSearch);
renderCategories();
// The helper marks the page as soon as it loads; check once the page is ready.
setTimeout(() => { if (!helperVersion()) $("helperMissing").hidden = false; }, 800);
