"""Export the app's off-topic check vectors for the website (topic.js).

    .venv\\Scripts\\python.exe website\\ai\\export_topic_text.py [test.json]

topic_check.py compares each picture with "an advertisement for <search>"
against 22 other ad topics, using CLIP's text half. This writes, made by the
app's own CLIP: the 22 topic vectors and the search vector for each side-list
category, to website/ai/topic_text.json. Typed searches outside that list are
turned into vectors in the browser (checked against the app). Rerun it if
TOPICS or CATEGORY_FAMILIES in topic_check.py change.

With a second argument, also writes the app's search vectors for a list of
test searches there (for checking the browser's text half).
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import meta_design_scraper_session as scraper  # noqa: E402
import topic_check  # noqa: E402

TEST_SEARCHES = ["skincare", "food", "chocolate", "sunscreen", "running shoes", "coffee",
                 "ayurvedic hair oil", "baby diapers", "mobile phones", "home loans", "Diwali gifts", "kurta"]


def search_vector(query):
    """Exactly topic_check.check_topics' query_vec."""
    vec = scraper.encode_texts([f"an advertisement for {query}", f"a {query} advertisement",
                                f"{query} products in an ad"]).mean(axis=0)
    return vec / np.linalg.norm(vec)


def as_list(vec):
    return [round(float(v), 7) for v in vec]


def main():
    scraper.load_models(scraper.ML_MODEL_PATH)
    data = {
        "model": "ViT-B/32",
        "below": topic_check.OFF_TOPIC_BELOW,
        "topics": [{"name": name, "family": family, "vector": as_list(vec)}
                   for (name, (family, _text)), vec in zip(topic_check.TOPICS.items(),
                       scraper.encode_texts([text for _family, text in topic_check.TOPICS.values()]))],
        "categories": {key: {"families": sorted(families), "vector": as_list(search_vector(key))}
                       for key, families in topic_check.CATEGORY_FAMILIES.items()},
        "generic": sorted(topic_check._GENERIC),
    }
    out = Path(__file__).with_name("topic_text.json")
    out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB): {len(data['topics'])} topics, "
          f"{len(data['categories'])} categories")
    if len(sys.argv) > 1:
        test = {q: {"vector": as_list(search_vector(q)),
                    "prompts": [as_list(v) for v in scraper.encode_texts(
                        [f"an advertisement for {q}", f"a {q} advertisement", f"{q} products in an ad"])]}
                for q in TEST_SEARCHES}
        Path(sys.argv[1]).write_text(json.dumps(test), encoding="utf-8")
        print(f"wrote {len(test)} test searches to {sys.argv[1]}")


if __name__ == "__main__":
    main()
