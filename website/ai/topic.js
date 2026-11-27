// "Is this ad really about the search?", ported from the laptop app's
// topic_check.py. An ad is off-topic only when BOTH are true:
//  1. CLIP gives "an advertisement for <search>" under 5% against 22 other ad
//     topics (topics in the search's own family don't count against it), and
//  2. the advertiser's own title/body doesn't mention the search word.
// Topic vectors and the side-list categories' search vectors were made by the
// app's own CLIP (export_topic_text.py); typed searches get theirs from CLIP's
// text half in the browser (checked identical to the app).

let data = null;
export async function loadTopics() {
  if (!data) data = await fetch(new URL("./topic_text.json", import.meta.url)).then((r) => r.json());
  return data;
}

const dot = (a, b) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s; };
const normalise = (v) => { const n = Math.hypot(...v); return v.map((x) => x / n); };

/** The key the app uses to look up a side-list category. */
export const categoryKey = (query) => String(query).toLowerCase().split(/\s+/).filter(Boolean).join(" ");

/** The three prompts the app averages for a search. */
export const searchPrompts = (q) => [`an advertisement for ${q}`, `a ${q} advertisement`, `${q} products in an ad`];

/** Words whose presence in the ad's own text keeps it, e.g. haircare -> haircare, hair. */
export function searchWords(query, generic) {
  const words = new Set((String(query).toLowerCase().match(/[a-z]+/g) || []).filter((w) => !generic.has(w) && w.length > 2));
  for (const w of [...words]) {
    for (const suffix of ["care", "s"]) {
      if (w.endsWith(suffix) && w.length > suffix.length + 2) words.add(w.slice(0, -suffix.length));
    }
  }
  return words;
}

/**
 * Everything needed to check a search. queryVector: null for a side-list
 * category (taken from the file), else the mean of the 3 prompt vectors from
 * the browser's CLIP text half (each normalised), like the app.
 */
export function prepareSearch(query, topics, promptVectors = null) {
  if (!String(query || "").trim()) return null;
  const key = categoryKey(query);
  const known = topics.categories[key];
  let queryVec;
  if (known && !promptVectors) queryVec = known.vector;
  else {
    const mean = promptVectors[0].map((_, j) => promptVectors.reduce((s, v) => s + v[j], 0) / promptVectors.length);
    queryVec = normalise(mean);
  }
  let families;
  if (known) families = new Set(known.families);
  else { // the two closest families (erring on keeping ads)
    const order = topics.topics.map((t, i) => [dot(t.vector, queryVec), i]).sort((a, b) => b[0] - a[0]);
    const list = [];
    for (const [, i] of order) {
      const f = topics.topics[i].family;
      if (!list.includes(f)) list.push(f);
      if (list.length === 2) break;
    }
    families = new Set(list);
  }
  return {
    queryVec,
    families,
    others: topics.topics.filter((t) => !families.has(t.family)).map((t) => t.vector),
    words: [...searchWords(query, new Set(topics.generic))],
    below: topics.below,
  };
}

const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Set row.topic_score (0-1) and row.off_topic (0/1), as check_topics does. Rows need clip_embedding, title, body. */
export function checkTopics(rows, prepared) {
  for (const row of rows) {
    row.topic_score = null;
    row.off_topic = 0;
    if (!prepared || !row.clip_embedding?.length) continue;
    const image = normalise(row.clip_embedding);
    const logits = [dot(image, prepared.queryVec), ...prepared.others.map((v) => dot(v, image))].map((x) => x * 100);
    const top = Math.max(...logits);
    const exp = logits.map((l) => Math.exp(l - top));
    const score = exp[0] / exp.reduce((a, b) => a + b, 0);
    row.topic_score = Math.round(score * 1e4) / 1e4;
    const ownText = `${row.title || ""} ${row.body || ""}`.toLowerCase();
    // Python's \b: no letter, digit or _ right before the word
    const mentions = prepared.words.some((w) => new RegExp(`(?<![\\p{L}\\p{N}_])${escape(w)}`, "u").test(ownText));
    row.off_topic = score < prepared.below && !mentions ? 1 : 0;
  }
  return rows;
}
