"""The app scores with the JSON copies of the models (no scikit-learn, see
linear_model.py). They must give exactly what the original .pkl models give."""
from pathlib import Path

import joblib
import numpy as np

import linear_model

ROOT = Path(__file__).resolve().parent.parent


def test_ranking_model_json_matches_the_trained_model():
    bundle = joblib.load(ROOT / "rank_model_v1.pkl")
    model = linear_model.LinearModel.load(ROOT / "rank_model_v1.json")
    X = np.random.default_rng(0).random((500, 9)) * [1, 1, 1, 1, 1, 1, 60, 0.5, 1]

    expected = bundle["model"].predict_proba(bundle["scaler"].transform(X))[:, 1]
    assert model.features == bundle["features"]
    assert np.allclose(model.probability(X), expected, atol=1e-12)


def test_legacy_classifier_json_matches_the_original():
    original = joblib.load(ROOT / "design_classifier.pkl")
    model = linear_model.LinearModel.load(ROOT / "design_classifier.json")
    X = np.random.default_rng(1).normal(size=(200, 512))

    assert np.allclose(model.probability(X), original.predict_proba(X)[:, 1], atol=1e-12)
