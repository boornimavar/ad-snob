// Ad Snob website, step 1: search through the helper add-on and show the ads.
// The page never contacts Meta itself: the helper (extension/) does, from the
// visitor's own browser, logged out. Add ?demo=1 to the address to try the
// page with made-up ads when the helper isn't installed (for development).

const CATEGORIES = [
  ["Beauty & personal care", ["skincare", "beauty", "haircare", "makeup", "fragrance"]],
  ["Retail & lifestyle", ["fashion", "food", "home decor", "electronics"]],
  ["Other industries", ["supplements", "fitness", "software", "pet care", "travel"]],
];
const DEMO = new URLSearchParams(location.search).has("demo");
const $ = (id) => document.getElementById(id);
const label = (q) => q.charAt(0).toUpperCase() + q.slice(1);
let searching = false;

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

function helperInstalled() {
  return DEMO || Boolean(document.documentElement.dataset.adsnobHelper);
}

function askHelper(query, country) {
  if (DEMO) return demoSearch(query);
  const id = crypto.randomUUID();
  return new Promise((resolve) => {
    const timer = setTimeout(() => { window.removeEventListener("message", onReply); resolve({ ok: false, error: "The helper took too long (over 2 minutes)." }); }, 120000);
    function onReply(event) {
      if (event.source !== window || event.data?.type !== "adsnob:result" || event.data.id !== id) return;
      clearTimeout(timer);
      window.removeEventListener("message", onReply);
      resolve(event.data.result);
    }
    window.addEventListener("message", onReply);
    window.postMessage({ type: "adsnob:search", id, query, country }, location.origin);
  });
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

function setStatus(kind, text) {
  $("status").className = `status ${kind}`;
  $("status").textContent = text;
}

function renderAds(ads) {
  const grid = $("grid");
  grid.replaceChildren();
  for (const ad of ads.filter((a) => a.images.length)) {
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

async function runSearch() {
  const query = $("q").value.trim();
  if (!query || searching) return;
  if (!helperInstalled()) { $("helperMissing").hidden = false; setStatus("bad", "Install the helper add-on first (steps above)."); return; }
  searching = true;
  $("go").disabled = true;
  document.querySelectorAll("#categories button").forEach((b) => b.classList.toggle("active", b.dataset.query === query.toLowerCase()));
  setStatus("", `Searching Meta for “${query}”… (up to a minute)`);
  $("grid").replaceChildren();
  const started = Date.now();
  const result = await askHelper(query, $("country").value);
  searching = false;
  $("go").disabled = false;
  $("diag").textContent = [...((result.diag || {}).steps || []), `took ${Math.round((Date.now() - started) / 1000)} s`].join("\n");
  if (!result.ok) { setStatus("bad", `Didn't work: ${result.error}`); $("count").textContent = "No results"; return; }
  const withImages = result.ads.filter((a) => a.images.length);
  if (DEMO) setStatus("ok", "Demo mode: made-up ads, Meta wasn't contacted.");
  else setStatus(result.ads.length ? "ok" : "bad", result.ads.length ? `Meta answered with ${result.ads.length} ads.` : "Meta answered, but with 0 ads.");
  $("count").textContent = `${withImages.length} image ad${withImages.length === 1 ? "" : "s"}${DEMO ? " (made-up demo ads)" : ""}`;
  renderAds(result.ads);
}

function demoSearch(query) {
  const colours = ["#e9c46a", "#f4a261", "#2a9d8f", "#e76f51", "#8ab17d", "#b5838d"];
  const ads = colours.map((c, i) => ({
    id: "", page: `Demo brand ${i + 1}`, start: Math.floor(Date.now() / 1000) - i * 40 * 86400,
    images: [`data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="400" height="${400 + (i % 3) * 80}"><rect width="100%" height="100%" fill="${c}"/><text x="50%" y="50%" font-family="sans-serif" font-size="28" text-anchor="middle" fill="#1b1b1b">${query} ad ${i + 1}</text></svg>`)}`],
  }));
  return new Promise((r) => setTimeout(() => r({ ok: true, ads, diag: { steps: ["demo mode: made-up ads, Meta not contacted"] } }), 600));
}

$("searchForm").addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });
renderCategories();
// The helper marks the page as soon as it loads; check once the page is ready.
setTimeout(() => { if (!helperInstalled()) $("helperMissing").hidden = false; }, 800);
