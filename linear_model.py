"""The app's two logistic-regression models as plain numbers (JSON) + numpy.

scikit-learn pulls in scipy's compiled helpers (e.g. _propack), and Windows
Smart App Control blocked one of those on a colleague's laptop, which stopped
the ranking model from loading. Scoring a logistic regression is one line of
maths, so the packaged app reads the weights from JSON and never imports
scikit-learn. scikit-learn is still used to train (--retrain) and to export.
"""
import json
from pathlib import Path

import numpy as np


class LinearModel:
    """Probability of the model's second class, same as sklearn's predict_proba(X)[:, 1]."""

    def __init__(self, coef, intercept, mean=None, scale=None, features=None, info=None):
        self.coef = np.asarray(coef, dtype=float)
        self.intercept = float(intercept)
        self.mean = None if mean is None else np.asarray(mean, dtype=float)
        self.scale = None if scale is None else np.asarray(scale, dtype=float)
        self.features = features
        self.info = info or {}

    def probability(self, X):
        X = np.asarray(X, dtype=float)
        if self.mean is not None:
            X = (X - self.mean) / self.scale
        z = X @ self.coef + self.intercept
        return 1.0 / (1.0 + np.exp(-z))

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data["coef"], data["intercept"], data.get("mean"), data.get("scale"),
                   data.get("features"), data.get("info"))


def export(model, path, scaler=None, features=None, info=None):
    """Write a fitted binary sklearn LogisticRegression (+ optional StandardScaler) as JSON."""
    data = {
        "coef": model.coef_[0].tolist(),
        "intercept": float(model.intercept_[0]),
        "classes": [str(c) for c in model.classes_],
        "features": features,
        "info": info or {},
    }
    if scaler is not None:
        data["mean"] = scaler.mean_.tolist()
        data["scale"] = scaler.scale_.tolist()
    Path(path).write_text(json.dumps(data, indent=1), encoding="utf-8")
