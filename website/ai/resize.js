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

/** Pillow's precompute_coeffs: per output pixel, the first input index and integer weights. */
function coefficients(inSize, outSize, offset = 0, span = inSize) {
  const scale = span / outSize;
  const filterscale = Math.max(scale, 1);
  const support = 2 * filterscale;
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
      const w = bicubic((x + xmin - center + 0.5) * ss);
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
 * Resize RGB pixels (Uint8Array, 3 bytes per pixel) like PIL's BICUBIC resize.
 * box = [x0, y0, x1, y1] in source pixels (Pillow's reducing box), default whole image.
 */
export function resizeBicubic(src, width, height, outW, outH, box = [0, 0, width, height]) {
  const [bx0, by0, bx1, by1] = box;
  const horiz = coefficients(width, outW, bx0, bx1 - bx0);
  const vert = coefficients(height, outH, by0, by1 - by0);
  // Rows the vertical pass will need (Pillow trims the horizontal pass the same way).
  const ybFirst = vert.bounds[0];
  const ybLast = vert.bounds[(outH - 1) * 2] + vert.bounds[(outH - 1) * 2 + 1];
  const rows = ybLast - ybFirst;
  const tmp = new Uint8Array(outW * rows * 3);
  const half = 1 << (PRECISION_BITS - 1);
  for (let y = 0; y < rows; y++) {
    const srcRow = (y + ybFirst) * width * 3;
    for (let xx = 0; xx < outW; xx++) {
      const xmin = horiz.bounds[xx * 2];
      const xmax = horiz.bounds[xx * 2 + 1];
      const k = xx * horiz.ksize;
      let r = half, g = half, b = half;
      for (let x = 0; x < xmax; x++) {
        const p = srcRow + (x + xmin) * 3;
        const w = horiz.kk[k + x];
        r += src[p] * w; g += src[p + 1] * w; b += src[p + 2] * w;
      }
      const o = (y * outW + xx) * 3;
      tmp[o] = clip8(r); tmp[o + 1] = clip8(g); tmp[o + 2] = clip8(b);
    }
  }
  const out = new Uint8Array(outW * outH * 3);
  for (let yy = 0; yy < outH; yy++) {
    const ymin = vert.bounds[yy * 2] - ybFirst;
    const ymax = vert.bounds[yy * 2 + 1];
    const k = yy * vert.ksize;
    for (let xx = 0; xx < outW; xx++) {
      let r = half, g = half, b = half;
      for (let y = 0; y < ymax; y++) {
        const p = ((y + ymin) * outW + xx) * 3;
        const w = vert.kk[k + y];
        r += tmp[p] * w; g += tmp[p + 1] * w; b += tmp[p + 2] * w;
      }
      const o = (yy * outW + xx) * 3;
      out[o] = clip8(r); out[o + 1] = clip8(g); out[o + 2] = clip8(b);
    }
  }
  return out;
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
