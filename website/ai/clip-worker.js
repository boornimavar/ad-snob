// CLIP, the "eye", in a background worker so the page never freezes.
// fp16 model on the CPU (WASM): checked to give the laptop app's answers
// exactly (WebGPU was 4x faster but drifted up to 0.8 design-score points).
import { CLIPVisionModelWithProjection, CLIPTextModelWithProjection, AutoTokenizer, Tensor, env } from "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.7.5";
import { clipInputPixels } from "./resize.js";

env.allowLocalModels = false; // always the official model from Hugging Face (cached by the browser after the first time)
const MODEL = "Xenova/clip-vit-base-patch32";
const MEAN = [0.48145466, 0.4578275, 0.40821073];
const STD = [0.26862954, 0.26130258, 0.27577711];
let model = null;
let textModel = null, tokenizer = null; // only for typed searches (the off-topic check)

async function load() {
  const files = new Map();
  model = await CLIPVisionModelWithProjection.from_pretrained(MODEL, {
    dtype: "fp16",
    device: "wasm",
    progress_callback: (p) => {
      if (p.status !== "progress" || !p.total) return;
      files.set(p.file, [p.loaded, p.total]);
      let loaded = 0, total = 0;
      for (const [l, t] of files.values()) { loaded += l; total += t; }
      self.postMessage({ kind: "progress", loaded, total });
    },
  });
}

/** Normalised 512-number CLIP embedding of an RGB picture. */
async function embed(rgb, width, height) {
  const px = clipInputPixels(rgb, width, height);
  const input = new Float32Array(3 * 224 * 224);
  for (let i = 0; i < 224 * 224; i++) {
    for (let c = 0; c < 3; c++) input[c * 224 * 224 + i] = (px[i * 3 + c] / 255 - MEAN[c]) / STD[c];
  }
  const { image_embeds } = await model({ pixel_values: new Tensor("float32", input, [1, 3, 224, 224]) });
  const v = Float32Array.from(image_embeds.data);
  const norm = Math.hypot(...v);
  for (let i = 0; i < v.length; i++) v[i] /= norm;
  return v;
}

/** Normalised CLIP text vectors (fp16, like the pictures: checked identical to the app). */
async function embedTexts(texts) {
  if (!textModel) {
    tokenizer = await AutoTokenizer.from_pretrained(MODEL);
    textModel = await CLIPTextModelWithProjection.from_pretrained(MODEL, { dtype: "fp16", device: "wasm" });
  }
  const { text_embeds } = await textModel(tokenizer(texts, { padding: "max_length", max_length: 77, truncation: true }));
  const d = text_embeds.dims[1];
  return texts.map((_, i) => {
    const v = Array.from(text_embeds.data.slice(i * d, (i + 1) * d));
    const n = Math.hypot(...v);
    return v.map((x) => x / n);
  });
}

let queue = Promise.resolve();
self.onmessage = (event) => {
  const { kind, id } = event.data;
  queue = queue.then(async () => { // one picture at a time
    try {
      if (kind === "load") {
        if (!model) await load();
        self.postMessage({ kind: "ready" });
      } else if (kind === "embed") {
        const v = await embed(event.data.rgb, event.data.width, event.data.height);
        self.postMessage({ kind: "embedding", id, embedding: v }, [v.buffer]);
      } else if (kind === "text") {
        self.postMessage({ kind: "text", id, vectors: await embedTexts(event.data.texts) });
      }
    } catch (err) {
      self.postMessage({ kind: "error", id, error: String(err.message || err) });
    }
  });
};
