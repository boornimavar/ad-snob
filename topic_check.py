"""Is an ad actually about what was searched?

Meta's Ad Library has no industry filter for ordinary ads, so a category is
just a keyword search: "food" also returns a jeweller's hiring poster that
lists "food & accommodation" among the job perks. This module flags such ads
as off-topic so the dashboard can hide them.

An ad is off-topic only when BOTH are true (tested 2026-10-15 on real food,
skincare and haircare searches: every ad hidden was genuinely off-topic):
  1. The picture: CLIP, comparing the ad's image embedding with "an
     advertisement for <search>" against a list of other common ad topics,
     gives the search less than OFF_TOPIC_BELOW of the probability.
  2. The advertiser's own text (title + body) doesn't mention the search
     word. Plain product photos are hard to judge from the picture alone;
     their copy usually says what they are ("hair colour", "hair serum").

Topics in the same family as the search are never counted against it, so a
"beauty" search doesn't hide skincare ads.
"""
import re

import numpy as np

OFF_TOPIC_BELOW = 0.05  # off-topic ads scored <= 0.04; Wolt (food delivery) 0.08

# Other common kinds of ads, grouped into families.
TOPICS = {
    "skincare": ("beauty", "an advertisement for skincare or cosmetics"),
    "haircare": ("beauty", "an advertisement for shampoo, hair oil or haircare"),
    "makeup": ("beauty", "an advertisement for makeup, lipstick or nail polish"),
    "fragrance": ("beauty", "an advertisement for perfume or fragrance"),
    "fashion": ("style", "an advertisement for clothing or fashion"),
    "jewellery": ("style", "an advertisement for jewellery, gold or diamonds"),
    "footwear": ("style", "an advertisement for shoes or sneakers"),
    "food": ("food", "an advertisement for food, snacks, drinks or a restaurant"),
    "supplements": ("health", "an advertisement for supplements, protein or vitamins"),
    "fitness": ("health", "an advertisement for a gym or fitness"),
    "clinic": ("health", "an advertisement for a hospital, clinic, doctor or pharmacy"),
    "home": ("home", "an advertisement for furniture or home decor"),
    "electronics": ("tech", "an advertisement for phones, laptops or electronics"),
    "software": ("tech", "an advertisement for an app or software"),
    "travel": ("travel", "an advertisement for travel, hotels or holidays"),
    "property": ("property", "an advertisement for real estate or apartments"),
    "finance": ("money", "an advertisement for loans, insurance, banking or investing"),
    "education": ("learning", "an advertisement for a course, school or coaching"),
    "job openings": ("jobs", "a job vacancy or hiring advertisement"),
    "vehicles": ("vehicles", "an advertisement for cars or motorbikes"),
    "events": ("events", "an advertisement for an event, concert or festival"),
    "pets": ("pets", "an advertisement for pet food or pet care"),
}

# The side-list categories and which families count as on-topic for them.
CATEGORY_FAMILIES = {
    "skincare": {"beauty"}, "beauty": {"beauty"}, "haircare": {"beauty"}, "makeup": {"beauty"},
    "fragrance": {"beauty"}, "fashion": {"style"}, "food": {"food", "health"}, "home decor": {"home"},
    "electronics": {"tech"}, "supplements": {"health", "food"}, "fitness": {"health"},
    "software": {"tech"}, "pet care": {"pets"}, "travel": {"travel"},
}

# Words too generic to prove an ad is on-topic.
_GENERIC = {"care", "and", "the", "for", "products", "product", "services", "service", "shop", "store", "online"}


def search_words(query):
    """Words whose presence in the ad's own text keeps it, e.g. haircare -> haircare, hair."""
    words = {w for w in re.findall(r"[a-z]+", str(query).lower()) if w not in _GENERIC and len(w) > 2}
    for w in list(words):
        for suffix in ("care", "s"):
            if w.endswith(suffix) and len(w) > len(suffix) + 2:
                words.add(w[: -len(suffix)])
    return words


def _families_for(query, query_vec, topic_vecs):
    """Families on-topic for this search: the fixed map for side-list
    categories, otherwise the two closest families (erring on keeping ads)."""
    key = " ".join(str(query).lower().split())
    if key in CATEGORY_FAMILIES:
        return CATEGORY_FAMILIES[key]
    closest = np.argsort(-(topic_vecs @ query_vec))
    families = []
    for i in closest:
        family = list(TOPICS.values())[i][0]
        if family not in families:
            families.append(family)
        if len(families) == 2:
            break
    return set(families)


def check_topics(rows, query, encode_text):
    """Set row["topic_score"] (0-1) and row["off_topic"] (0/1) on each row.

    encode_text(list_of_str) -> L2-normalised numpy array, one row per text
    (CLIP's text encoder). Rows without an image embedding stay on-topic.
    """
    if not str(query or "").strip():
        for row in rows:
            row["topic_score"], row["off_topic"] = None, 0
        return rows
    query_vec = encode_text([f"an advertisement for {query}", f"a {query} advertisement",
                             f"{query} products in an ad"]).mean(axis=0)
    query_vec = query_vec / np.linalg.norm(query_vec)
    all_vecs = encode_text([text for _family, text in TOPICS.values()])
    on_topic_families = _families_for(query, query_vec, all_vecs)
    others = np.array([vec for vec, (family, _text) in zip(all_vecs, TOPICS.values())
                       if family not in on_topic_families])
    words = search_words(query)
    for row in rows:
        row["topic_score"], row["off_topic"] = None, 0
        embedding = str(row.get("clip_embedding") or "")
        if not embedding:
            continue
        try:
            image_vec = np.array([float(v) for v in embedding.split(",")])
        except ValueError:
            continue
        image_vec = image_vec / np.linalg.norm(image_vec)
        logits = np.concatenate([[image_vec @ query_vec], others @ image_vec]) * 100.0
        probs = np.exp(logits - logits.max())
        score = float(probs[0] / probs.sum())
        row["topic_score"] = round(score, 4)
        own_text = f"{row.get('title') or ''} {row.get('body') or ''}".lower()
        mentions_search = any(re.search(rf"\b{re.escape(w)}", own_text) for w in words)
        row["off_topic"] = int(score < OFF_TOPIC_BELOW and not mentions_search)
    return rows
