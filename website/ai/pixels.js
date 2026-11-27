// The picture preparation the laptop app's text reader (calculate_ocr in
// meta_design_scraper_session.py) does with Pillow, rebuilt to give the same
// pixels: enlarge x2, grey, extra contrast, sharpen, auto-contrast and two
// black-and-white versions. Checked pixel by pixel against Pillow 12.3.
import { resizeBicubic } from "./resize.js";

/** Pillow RGB -> L: (R*19595 + G*38470 + B*7471 + 0x8000) >> 16 */
export function toGray(rgb, n) {
  const out = new Uint8Array(n);
  for (let i = 0, p = 0; i < n; i++, p += 3) out[i] = (rgb[p] * 19595 + rgb[p + 1] * 38470 + rgb[p + 2] * 7471 + 0x8000) >> 16;
  return out;
}

/** ImageEnhance.Contrast(gray).enhance(factor): blend with a flat image of the rounded mean. */
export function contrast(gray, factor) {
  let sum = 0;
  for (let i = 0; i < gray.length; i++) sum += gray[i];
  const mean = Math.trunc(sum / gray.length + 0.5);
  const alpha = Math.fround(factor);
  const out = new Uint8Array(gray.length);
  for (let i = 0; i < gray.length; i++) {
    // Pillow Blend.c: float temp = in1 + alpha * (in2 - in1); truncate, clip
    const t = Math.fround(mean + Math.fround(alpha * (gray[i] - mean)));
    out[i] = t <= 0 ? 0 : t >= 255 ? 255 : Math.trunc(t);
  }
  return out;
}

/** ImageFilter.SHARPEN on a grey image (3x3 kernel /16; edge pixels copied as Pillow does). */
export function sharpen(gray, w, h) {
  const out = new Uint8Array(gray);
  const k = [-2, -2, -2, -2, 32, -2, -2, -2, -2].map((v) => Math.fround(v / 16));
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      let ss = 0;
      let i = 0;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) ss = Math.fround(ss + Math.fround(gray[(y + dy) * w + x + dx] * k[i++]));
      }
      out[y * w + x] = ss <= 0 ? 0 : ss >= 255 ? 255 : Math.trunc(ss + 0.5);
    }
  }
  return out;
}

/** ImageOps.autocontrast(gray) with cutoff 0. */
export function autocontrast(gray) {
  let lo = 255, hi = 0;
  for (const v of gray) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (hi <= lo) return new Uint8Array(gray);
  const scale = 255 / (hi - lo);
  const offset = -lo * scale;
  const lut = new Uint8Array(256);
  for (let ix = 0; ix < 256; ix++) {
    const v = Math.trunc(ix * scale + offset);
    lut[ix] = v < 0 ? 0 : v > 255 ? 255 : v;
  }
  return gray.map((v) => lut[v]);
}

export const threshold = (gray, level) => gray.map((v) => (v > level ? 255 : 0));

/** The six copies calculate_ocr reads, in its order. RGB copy first, then five grey ones. */
export function ocrVariants(rgb, width, height) {
  const w = width * 2, h = height * 2;
  const up = resizeBicubic(rgb, width, height, w, h);
  const gray = contrast(toGray(up, w * h), 2.0);
  return {
    width: w, height: h,
    rgb: up,
    grays: [gray, sharpen(gray, w, h), autocontrast(gray), threshold(gray, 170), threshold(gray, 210)],
  };
}

/** visual_complexity: 96x96 bicubic thumbnail, mean of per-channel variance, sqrt / 80, capped at 1. */
export function visualComplexity(rgb, width, height) {
  const small = resizeBicubic(rgb, width, height, 96, 96);
  const n = 96 * 96;
  let variances = 0;
  for (let c = 0; c < 3; c++) {
    let sum = 0, sum2 = 0;
    for (let i = 0; i < n; i++) { const v = small[i * 3 + c]; sum += v; sum2 += v * v; }
    variances += sum2 / n - (sum / n) ** 2; // ImageStat.var
  }
  return Math.min(Math.sqrt(variances / 3) / 80, 1);
}
