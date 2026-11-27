// Ad Snob website, step 3: search through the helper add-on, then score every
// picture with the app's AI in the browser (website/ai/) and show the best first.
// The page never contacts Meta itself: the helper (extension/) searches and
// fetches the ad pictures, from the visitor's own browser, logged out.
// Add ?demo=1 to try the page with made-up ads when the helper isn't installed.
import { Analyzer, decodeImage } from "./ai/analyzer.js";
import { allSaved, saveAd, unsaveAd } from "./saved.js";
import { loadTopics, prepareSearch, checkTopics, categoryKey, searchPrompts } from "./ai/topic.js";
import { deduplicateRows, summarizeGroups, daysRunning, runningLabel, isLikelyWinner, isNewAd } from "./ai/dedup.js";

const CATEGORIES = [
  ["Beauty & personal care", ["skincare", "beauty", "haircare", "makeup", "fragrance"]],
  ["Retail & lifestyle", ["fashion", "food", "home decor", "electronics"]],
  ["Other industries", ["supplements", "fitness", "software", "pet care", "travel"]],
];
const NEEDS_HELPER = "0.6.0"; // 0.6.0 sends each carousel card's own text (the off-topic check reads it), like the app
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
let showDuplicates = false;
let showOffTopic = false;
let winnersOnly = false;
let regroupTimer = null;
let view = "explore";       // or "saved"
let savedKeys = new Set();  // keys of the ads saved in this browser
let savedItems = [];        // cards in the Saved ads view

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
/** Meta's start time as YYYY-MM-DD, like the app (datetime.fromtimestamp = this computer's local date). */
function startDateOf(start) {
  if (typeof start === "number") {
    const d = new Date(start * 1000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  const m = String(start || "").match(/^\d{4}-\d{2}-\d{2}/);
  return m ? m[0] : "";
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
    scheduleRegroup();
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
  card.className = item.state === "waiting" ? "card scoring" : "card";
  card.style.setProperty("--d", `${(document.querySelectorAll(".card").length % 6) * 70}ms`); // cards rise in one by one
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
  when.className = "when";
  when.textContent = runningLabel(daysRunning(startDateOf(ad.start)));
  meta2.append(when);
  if (ad.id) {
    const link = document.createElement("a");
    link.href = `https://www.facebook.com/ads/library/?id=${encodeURIComponent(ad.id)}`;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "View on Meta ↗";
    meta2.append(link);
  }
  const tag = document.createElement("div");
  tag.className = "tag-slot";
  const bar = document.createElement("div");
  bar.className = "score-bar";
  bar.append(document.createElement("span"));
  body.append(brand, tag, meta, bar, meta2);
  const media = document.createElement("div");
  media.className = "creative-media";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "save-float";
  save.addEventListener("click", () => toggleSave(item));
  media.append(img, save);
  card.append(media, body);
  card.tabIndex = 0;
  card.title = "Click for details";
  card.addEventListener("click", (e) => { if (!e.target.closest("a,button,input")) openDetail(item); });
  card.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === card) openDetail(item); });
  item.card = card;
  refreshSaveButton(item);
  return card;
}

function updateCard(item) {
  const verdict = item.card.querySelector(".verdict");
  const score = item.card.querySelector(".score");
  item.card.classList.toggle("scoring", item.state === "waiting");
  if (item.state === "failed") {
    verdict.textContent = "Couldn't score";
    verdict.title = item.error;
  } else if (item.state === "scored") {
    const r = item.record.rank_score;
    const dup = item.row?.is_creative_representative === 0;
    const off = item.row?.off_topic === 1;
    verdict.textContent = off ? "Off-topic" : dup ? "Duplicate" : r >= DESIGN_LED ? "Design-led" : "Not design-led";
    verdict.title = off ? "Neither the picture nor the ad's text looks like this search (e.g. a hiring poster in “food”)"
      : dup ? "Another version of a design shown elsewhere (a different size or crop)" : "";
    score.textContent = `${Math.round(r * 100)}%`;
    item.card.classList.toggle("design-led", r >= DESIGN_LED);
    item.card.querySelector(".score-bar span").style.width = `${Math.round(r * 100)}%`;
    item.card.classList.toggle("duplicate", dup);
  }
  if (item.row) {
    // same card text as the app: how long the design has run, and in how many separate ads
    const ads = Number(item.row.group_ad_count || 1);
    item.card.querySelector(".when").textContent = [runningLabel(daysRunning(item.row.ad_start_date)), ads > 1 ? `${ads} ads` : ""].filter(Boolean).join(" · ");
    const slot = item.card.querySelector(".tag-slot");
    slot.replaceChildren();
    const tag = document.createElement("span");
    tag.className = "winner-tag";
    if (isLikelyWinner(item.row)) {
      tag.textContent = "🔥 Likely winner";
      tag.title = "Running 3+ months in 3+ separate ads: the brand keeps paying for it (a clue, not proof)";
      slot.append(tag);
    } else if (isNewAd(item.row)) {
      tag.textContent = "🆕 New";
      tag.classList.add("new-tag");
      tag.title = "Started in the last 2 weeks: something they are testing now";
      slot.append(tag);
    }
  }
  applyFilter(item);
  if (item === detailItem) renderDetailInfo(item);
}

function applyFilter(item) {
  if (item.searchId === "saved") { item.card.hidden = false; return; }
  const scored = item.state === "scored";
  item.card.hidden = (designLedOnly && scored && item.record.rank_score < DESIGN_LED)
    || (!showDuplicates && item.row?.is_creative_representative === 0)
    || (!showOffTopic && item.row?.off_topic === 1)
    || (winnersOnly && !(item.row && isLikelyWinner(item.row)));
}

/** Group copies of the same design exactly like the app (ai/dedup.js): every scored picture, in collection order. */
function regroup() {
  clearTimeout(regroupTimer);
  regroupTimer = null;
  const scored = creatives.filter((c) => c.state === "scored");
  for (const c of scored) {
    const text = c.ad.texts?.[c.index] || c.ad; // each carousel card's own text, as the app keeps it
    c.row = { ...c.record, ad_id: c.ad.id, image_file: c.key, ad_start_date: startDateOf(c.ad.start), title: text.title || "", body: text.body || "" };
  }
  const rows = scored.map((c) => c.row);
  deduplicateRows(rows);
  summarizeGroups(rows);
  checkTopics(rows, search?.topic); // until the search's topic is ready, nothing is off-topic
  scored.forEach(updateCard);
}
function scheduleRegroup() {
  if (!regroupTimer) regroupTimer = setTimeout(() => { regroup(); updateProgress(); }, 400);
}

function sortBestFirst() {
  const grid = $("grid");
  const order = [...creatives].sort((a, b) => (b.record?.rank_score ?? -1) - (a.record?.rank_score ?? -1));
  let shown = 0;
  for (const item of order) {
    item.card.style.setProperty("--d", `${Math.min(item.card.hidden ? 0 : shown++, 12) * 45}ms`); // the best ones rise first
    grid.append(item.card);
  }
}

// ---------- the live panel: numbers that count up ----------
const REDUCED_MOTION = matchMedia("(prefers-reduced-motion: reduce)").matches;
function tween(el, to) {
  const from = Number(el.dataset.v || 0);
  if (from === to && el.textContent !== "") return;
  el.dataset.v = to;
  if (to > from) { el.classList.remove("bump"); void el.offsetWidth; el.classList.add("bump"); }
  cancelAnimationFrame(el.raf);
  clearTimeout(el.landing);
  const land = () => { cancelAnimationFrame(el.raf); el.textContent = to.toLocaleString("en-IN"); };
  if (REDUCED_MOTION || document.hidden) return land();
  el.landing = setTimeout(land, 700); // the right number even if the browser skips animation frames
  const start = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - start) / 650);
    el.textContent = Math.round(from + (to - from) * (1 - (1 - k) ** 3)).toLocaleString("en-IN");
    if (k < 1) el.raf = requestAnimationFrame(step);
  };
  el.raf = requestAnimationFrame(step);
}
function setCount(num, label, extra = "") {
  if (!$("countNum")) {
    $("count").replaceChildren();
    const n = document.createElement("span"); n.id = "countNum"; n.className = "count-num";
    const l = document.createElement("span"); l.id = "countLabel";
    const x = document.createElement("span"); x.id = "countExtra"; x.className = "count-extra";
    $("count").append(n, l, x);
  }
  tween($("countNum"), num);
  $("countLabel").textContent = label;
  $("countExtra").textContent = extra;
}
function setLive({ stage, found, scored, designLed, copies, offTopic }) {
  $("live").hidden = view !== "explore";
  $("live").dataset.stage = stage;
  $("liveKicker").textContent = stage === "done" ? (search.endNote?.startsWith("Stopped") ? "Stopped" : "Search finished") : "Live search";
  $("liveTitle").textContent = `“${search.query}” · ${$("country").selectedOptions[0].text}`;
  const order = ["collecting", "scoring", "done"];
  document.querySelectorAll("#live .stage").forEach((el) => {
    const i = order.indexOf(el.dataset.stage), now = order.indexOf(stage);
    el.classList.toggle("active", i === now);
    el.classList.toggle("complete", i < now || stage === "done");
  });
  tween($("statFound"), found);
  tween($("statScored"), scored);
  $("statScoredOf").textContent = found ? `of ${found.toLocaleString("en-IN")} · ${Math.round((100 * scored) / found)}%` : "";
  tween($("statDesign"), designLed);
  tween($("statDups"), copies);
  tween($("statOff"), offTopic);
  $("liveBar").style.width = `${found ? (100 * scored) / found : 0}%`;
}

function updateProgress() {
  const scored = creatives.filter((c) => c.state !== "waiting").length;
  const allScored = scored === creatives.length;
  if (search && !search.collecting && allScored && !search.sorted) regroup(); // the final grouping, over everything
  const unique = creatives.filter((c) => c.state === "scored" && c.row?.is_creative_representative !== 0 && (showOffTopic || c.row?.off_topic !== 1));
  const offTopic = creatives.filter((c) => c.state === "scored" && c.row?.is_creative_representative !== 0 && c.row?.off_topic === 1).length;
  const designLed = unique.filter((c) => c.record.rank_score >= DESIGN_LED).length;
  const copies = creatives.filter((c) => c.state === "scored" && c.row?.is_creative_representative === 0).length;
  const shown = creatives.filter((c) => c.state === "scored" && !c.card.hidden).length;
  const kind = `${winnersOnly ? "likely-winner " : ""}${designLedOnly ? "design-led " : ""}`;
  if (view === "explore") setCount(shown, ` ${kind}creative${shown === 1 ? "" : "s"} shown`, `${DEMO ? " · made-up demo ads" : ""}`);
  if (search) setLive({ stage: search.collecting ? "collecting" : allScored ? "done" : "scoring", found: creatives.length, scored, designLed, copies, offTopic });
  if (search?.collecting) {
    if (creatives.length) setStatus("", "");
  } else if (search && !allScored) {
    setStatus("", "The best ones move to the top when scoring finishes.");
  } else if (search && allScored && !search.sorted) {
    search.sorted = true;
    sortBestFirst();
    const failed = creatives.filter((c) => c.state === "failed").length;
    setStatus(creatives.length ? "ok" : "bad", search.endNote || (creatives.length
      ? `Done: ${unique.length} different designs, best first.${failed ? ` ${failed} picture${failed === 1 ? "" : "s"} couldn't be scored.` : ""}`
        + (designLedOnly && designLed < unique.length ? " Turn off ✳ Design-led to see the rest." : "")
      : "Meta answered, but with 0 image ads. It may be limiting this connection; try again later."));
  }
}

// ---------- detail panel (same content as the app's) ----------
const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00`);
  return isNaN(d) ? iso : d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}
// Plain-English notes from the AI's scores, with the app's cut-offs.
function aiNotes(r) {
  const n = [], num = (k) => Number(r[k]);
  const words = num("ocr_word_count");
  if (num("clip_design_score") >= 0.545) n.push("Polished, designed look");
  if (num("broad_graphic_prob") >= 0.37) n.push("Strong graphic layout");
  if (num("broad_photo_prob") >= 0.24) n.push("Photo-led");
  if (num("broad_product_prob") >= 0.52) n.push("Product-focused");
  if (words > 0 && words <= 8) n.push("Minimal text"); else if (words > 55) n.push("Text-heavy"); else if (words > 8) n.push("Headline + supporting text");
  if (num("ocr_text_density") >= 0.10) n.push("Big, bold text");
  if (r.visual_complexity != null && r.visual_complexity !== "") {
    if (num("visual_complexity") <= 0.54) n.push("Clean, simple"); else if (num("visual_complexity") >= 0.82) n.push("Busy, detailed");
  }
  return n;
}
let detailItem = null;
function openDetail(item) {
  detailItem = item;
  $("detailImg").src = item.url;
  $("detailImg").alt = `Ad by ${item.ad.page || "unknown advertiser"}`;
  renderDetailInfo(item);
  // every version of this design (this one first, then the group's lead, then by score), like the app
  const versions = item.row ? [item, ...creatives.filter((c) => c !== item && c.searchId === item.searchId && c.row?.creative_group_id === item.row.creative_group_id)
    .sort((a, b) => (b.row.is_creative_representative - a.row.is_creative_representative) || (b.record.rank_score - a.record.rank_score))] : [item];
  $("versionsLabel").textContent = versions.length > 1 ? `${versions.length} versions of this design` : "";
  $("detailVersions").replaceChildren(...(versions.length > 1 ? versions.map((v, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = i === 0 ? "active" : "";
    b.title = `${v.ad.page || ""}${v.row?.ad_start_date ? ` · since ${formatDate(v.row.ad_start_date)}` : ""}`;
    const img = document.createElement("img");
    img.src = v.url; img.alt = `Version ${i + 1}`; img.loading = "lazy"; img.referrerPolicy = "no-referrer";
    b.append(img);
    b.addEventListener("click", () => {
      $("detailImg").src = v.url;
      $("detailVersions").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    });
    return b;
  }) : []));
  $("detailBackdrop").classList.add("show");
  document.body.style.overflow = "hidden";
  $("detailClose").focus();
}
function renderDetailInfo(item) {
  const r = { ...(item.record || {}), ...(item.row || {}) };
  const text = item.ad.texts?.[item.index] || item.ad;
  const scored = item.state === "scored";
  const days = daysRunning(r.ad_start_date || startDateOf(item.ad.start));
  const ads = Number(r.group_ad_count || 1);
  const score = Math.round(Number(r.rank_score || 0) * 100);
  const adLink = item.ad.id ? `https://www.facebook.com/ads/library/?id=${encodeURIComponent(item.ad.id)}` : "";
  const notes = scored ? aiNotes(r) : [];
  const tags = [isLikelyWinner(r) ? '<span class="winner-tag">🔥 Likely winner</span>' : "", isNewAd(r) ? '<span class="winner-tag new-tag">🆕 New</span>' : "",
    r.is_creative_representative === 0 ? '<span class="winner-tag new-tag">Duplicate</span>' : "", r.off_topic === 1 ? '<span class="winner-tag new-tag">Off-topic</span>' : ""].join("");
  const copy = [text.title ? `<strong>${escapeHtml(text.title)}</strong>` : "", escapeHtml(text.body || "")].join("");
  const since = r.ad_start_date || startDateOf(item.ad.start);
  $("detailInfo").innerHTML = `<div><div class="detail-brand" id="detailBrand">${escapeHtml(item.ad.page || "Unknown advertiser")}</div><div class="detail-sub">${escapeHtml(item.query || search?.query || "")}</div></div>
  ${tags ? `<div class="detail-tags">${tags}</div>` : ""}
  <div class="detail-facts">
    <div class="fact"><div class="fact-label">Design score</div><div class="fact-value score">${scored ? `${score}%` : item.state === "failed" ? "—" : "Scoring…"}</div></div>
    <div class="fact"><div class="fact-label">Verdict</div><div class="fact-value">${scored ? (score >= 50 ? "Design-led" : "Not design-led") : "—"}</div></div>
    <div class="fact"><div class="fact-label">Running since</div><div class="fact-value">${since ? formatDate(since) : "Unknown"}</div>${days !== null ? `<div class="detail-sub">${runningLabel(days).replace("Running ", "")}</div>` : ""}</div>
    <div class="fact"><div class="fact-label">Used in</div><div class="fact-value">${ads} ad${ads === 1 ? "" : "s"}</div><div class="detail-sub">separate ads with this design</div></div>
  </div>
  ${notes.length ? `<div class="detail-section"><h4>What the AI noticed</h4><div class="notice-chips">${notes.map((x) => `<span>${x}</span>`).join("")}</div></div>` : ""}
  ${copy ? `<div class="detail-section"><h4>Ad text</h4><div class="ad-copy">${copy}</div></div>` : ""}
  ${text.cta ? `<div class="detail-section"><h4>Button</h4><div class="notice-chips"><span>${escapeHtml(text.cta)}</span></div></div>` : ""}
  <div class="score-help">Design score = how designed the ad looks. Likely winner = running 3+ months in 3+ ads, so the brand keeps paying for it (a clue, not proof).</div>
  <div class="detail-actions">${adLink ? `<a class="primary" href="${adLink}" target="_blank" rel="noopener">View on Meta ↗</a>` : ""}<button type="button" id="detailSave">${isSaved(item) ? "★ Saved" : "♧ Save"}</button><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">Open full image ↗</a></div>`;
  $("detailSave").addEventListener("click", () => toggleSave(item));
}
function closeDetail() {
  if (!detailItem) return;
  const card = detailItem.card;
  $("detailBackdrop").classList.remove("show");
  document.body.style.overflow = "";
  detailItem = null;
  card?.focus();
}
$("detailClose").addEventListener("click", closeDetail);
$("detailBackdrop").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeDetail(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDetail(); });

// ---------- saved ads (kept in this browser, with a copy of the picture) ----------
const saveKey = (item) => item.saveKey || item.key;
const isSaved = (item) => savedKeys.has(saveKey(item));

function refreshSaveButton(item) {
  const button = item.card?.querySelector(".save-float");
  if (!button) return;
  const saved = isSaved(item);
  if (button.classList.contains("saved") !== saved && button.textContent) { button.classList.remove("pop"); void button.offsetWidth; button.classList.add("pop"); }
  button.classList.toggle("saved", saved);
  button.textContent = saved ? "★" : "♧";
  button.title = button.ariaLabel = saved ? "Remove from saved ads" : "Save this ad";
}
function refreshSavedEverywhere(key) {
  for (const c of [...creatives, ...savedItems]) if (saveKey(c) === key) refreshSaveButton(c);
  if (detailItem && saveKey(detailItem) === key) renderDetailInfo(detailItem);
  $("savedCount").textContent = savedKeys.size ? String(savedKeys.size) : "";
}

async function toggleSave(item) {
  const key = saveKey(item);
  if (item.saving) return;
  item.saving = true;
  try {
    if (savedKeys.has(key)) {
      await unsaveAd(key);
      savedKeys.delete(key);
      if (view === "saved") {
        const gone = savedItems.find((c) => saveKey(c) === key);
        if (gone === detailItem) closeDetail();
        gone?.card.remove();
        savedItems = savedItems.filter((c) => c !== gone);
        updateSavedCount();
      }
    } else {
      const text = item.ad.texts?.[item.index] || item.ad;
      await saveAd({
        key,
        query: item.query || search?.query || "",
        index: 0,
        ad: { id: item.ad.id, page: item.ad.page, start: item.ad.start, texts: [{ title: text.title || "", body: text.body || "", cta: text.cta || "" }] },
        record: item.record || null,
        row: item.row ? { ...item.row, creative_group_id: null } : null,
        picture: await fetchPicture(item.url),
      });
      savedKeys.add(key);
    }
  } catch (err) {
    setStatus("bad", `Couldn't update saved ads: ${err.message || err}`);
  } finally {
    item.saving = false;
  }
  refreshSavedEverywhere(key);
}

function updateSavedCount() {
  $("count").textContent = savedItems.length
    ? `${savedItems.length} saved creative${savedItems.length === 1 ? "" : "s"} · kept in this browser`
    : "No saved ads yet. Press ♧ on any ad to keep it here (it stays after you close the page).";
}

async function showSaved() {
  view = "saved";
  closeDetail();
  $("exploreBtn").classList.remove("active");
  $("savedBtn").classList.add("active");
  $("grid").hidden = true;
  $("live").hidden = true;
  document.querySelector(".toggles").hidden = true;
  for (const c of savedItems) URL.revokeObjectURL(c.url);
  savedItems = [];
  $("savedGrid").replaceChildren();
  $("savedGrid").hidden = false;
  $("count").textContent = "Loading saved ads…";
  try {
    for (const entry of await allSaved()) {
      const item = {
        key: entry.key, saveKey: entry.key, ad: entry.ad, index: 0, query: entry.query, searchId: "saved",
        url: URL.createObjectURL(entry.picture), record: entry.record, row: entry.row,
        state: entry.record ? "scored" : "unscored",
      };
      savedItems.push(item);
      $("savedGrid").append(makeCard(item));
      if (item.record) updateCard(item); else item.card.querySelector(".verdict").textContent = "Not scored";
    }
  } catch (err) {
    $("count").textContent = `Couldn't open saved ads: ${err.message || err}`;
    return;
  }
  if (view === "saved") updateSavedCount();
}

function showExplore() {
  if (view === "explore") return;
  view = "explore";
  closeDetail();
  $("savedBtn").classList.remove("active");
  $("exploreBtn").classList.add("active");
  $("savedGrid").hidden = true;
  $("grid").hidden = false;
  document.querySelector(".toggles").hidden = false;
  if (search) updateProgress(); else $("count").textContent = "Choose an industry or type a search to begin";
  if (!search) $("live").hidden = true;
}

// ---------- searching ----------
function addAds(ads) {
  const grid = $("grid");
  for (const ad of ads) {
    ad.images.forEach((url, i) => {
      const item = { key: ad.id ? `${ad.id}:${i}` : url, index: i, ad, url, state: "waiting", searchId: search.id };
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
  if (!version) { showHelperBox(); return setStatus("bad", "Add the helper to Chrome first (steps above)."); }
  if (olderThan(version, NEEDS_HELPER)) { showHelperBox(version); return setStatus("bad", `Your helper is out of date (${version}, this site needs ${NEEDS_HELPER}). Update it with the steps above.`); }
  showExplore();
  search = { id: crypto.randomUUID(), collecting: true, query };
  closeDetail();
  creatives = [];
  queue = [];
  $("go").hidden = true;
  $("stop").hidden = false;
  document.querySelectorAll("#categories button").forEach((b) => b.classList.toggle("active", b.dataset.query === query.toLowerCase()));
  $("grid").replaceChildren();
  $("count").textContent = "";
  setStatus("", `Searching Meta for “${query}”… the first ads take about 10–30 seconds.`, true);
  setLive({ stage: "collecting", found: 0, scored: 0, designLed: 0, copies: 0, offTopic: 0 });
  startAI().catch((err) => { $("aiStatus").textContent = `The AI couldn't start: ${err.message || err}`; });
  prepareTopic(query, search);
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
/** The off-topic check for this search: ready-made for side-list categories, CLIP's text half for typed ones. */
async function prepareTopic(query, run) {
  try {
    const topics = await loadTopics();
    if (topics.categories[categoryKey(query)]) run.topic = prepareSearch(query, topics);
    else {
      await startAI();
      if (run !== search) return;
      $("aiStatus").textContent = "Getting the off-topic check ready for this typed search (one-time download, about 127 MB)…";
      run.topic = prepareSearch(query, topics, await analyzer.embedTexts(searchPrompts(query)));
      if (run === search) $("aiStatus").textContent = "";
    }
  } catch (err) {
    if (run === search) $("aiStatus").textContent = `Off-topic check skipped: ${err.message || err}`;
  }
  if (run !== search) return;
  regroup();
  run.sorted = false; // if it already finished, re-sort and rewrite the summary with the off-topic ads hidden
  updateProgress();
}

function toggleOffTopic() {
  showOffTopic = !showOffTopic;
  $("offTopic").classList.toggle("active", showOffTopic);
  creatives.forEach(applyFilter);
  updateProgress();
}
function toggleDuplicates() {
  showDuplicates = !showDuplicates;
  $("dups").classList.toggle("active", showDuplicates);
  creatives.forEach(applyFilter);
  updateProgress();
}
function toggleWinners() {
  winnersOnly = !winnersOnly;
  $("winners").classList.toggle("active", winnersOnly);
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
      return { id: "", page: `Demo brand ${n}`, title: `Made-up ad ${n}`, body: "Made-up ad text for trying the page.", cta: "Shop now", start: Math.floor(Date.now() / 1000) - n * 30 * 86400, images: [url] };
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
$("dups").addEventListener("click", toggleDuplicates);
$("offTopic").addEventListener("click", toggleOffTopic);
$("exploreBtn").addEventListener("click", showExplore);
$("savedBtn").addEventListener("click", showSaved);
allSaved().then((rows) => { savedKeys = new Set(rows.map((r) => r.key)); refreshSavedEverywhere(null); })
  .catch(() => { /* private windows may block storage; saving will say so */ });
$("winners").addEventListener("click", toggleWinners);
renderCategories();
// The helper marks the page as soon as it loads; check once the page is ready.
/** The install box; with `oldVersion`, worded as an update. */
function showHelperBox(oldVersion) {
  $("helperMissing").hidden = false;
  $("helperDownload").href = `downloads/ad-snob-helper.zip?v=${NEEDS_HELPER}`; // never an old cached copy
  if (oldVersion) {
    $("helperTitle").textContent = `Update the helper: you have ${oldVersion}, this site needs ${NEEDS_HELPER}`;
    $("helperUpdate").hidden = false;
  }
}
document.querySelectorAll("button.copy").forEach((b) => b.addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copied ✓"; } catch { b.textContent = "Copy it by hand"; }
  setTimeout(() => { b.textContent = "Copy"; }, 2000);
}));
setTimeout(() => {
  const v = helperVersion();
  if (!v) showHelperBox(); else if (olderThan(v, NEEDS_HELPER)) showHelperBox(v);
}, 800);
