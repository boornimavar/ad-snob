// Picture shrinking that matches Pillow's Image.resize(..., BICUBIC), which the
// laptop app's CLIP preprocessing uses (torchvision Resize on a PIL image).
// Browsers' own canvas scaling gives slightly different pixels, and that alone
// moved CLIP's answers (0.97 instead of 1.00 match on real ads). Same pixels,
// same answers: with this, browser CLIP matches the app exactly.
//
// A port of Pillow's ImagingResample (libImaging/Resample.c): a horizontal
// pass then a vertical pass, each a convolution with the bicubic kernel
// (a = -0.5) stretched by the scale when shrinking, using fixed-point
// coefficients and rounding/clipping to 0-255 after each pass, as Pillow does.

const PRECISION_BITS = 32 - 8 - 2;

function bicubic(x) {
  const a = -0.5;
  x = Math.abs(x);
  if (x < 1) return ((a + 2) * x - (a + 3)) * x * x + 1;
  if (x < 2) return (((x - 5) * x + 8) * x - 4) * a;
  return 0;
}

const sinc = (x) => { if (x === 0) return 1; x *= Math.PI; return Math.sin(x) / x; };
const lanczos = (x) => (x >= -3 && x < 3 ? sinc(x) * sinc(x / 3) : 0);

// Pillow's filters: kernel + support (Resample.c).
export const FILTERS = { bicubic: { kernel: bicubic, support: 2 }, lanczos: { kernel: lanczos, support: 3 } };

/** Pillow's precompute_coeffs: per output pixel, the first input index and integer weights. */
function coefficients(inSize, outSize, offset = 0, span = inSize, filter = FILTERS.bicubic) {
  const scale = span / outSize;
  const filterscale = Math.max(scale, 1);
  const support = filter.support * filterscale;
  const ksize = Math.ceil(support) * 2 + 1;
  const bounds = new Int32Array(outSize * 2);
  const kk = new Int32Array(outSize * ksize);
  for (let xx = 0; xx < outSize; xx++) {
    const center = offset + (xx + 0.5) * scale;
    const ss = 1 / filterscale;
    let xmin = Math.trunc(center - support + 0.5);
    if (xmin < 0) xmin = 0;
    let xmax = Math.trunc(center + support + 0.5);
    if (xmax > inSize) xmax = inSize;
    xmax -= xmin;
    const k = new Float64Array(xmax);
    let ww = 0;
    for (let x = 0; x < xmax; x++) {
      const w = filter.kernel((x + xmin - center + 0.5) * ss);
      k[x] = w;
      ww += w;
    }
    for (let x = 0; x < xmax; x++) {
      const v = ww !== 0 ? k[x] / ww : k[x];
      // normalize_coeffs_8bpc: round half away from zero
      kk[xx * ksize + x] = v < 0 ? Math.trunc(-0.5 + v * (1 << PRECISION_BITS)) : Math.trunc(0.5 + v * (1 << PRECISION_BITS));
    }
    bounds[xx * 2] = xmin;
    bounds[xx * 2 + 1] = xmax;
  }
  return { bounds, kk, ksize };
}

const clip8 = (v) => {
  const x = v >> PRECISION_BITS; // the accumulator starts at 1 << (PRECISION_BITS - 1), so this rounds
  return x < 0 ? 0 : x > 255 ? 255 : x;
};

/**
 * Resize pixels like Pillow's Image.resize (default BICUBIC). channels = 3
 * (RGB) or 1 (grey). box = [x0, y0, x1, y1] in source pixels (floats allowed,
 * as ImageOps.fit uses), default the whole image.
 */
export function resizePixels(src, width, height, outW, outH, { box = [0, 0, width, height], channels = 3, filter = FILTERS.bicubic } = {}) {
  const [bx0, by0, bx1, by1] = box;
  const horiz = coefficients(width, outW, bx0, bx1 - bx0, filter);
  const vert = coefficients(height, outH, by0, by1 - by0, filter);
  // Rows the vertical pass will need (Pillow trims the horizontal pass the same way).
  const ybFirst = vert.bounds[0];
  const ybLast = vert.bounds[(outH - 1) * 2] + vert.bounds[(outH - 1) * 2 + 1];
  const rows = ybLast - ybFirst;
  const tmp = new Uint8Array(outW * rows * channels);
  const half = 1 << (PRECISION_BITS - 1);
  const acc = new Float64Array(channels);
  for (let y = 0; y < rows; y++) {
    const srcRow = (y + ybFirst) * width * channels;
    for (let xx = 0; xx < outW; xx++) {
      const xmin = horiz.bounds[xx * 2];
      const xmax = horiz.bounds[xx * 2 + 1];
      const k = xx * horiz.ksize;
      acc.fill(half);
      for (let x = 0; x < xmax; x++) {
        const p = srcRow + (x + xmin) * channels;
        const w = horiz.kk[k + x];
        for (let c = 0; c < channels; c++) acc[c] += src[p + c] * w;
      }
      const o = (y * outW + xx) * channels;
      for (let c = 0; c < channels; c++) tmp[o + c] = clip8(acc[c]);
    }
  }
  const out = new Uint8Array(outW * outH * channels);
  for (let yy = 0; yy < outH; yy++) {
    const ymin = vert.bounds[yy * 2] - ybFirst;
    const ymax = vert.bounds[yy * 2 + 1];
    const k = yy * vert.ksize;
    for (let xx = 0; xx < outW; xx++) {
      acc.fill(half);
      for (let y = 0; y < ymax; y++) {
        const p = ((y + ymin) * outW + xx) * channels;
        const w = vert.kk[k + y];
        for (let c = 0; c < channels; c++) acc[c] += tmp[p + c] * w;
      }
      const o = (yy * outW + xx) * channels;
      for (let c = 0; c < channels; c++) out[o + c] = clip8(acc[c]);
    }
  }
  return out;
}

/** RGB BICUBIC resize (Pillow's default), kept for the callers that only need that. */
export function resizeBicubic(src, width, height, outW, outH, box = [0, 0, width, height]) {
  return resizePixels(src, width, height, outW, outH, { box });
}

function roundHalfEven(x) {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

/** CLIP's input: shortest side to 224 (bicubic, like torchvision Resize), then a 224x224 centre crop. */
export function clipInputPixels(rgb, width, height, size = 224) {
  // torchvision Resize(int): shorter side = size, longer side = int(size * long / short)
  const [w, h] = width <= height
    ? [size, Math.trunc((size * height) / width)]
    : [Math.trunc((size * width) / height), size];
  const resized = resizeBicubic(rgb, width, height, w, h);
  // torchvision CenterCrop: top/left = int(round((dim - size) / 2)) with Python's
  // round, which rounds halves to even (86.5 -> 86); Math.round would give 87.
  const top = roundHalfEven((h - size) / 2);
  const left = roundHalfEven((w - size) / 2);
  const out = new Uint8Array(size * size * 3);
  for (let y = 0; y < size; y++) {
    out.set(resized.subarray(((y + top) * w + left) * 3, ((y + top) * w + left + size) * 3), y * size * 3);
  }
  return out;
}
