"""`prediction` — score the student's predictions.csv against hidden labels.
config: {metric, thresholds: [[bound,'full'|points], ...], id_col?, target_col?, pred_col?}
expected: null (the hidden labels live in assignment-content; grader downloads them).

thresholds map the metric value to a score. For lower-is-better metrics (rmse/mae/
logloss), a bound means "value <= bound → that score" (checked best-first). For
higher-is-better (r2/accuracy/f1/auc…), "value >= bound → that score".
"""
from __future__ import annotations

import io

from . import GradeContext, QResult, _verdict
from ..metrics import compute_metric, lower_is_better


def _read_csv(raw: bytes):
    import pandas as pd
    return pd.read_csv(io.BytesIO(raw))


def _score_from_thresholds(metric: str, value: float, thresholds: list, pts: float) -> float:
    lo = lower_is_better(metric)
    best = 0.0
    for bound, award in thresholds:
        bound = float(bound)
        award_pts = pts if str(award).lower() == "full" else float(award)
        hit = (value <= bound) if lo else (value >= bound)
        if hit:
            best = max(best, award_pts)
    return min(best, pts)


def grade(question: dict, ctx: GradeContext) -> QResult:
    qid = str(question.get("id"))
    pts = float(question.get("points") or 0)
    cfg = question.get("config") or {}
    metric = cfg.get("metric") or "rmse"
    thresholds = cfg.get("thresholds") or []

    preds_raw = ctx.artifacts.get("predictions.csv")
    labels_raw = ctx.labels.get(qid)
    if preds_raw is None:
        return QResult(qid, 0.0, pts, "fail", "No predictions.csv was produced.")
    if labels_raw is None:
        return QResult(qid, 0.0, pts, "error", "Hidden labels missing for this question.")

    try:
        preds = _read_csv(preds_raw)
        labels = _read_csv(labels_raw)
    except Exception as e:
        return QResult(qid, 0.0, pts, "error", f"Could not read predictions/labels: {e}")

    id_col = cfg.get("id_col")
    target_col = cfg.get("target_col") or (labels.columns[-1] if len(labels.columns) else None)
    pred_col = cfg.get("pred_col")

    try:
        if id_col and id_col in preds.columns and id_col in labels.columns:
            merged = labels.merge(preds, on=id_col, suffixes=("_true", "_pred"))
            y_true = merged[f"{target_col}_true"] if f"{target_col}_true" in merged else merged[target_col]
            pcol = pred_col or (f"{target_col}_pred" if f"{target_col}_pred" in merged else preds.columns[-1])
            y_pred = merged[pcol]
        else:
            # positional alignment fallback
            if len(preds) != len(labels):
                return QResult(qid, 0.0, pts, "fail",
                               f"predictions ({len(preds)}) and labels ({len(labels)}) differ in length.")
            y_true = labels[target_col]
            y_pred = preds[pred_col] if pred_col and pred_col in preds.columns else preds.iloc[:, -1]
    except Exception as e:
        return QResult(qid, 0.0, pts, "error", f"Could not align predictions to labels: {e}")

    try:
        value = compute_metric(metric, y_true, y_pred)
    except Exception as e:
        return QResult(qid, 0.0, pts, "error", f"metric {metric} failed: {e}")

    score = _score_from_thresholds(metric, value, thresholds, pts) if thresholds else (pts if value else 0.0)
    return QResult(qid, score, pts, _verdict(score, pts),
                   f"{metric.upper()} = {value:.4g} → {score:g}/{pts:g} points.")
