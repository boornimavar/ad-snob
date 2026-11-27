"""Export the app's CLIP text features for the website's in-browser scoring.

    .venv\\Scripts\\python.exe website\\ai\\export_clip_text.py

The app compares every picture with fixed descriptions ("a professionally
designed static advertising creative...", "an ordinary photograph...").
load_models() turns them into CLIP vectors; this writes those exact vectors to
website/ai/clip_text.json so the website doesn't need CLIP's text half for
them. Rerun it if the prompts in meta_design_scraper_session.py change.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import meta_design_scraper_session as scraper  # noqa: E402


def rows(tensor):
    return [[round(float(v), 7) for v in row] for row in tensor.cpu().float()]


def main():
    scraper.load_models(scraper.ML_MODEL_PATH)
    design, photo = scraper._clip_text_features
    data = {
        "model": "ViT-B/32",
        "design": rows(design),                       # predict_clip_design: mean similarity
        "photo": rows(photo),
        "product": rows(scraper._product_text_features),  # predict_product_photo
        "broad": rows(scraper._broad_class_features),     # graphic, photo, video, product prototypes
    }
    out = Path(__file__).with_name("clip_text.json")
    out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB): "
          + ", ".join(f"{k} {len(v)}" for k, v in data.items() if k != "model"))


if __name__ == "__main__":
    main()
