"""Prediction metrics (scikit-learn where possible). Used by the `prediction` tag.

Each metric takes aligned (y_true, y_pred) arrays and returns a float. `lower_is_better`
tells the threshold mapper which direction is good.
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np


def _arr(x):
    return np.asarray(x, dtype=float).ravel()


def rmse(y_true, y_pred) -> float:
    from sklearn.metrics import mean_squared_error
    return float(math.sqrt(mean_squared_error(_arr(y_true), _arr(y_pred))))


def mae(y_true, y_pred) -> float:
    from sklearn.metrics import mean_absolute_error
    return float(mean_absolute_error(_arr(y_true), _arr(y_pred)))


def r2(y_true, y_pred) -> float:
    from sklearn.metrics import r2_score
    return float(r2_score(_arr(y_true), _arr(y_pred)))


def accuracy(y_true, y_pred) -> float:
    from sklearn.metrics import accuracy_score
    return float(accuracy_score(np.asarray(y_true).ravel(), np.asarray(y_pred).ravel()))


def _labels(y_true, y_pred):
    return np.asarray(y_true).ravel(), np.asarray(y_pred).ravel()


def f1(y_true, y_pred) -> float:
    from sklearn.metrics import f1_score
    yt, yp = _labels(y_true, y_pred)
    avg = "binary" if len(set(yt.tolist())) <= 2 else "macro"
    return float(f1_score(yt, yp, average=avg, zero_division=0))


def precision(y_true, y_pred) -> float:
    from sklearn.metrics import precision_score
    yt, yp = _labels(y_true, y_pred)
    avg = "binary" if len(set(yt.tolist())) <= 2 else "macro"
    return float(precision_score(yt, yp, average=avg, zero_division=0))


def recall(y_true, y_pred) -> float:
    from sklearn.metrics import recall_score
    yt, yp = _labels(y_true, y_pred)
    avg = "binary" if len(set(yt.tolist())) <= 2 else "macro"
    return float(recall_score(yt, yp, average=avg, zero_division=0))


def auc(y_true, y_pred) -> float:
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(np.asarray(y_true).ravel(), _arr(y_pred)))


def logloss(y_true, y_pred) -> float:
    from sklearn.metrics import log_loss
    return float(log_loss(np.asarray(y_true).ravel(), _arr(y_pred)))


# name -> (fn, lower_is_better)
METRICS: dict[str, tuple[Callable, bool]] = {
    "rmse": (rmse, True),
    "mae": (mae, True),
    "logloss": (logloss, True),
    "r2": (r2, False),
    "accuracy": (accuracy, False),
    "f1": (f1, False),
    "precision": (precision, False),
    "recall": (recall, False),
    "auc": (auc, False),
}


def compute_metric(name: str, y_true, y_pred) -> float:
    key = (name or "").strip().lower()
    if key not in METRICS:
        raise ValueError(f"unknown metric {name!r}")
    return METRICS[key][0](y_true, y_pred)


def lower_is_better(name: str) -> bool:
    return METRICS.get((name or "").strip().lower(), (None, False))[1]
