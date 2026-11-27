// The laptop app's design score, rebuilt for the browser. Pure maths: CLIP
// gives a 512-number "fingerprint" per picture and Tesseract gives words; this
// turns them into the same measurements and the same rank_score as
// meta_design_scraper_session.py (analyze_images_batch + rank_score).

const round = (x, digits) => Math.round(x * 10 ** digits) / 10 ** digits;
const dot = (a, b) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s; };
const meanSim = (v, rows) => rows.reduce((s, row) => s + dot(v, row), 0) / rows.length;
const sigmoid = (z) => 1 / (1 + Math.exp(-z));

/** CLIP measurements from a normalised image embedding and the app's exported text features (clip_text.json). */
export function clipMeasurements(embedding, text) {
  const design = meanSim(embedding, text.design);
  const photo = meanSim(embedding, text.photo);
  const product = meanSim(embedding, text.product);
  const logits = text.broad.map((row) => dot(embedding, row) * 25);
  const top = Math.max(...logits);
  const exp = logits.map((l) => Math.exp(l - top));
  const total = exp.reduce((a, b) => a + b, 0);
  const [graphic, photoP, video, productP] = exp.map((e) => e / total);
  return {
    clip_design_score: round(sigmoid((design - photo) * 8), 4),      // predict_clip_design
    clip_product_score: round(sigmoid((product - design) * 10), 4),  // predict_product_photo
    broad_graphic_prob: round(graphic, 4),
    broad_photo_prob: round(photoP, 4),
    broad_video_prob: round(video, 4),
    broad_product_prob: round(productP, 4),
  };
}

/** normalize_ocr_tokens: lower-case words of letters/digits/%+₹$€£, 2+ chars or a digit, sorted, unique. */
export function ocrTokens(text) {
  const words = String(text || "").toLowerCase().replace(/[^a-z0-9%+₹$€£]+/g, " ").split(" ").filter(Boolean);
  return [...new Set(words.filter((t) => t.length >= 2 || /^\d+$/.test(t)))].sort();
}

/**
 * calculate_ocr's choice between the 6 readings. readings: one array of
 * words per reading, each word {text, conf, left, top, width, height} in the
 * enlarged (x2) picture. Keeps words with conf >= 30; best = most words, then
 * most text coverage. Coverage is the share of the ORIGINAL picture inside
 * the union of word boxes.
 */
export function bestReading(readings, width, height, scale = 2) {
  let best = { count: 0, density: 0, text: "" };
  for (const words of readings) {
    const kept = words.filter((w) => w.text.trim() && w.conf >= 30);
    const mask = new Uint8Array(width * height);
    for (const w of kept) {
      const x0 = Math.max(0, Math.floor(w.left / scale)), y0 = Math.max(0, Math.floor(w.top / scale));
      const x1 = Math.min(width, Math.floor((w.left + w.width) / scale)), y1 = Math.min(height, Math.floor((w.top + w.height) / scale));
      for (let y = y0; y < y1; y++) mask.fill(1, y * width + x0, y * width + x1);
    }
    let covered = 0;
    for (const v of mask) covered += v;
    const density = width * height ? covered / (width * height) : 0;
    if (kept.length > best.count || (kept.length === best.count && density > best.density)) {
      best = { count: kept.length, density, text: kept.map((w) => w.text.trim()).join(" ") };
    }
  }
  const ocrScore = 0.65 * Math.min(best.count / 14, 1) + 0.35 * Math.min(best.density / 0.04, 1);
  return {
    ocr_word_count: best.count,
    ocr_text_density: round(best.density, 6),
    ocr_score: round(ocrScore, 4),
    ocr_fingerprint: ocrTokens(best.text).join(" "),
  };
}

/** rank_score with the trained model (rank_model_v1.json): zero words -> 0.05, else the model's probability. */
export function rankScore(record, model) {
  if (Number(record.ocr_word_count) === 0) return 0.05;
  let z = model.intercept;
  model.features.forEach((name, i) => {
    z += ((Number(record[name]) || 0) - model.mean[i]) / model.scale[i] * model.coef[i];
  });
  return round(sigmoid(z), 4);
}
