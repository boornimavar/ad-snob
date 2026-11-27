// Duplicate grouping, ported line by line from the laptop app's
// deduplicate_analyzed_rows + same_underlying_creative + summarize_groups
// (meta_design_scraper_session.py). One difference on purpose: no SIFT
// (keypoint matching), which the browser can't run cheaply. On 290 saved ads
// SIFT changed 1 group of 289; every other rule is the same.
//
// A row: {image_hash, frame_variant_hash: [5 hex], ocr_fingerprint: "a b c",
// rank_score, aspect_ratio, image_width, image_height, creative_score, ad_id,
// image_file, ad_start_date}. Rows must be in collection order, like the app.
import { hashDistance } from "./phash.js";

const CREATIVE_OCR_JACCARD = 0.5;
const CREATIVE_OCR_STRONG_JACCARD = 0.88;

const words = (row) => String(row.ocr_fingerprint || "").split(/\s+/).filter(Boolean);

function stripChars(t, chars) {
  let a = 0, b = t.length;
  while (a < b && chars.includes(t[a])) a++;
  while (b > a && chars.includes(t[b - 1])) b--;
  return t.slice(a, b);
}

export function numericTokens(tokens) {
  const out = new Set();
  for (const t of tokens) {
    const core = stripChars(t, "₹$€£%+");
    if (/^\p{Nd}+$/u.test(core) && core.length >= 2 && core.length <= 4) out.add(core);
  }
  return out;
}

export function numbersCompatible(a, b) {
  for (const x of a) for (const y of b) {
    if (x === y || x.includes(y) || y.includes(x)) return true;
    if (x.length >= 3 && y.length >= 3 && x.slice(-3) === y.slice(-3)) return true;
  }
  return false;
}

export function ocrTokenJaccard(a, b) {
  const aa = new Set(a), bb = new Set(b);
  if (!aa.size || !bb.size) return 0;
  let both = 0;
  for (const t of aa) if (bb.has(t)) both++;
  return both / (aa.size + bb.size - both);
}

export function cropHashDistance(a = [], b = []) {
  let best = 999;
  for (const x of a) for (const y of b) best = Math.min(best, hashDistance(String(x), String(y)));
  return best;
}

/** [same?, reason], the app's rules in the app's order (SIFT = never a match). */
export function sameUnderlyingCreative(a, b) {
  const textA = words(a), textB = words(b);
  const numA = numericTokens(textA), numB = numericTokens(textB);
  if (numA.size && numB.size && !numbersCompatible(numA, numB)) return [false, "numeric mismatch"];
  if (a.image_hash && a.image_hash === b.image_hash) return [true, "exact pHash"];
  const textSimilarity = ocrTokenJaccard(textA, textB);
  const visualDistance = cropHashDistance(a.frame_variant_hash, b.frame_variant_hash);
  if (textA.length >= 3 && textB.length >= 3 && textSimilarity >= CREATIVE_OCR_JACCARD) {
    if (visualDistance <= 8 && textSimilarity >= CREATIVE_OCR_STRONG_JACCARD) {
      return [true, `crop pHash ${visualDistance} + OCR ${textSimilarity.toFixed(2)}`];
    }
    return [false, "same-ish text, pictures too different"];
  }
  if (visualDistance <= 6) return [true, `crop pHash ${visualDistance} (visual match, low/no OCR overlap)`];
  return [false, "different"];
}

export function isBetterFrame(candidate, current) {
  const cs = Number(candidate.rank_score || 0), rs = Number(current.rank_score || 0);
  if (Math.abs(cs - rs) > 0.03) return cs > rs;
  const cr = Number(candidate.aspect_ratio || 0), rr = Number(current.aspect_ratio || 0);
  const cd = cr ? Math.abs(cr - 1) : 999, rd = rr ? Math.abs(rr - 1) : 999;
  if (cd !== rd) return cd < rd;
  const cp = (candidate.image_width || 0) * (candidate.image_height || 0);
  const rp = (current.image_width || 0) * (current.image_height || 0);
  if (cp !== rp) return cp > rp;
  return Number(candidate.creative_score || 0) > Number(current.creative_score || 0);
}

/**
 * Group duplicates. Sets on every row: creative_group_id, duplicate,
 * is_creative_representative, creative_variant_of, creative_variant_count,
 * representative_reason. Returns {representatives, duplicateCount}.
 */
export function deduplicateRows(rows) {
  const reps = [];
  const groups = new Map(); // group id -> {repIdx, variants: [[row, reason]]}
  const hashIndex = new Map(), tokenIndex = new Map(), cropIndex = new Map();
  const textPoor = [];
  let duplicateCount = 0, counter = 0;

  const rowTokens = (row) => new Set(words(row));
  const prefixes = (row) => new Set((row.frame_variant_hash || []).filter(Boolean).map((h) => String(h).slice(0, 4)));
  const add = (map, key, idx) => { if (!map.has(key)) map.set(key, new Set()); map.get(key).add(idx); };
  function register(idx, row) {
    if (row.image_hash && !hashIndex.has(row.image_hash)) hashIndex.set(row.image_hash, idx);
    const toks = rowTokens(row);
    if (toks.size >= 3) for (const t of toks) add(tokenIndex, t, idx);
    else textPoor.push(idx);
    for (const p of prefixes(row)) add(cropIndex, p, idx);
  }

  for (const row of rows) {
    row.duplicate = 0;
    const cand = new Set();
    if (row.image_hash && hashIndex.has(row.image_hash)) cand.add(hashIndex.get(row.image_hash));
    for (const p of prefixes(row)) for (const i of cropIndex.get(p) || []) cand.add(i);
    const toks = rowTokens(row);
    if (toks.size >= 3) { for (const t of toks) for (const i of tokenIndex.get(t) || []) cand.add(i); } else for (const i of textPoor) cand.add(i);

    const exact = row.image_hash && hashIndex.has(row.image_hash) ? hashIndex.get(row.image_hash) : null;
    const ordered = (exact !== null ? [exact] : []).concat([...cand].filter((i) => i !== exact).sort((x, y) => x - y));

    let matched = null, reason = "";
    for (const idx of ordered) {
      const [same, why] = sameUnderlyingCreative(row, reps[idx]);
      if (same) { matched = idx; reason = why; break; }
    }
    if (matched === null) {
      const idx = reps.length;
      reps.push(row);
      row.creative_group_id = ++counter;
      groups.set(counter, { repIdx: idx, variants: [] });
      register(idx, row);
      continue;
    }
    const current = reps[matched];
    const gid = current.creative_group_id;
    duplicateCount++;
    row.creative_group_id = gid;
    row.duplicate = 1;
    if (isBetterFrame(row, current)) {
      current.duplicate = 1;
      row.duplicate = 0;
      reps[matched] = row;
      groups.get(gid).variants.push([current, reason]);
      register(matched, row);
    } else {
      groups.get(gid).variants.push([row, reason]);
    }
  }

  // Merge groups whose leads are the same picture and pass the same rules.
  const leadsByHash = new Map();
  for (let gid of [...groups.keys()]) {
    const lead = reps[groups.get(gid).repIdx];
    if (!lead.image_hash) continue;
    if (!leadsByHash.has(lead.image_hash)) leadsByHash.set(lead.image_hash, []);
    const samePicture = leadsByHash.get(lead.image_hash);
    let keep = samePicture.find((g) => sameUnderlyingCreative(lead, reps[groups.get(g).repIdx])[0]);
    if (keep === undefined) { samePicture.push(gid); continue; }
    if (isBetterFrame(lead, reps[groups.get(keep).repIdx])) {
      samePicture[samePicture.indexOf(keep)] = gid;
      [keep, gid] = [gid, keep];
    }
    const merged = groups.get(gid);
    groups.delete(gid);
    const moved = [[reps[merged.repIdx], "exact pHash"], ...merged.variants];
    for (const [m] of moved) { m.creative_group_id = keep; m.duplicate = 1; }
    groups.get(keep).variants.push(...moved);
    duplicateCount++;
  }

  const representatives = [];
  for (const g of groups.values()) {
    const rep = reps[g.repIdx];
    rep.is_creative_representative = 1;
    rep.creative_variant_of = "";
    rep.creative_variant_count = g.variants.length;
    rep.representative_reason = g.variants.length ? "best-scoring version of this design" : "unique creative (no variants found)";
    for (const [v, why] of g.variants) {
      v.is_creative_representative = 0;
      v.creative_variant_of = rep.image_file;
      v.creative_variant_count = 0;
      v.representative_reason = why;
    }
    representatives.push(rep);
  }
  return { representatives, duplicateCount };
}

/** The app's summarize_groups: separate ads per design, and the lead gets the earliest start date. */
export function summarizeGroups(rows) {
  const groups = new Map();
  for (const r of rows) {
    const key = r.creative_group_id || r.image_hash;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  for (const members of groups.values()) {
    const adCount = new Set(members.map((m) => m.ad_id).filter(Boolean)).size || 1;
    const dates = members.map((m) => m.ad_start_date).filter(Boolean).sort();
    for (const m of members) {
      m.group_ad_count = adCount;
      if (dates.length && (m.is_creative_representative ?? 1)) m.ad_start_date = dates[0];
    }
  }
}

// The dashboard's tags (dashboard.html): running 3+ months in 3+ separate ads, or started in the last 2 weeks.
export const WINNER_DAYS = 90, WINNER_ADS = 3, NEW_DAYS = 14;
export function daysRunning(value, now = Date.now()) {
  if (!value) return null;
  const days = Math.floor((now - new Date(String(value).replace(" ", "T")).getTime()) / 86400000);
  return days >= 0 ? days : null;
}
export function isLikelyWinner(r, now) {
  const d = daysRunning(r.ad_start_date, now);
  return d !== null && d >= WINNER_DAYS && Number(r.group_ad_count || 1) >= WINNER_ADS;
}
export function isNewAd(r, now) {
  const d = daysRunning(r.ad_start_date, now);
  return d !== null && d <= NEW_DAYS;
}
export function runningLabel(days) {
  if (days === null) return "";
  if (days === 0) return "Started today";
  if (days < 60) return `Running ${days}d`;
  if (days < 730) return `Running ${Math.floor(days / 30)} mo`;
  return `Running ${Math.floor(days / 365)} yrs`;
}
