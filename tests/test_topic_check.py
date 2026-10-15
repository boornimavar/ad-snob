"""topic_check: an ad is off-topic only if the picture is about something
else AND the advertiser's own text doesn't mention the search."""
import numpy as np

import topic_check


def fake_encoder(texts):
    """One axis per idea: an ad 'about X' points along X's axis."""
    axes = ["food", "job", "hair"]
    vectors = []
    for text in texts:
        vector = np.array([1.0 if word in text else 0.0 for word in axes]) + 0.01
        vectors.append(vector / np.linalg.norm(vector))
    return np.array(vectors)


def row(picture_axis, title="", body=""):
    vector = {"food": [1, 0, 0], "job": [0, 1, 0], "hair": [0, 0, 1]}[picture_axis]
    return {"clip_embedding": ",".join(str(v) for v in vector), "title": title, "body": body}


def test_a_job_ad_under_food_is_off_topic():
    ads = [row("food"), row("job", title="Job opportunities await you")]
    topic_check.check_topics(ads, "food", fake_encoder)
    assert [a["off_topic"] for a in ads] == [0, 1]


def test_the_advertisers_own_words_keep_an_ad():
    ads = [row("job", body="Fresh food delivered daily")]
    topic_check.check_topics(ads, "food", fake_encoder)
    assert ads[0]["off_topic"] == 0


def test_no_search_or_no_picture_never_hides():
    ads = [row("job"), {"clip_embedding": "", "title": "", "body": ""}]
    topic_check.check_topics(ads[:1], "", fake_encoder)
    topic_check.check_topics(ads[1:], "food", fake_encoder)
    assert [a["off_topic"] for a in ads] == [0, 0]


def test_search_words_include_the_stem():
    assert topic_check.search_words("haircare") == {"haircare", "hair"}
    assert topic_check.search_words("pet care") == {"pet"}
