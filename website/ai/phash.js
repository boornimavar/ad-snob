// Picture fingerprints for duplicate grouping, as the laptop app makes them:
// imagehash 4.3.2 phash (grey, 32x32 LANCZOS, 2-D DCT, 8x8 low frequencies
// above their median) of the whole picture, and of five square crops
// (frame_variant_hash: ImageOps.fit to 256x256 LANCZOS at five centres) so
// the same design in square, tall and wide frames still matches.
import { resizePixels, FILTERS } from "./resize.js";
import { toGray } from "./pixels.js";

const N = 32;
const COS = new Float64Array(N * N); // COS[k*N+n] = cos(pi*k*(2n+1)/(2N))
for (let k = 0; k < N; k++) for (let n = 0; n < N; n++) COS[k * N + n] = Math.cos((Math.PI * k * (2 * n + 1)) / (2 * N));

/** scipy.fftpack.dct type II, unnormalised: y[k] = 2 * sum x[n] cos(pi k (2n+1) / 2N) */
function dct1d(x, k) {
  let s = 0;
  for (let n = 0; n < N; n++) s += x[n] * COS[k * N + n];
  return 2 * s;
}

/** phash of a grey picture (Uint8Array, w*h). Returns 16 hex digits, like str(imagehash.phash(img)). */
export function phashGray(gray, width, height) {
  const small = resizePixels(gray, width, height, N, N, { channels: 1, filter: FILTERS.lanczos });
  // dct along axis 0 (down the columns), then along axis 1 (across the rows); keep the 8x8 corner
  const col = new Float64Array(N);
  const stage = new Float64Array(8 * N); // rows k=0..7 of the column DCT
  for (let x = 0; x < N; x++) {
    for (let y = 0; y < N; y++) col[y] = small[y * N + x];
    for (let k = 0; k < 8; k++) stage[k * N + x] = dct1d(col, k);
  }
  const low = new Float64Array(64);
  const row = new Float64Array(N);
  for (let k = 0; k < 8; k++) {
    for (let x = 0; x < N; x++) row[x] = stage[k * N + x];
    for (let j = 0; j < 8; j++) low[k * 8 + j] = dct1d(row, j);
  }
  const sorted = Float64Array.from(low).sort();
  const median = (sorted[31] + sorted[32]) / 2;
  let hex = "";
  for (let i = 0; i < 64; i += 4) {
    let nibble = 0;
    for (let b = 0; b < 4; b++) nibble = (nibble << 1) | (low[i + b] > median ? 1 : 0);
    hex += nibble.toString(16);
  }
  return hex;
}

/** str(imagehash.phash(image)) for an RGB picture. */
export function phashRGB(rgb, width, height) {
  return phashGray(toGray(rgb, width * height), width, height);
}

const CENTRES = [[0.5, 0.5], [0.25, 0.5], [0.75, 0.5], [0.5, 0.25], [0.5, 0.75]];

/** frame_variant_hash: phash of ImageOps.fit(img, (256, 256), LANCZOS, centering) at five centres. */
export function frameVariantHashes(rgb, width, height) {
  return CENTRES.map(([cx, cy]) => {
    // ImageOps.fit's crop box for a square output (bleed 0)
    const ratio = width / height;
    let cw, ch;
    if (ratio === 1) { cw = width; ch = height; } else if (ratio >= 1) { cw = height; ch = height; } else { cw = width; ch = width; }
    const left = (width - cw) * cx;
    const top = (height - ch) * cy;
    const square = resizePixels(rgb, width, height, 256, 256, { box: [left, top, left + cw, top + ch], filter: FILTERS.lanczos });
    return phashRGB(square, 256, 256);
  });
}

/** Number of differing bits between two 16-hex-digit hashes (imagehash's a - b). */
export function hashDistance(a, b) {
  if (!a || !b || a.length !== b.length) return 999;
  let d = 0;
  for (let i = 0; i < a.length; i++) {
    let x = parseInt(a[i], 16) ^ parseInt(b[i], 16);
    while (x) { d += x & 1; x >>= 1; }
  }
  return d;
}
