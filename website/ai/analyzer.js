// The design score for one picture, in the browser, the way the laptop app
// does it (analyze_images_batch + rank_score): CLIP's measurements, the best of
// six text readings, busy-ness, then the trained ranking model.
import Tesseract from "https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.esm.min.js";
import { ocrVariants, visualComplexity } from "./pixels.js";
import { clipMeasurements, bestReading, rankScore } from "./score.js";
import { phashRGB, frameVariantHashes } from "./phash.js";

const { createScheduler, createWorker, PSM } = Tesseract; // the browser build only has a default export
// Text reading is the slow part (6 readings per picture): one reader per spare core, 2-4.
const OCR_WORKERS = Math.max(2, Math.min(4, (navigator.hardwareConcurrency || 4) - 2));

export class Analyzer {
  constructor() {
    this.clip = new Worker(new URL("./clip-worker.js", import.meta.url), { type: "module" });
    this.pending = new Map();
    this.nextId = 0;
    this.clip.onmessage = (event) => {
      const { kind, id } = event.data;
      if (kind === "progress") return this.onProgress?.(event.data.loaded, event.data.total);
      if (kind === "ready") return this.readyResolve?.();
      const job = this.pending.get(id);
      if (kind === "error" && !job) return this.readyReject?.(new Error(event.data.error));
      if (!job) return;
      this.pending.delete(id);
      if (kind === "embedding") job.resolve(event.data.embedding);
      else job.reject(new Error(event.data.error));
    };
  }

  /** Download/start everything. onProgress(loaded, total) while the AI model downloads (first time only). */
  async init(onProgress) {
    if (this.ready) return this.ready;
    this.onProgress = onProgress;
    this.ready = (async () => {
      const base = new URL(".", import.meta.url);
      const [text, model] = await Promise.all([
        fetch(new URL("clip_text.json", base)).then((r) => r.json()),
        fetch(new URL("rank_model_v1.json", base)).then((r) => r.json()),
      ]);
      this.text = text;
      this.model = model;
      const clipReady = new Promise((resolve, reject) => { this.readyResolve = resolve; this.readyReject = reject; });
      this.clip.postMessage({ kind: "load" });
      this.scheduler = createScheduler();
      const workers = await Promise.all(Array.from({ length: OCR_WORKERS }, () =>
        createWorker("eng", 1, { langPath: new URL("tessdata", base).href, gzip: false })));
      for (const w of workers) {
        await w.setParameters({ tessedit_pageseg_mode: PSM.SPARSE_TEXT }); // --psm 11, as the app
        this.scheduler.addWorker(w);
      }
      await clipReady;
    })();
    return this.ready;
  }

  embed(rgb, width, height) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      const copy = rgb.slice(); // the worker gets its own copy
      this.clip.postMessage({ kind: "embed", id, rgb: copy, width, height }, [copy.buffer]);
    });
  }

  /** One text reading: tesseract words as {text, conf, left, top, width, height}. */
  async read(pixels, width, height, channels) {
    const canvas = new OffscreenCanvas(width, height);
    const ctx = canvas.getContext("2d");
    const data = ctx.createImageData(width, height);
    for (let i = 0, p = 0; i < width * height; i++, p += 4) {
      const s = i * channels;
      data.data[p] = pixels[s];
      data.data[p + 1] = pixels[channels === 3 ? s + 1 : s];
      data.data[p + 2] = pixels[channels === 3 ? s + 2 : s];
      data.data[p + 3] = 255;
    }
    ctx.putImageData(data, 0, 0);
    const png = await canvas.convertToBlob({ type: "image/png" }); // lossless
    const { data: out } = await this.scheduler.addJob("recognize", png, {}, { blocks: true });
    const words = out.words?.length ? out.words
      : (out.blocks || []).flatMap((b) => b.paragraphs.flatMap((p) => p.lines.flatMap((l) => l.words)));
    return words.map((w) => ({ text: w.text, conf: w.confidence, left: w.bbox.x0, top: w.bbox.y0,
      width: w.bbox.x1 - w.bbox.x0, height: w.bbox.y1 - w.bbox.y0 }));
  }

  /** All measurements + rank_score for one RGB picture, like one row of the app's ads table. */
  async analyze(rgb, width, height) {
    await this.ready;
    const embeddingPromise = this.embed(rgb, width, height);
    const v = ocrVariants(rgb, width, height);
    const readings = await Promise.all([
      this.read(v.rgb, v.width, v.height, 3),
      ...v.grays.map((g) => this.read(g, v.width, v.height, 1)),
    ]);
    const record = {
      ...clipMeasurements(await embeddingPromise, this.text),
      ...bestReading(readings, width, height),
      visual_complexity: Math.round(visualComplexity(rgb, width, height) * 1e4) / 1e4,
      image_width: width,
      image_height: height,
      aspect_ratio: height ? Math.round((width / height) * 1e4) / 1e4 : 0,
      // fingerprints for duplicate grouping (dedup.js), same as the app's
      image_hash: phashRGB(rgb, width, height),
      frame_variant_hash: frameVariantHashes(rgb, width, height),
    };
    // the app's combined_score: only a last tie-breaker when picking a group's best version
    record.creative_score = Math.round((0.65 * record.clip_design_score + 0.25 * record.ocr_score + 0.1 * record.visual_complexity) * 1e4) / 1e4;
    record.rank_score = rankScore(record, this.model);
    return record;
  }
}

/** RGB pixels of a picture file, decoded the way Chrome does it (checked identical to the app's Pillow). */
export async function decodeImage(blob) {
  const bmp = await createImageBitmap(blob, { colorSpaceConversion: "none", premultiplyAlpha: "none" });
  const canvas = new OffscreenCanvas(bmp.width, bmp.height);
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(bmp, 0, 0);
  const rgba = ctx.getImageData(0, 0, bmp.width, bmp.height).data;
  const rgb = new Uint8Array(bmp.width * bmp.height * 3);
  for (let i = 0, p = 0; p < rgba.length; p += 4) { rgb[i++] = rgba[p]; rgb[i++] = rgba[p + 1]; rgb[i++] = rgba[p + 2]; }
  return { rgb, width: bmp.width, height: bmp.height };
}
