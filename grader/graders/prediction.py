"""`prediction` — score the student's predictions against hidden labels.
config: {metric, thresholds, id_col?, target_col?, pred_col?, pred_var?}
expected: null (the hidden labels live in assignment-content; grader downloads them).

The student's predictions are located flexibly, in order:
  (a) a `predictions.csv` artifact (notebook-written or uploaded),
  (b) a dumped predictions variable (list / ndarray / Series / DataFrame) — named by
      config.pred_var, else common names (predictions/preds/y_pred/prediction),
  (c) the only *.csv the student produced (any name).
A precise error is returned only when truly none is found.

thresholds map the metric value to a score. For lower-is-better metrics (rmse/mae/
logloss), a bound means "value <= bound → that score" (checked best-first). For
higher-is-better (r2/accuracy/f1/auc…), "value >= bound → that score".
"""
from __future__ import annotations

import io

from . import GradeContext, QResult, _verdict
from ..extract import deserialize_df
from ..metrics import compute_metric, lower_is_better

_PRED_VAR_NAMES = ("predictions", "preds", "y_pred", "prediction", "pred")


def _read_csv(raw: bytes):
    import pandas as pd
    return pd.read_csv(io.BytesIO(raw))


def _df_from_variable(val):
    """Turn a dumped predictions variable into a DataFrame, or None if not usable.
    Accepts a DataFrame dump, a list/tuple of scalars, or a nested list."""
    import pandas as pd
    if val is None:
        return None
    d = deserialize_df(val)  # {__df__,...} -> DataFrame; else returns val unchanged
    if isinstance(d, pd.DataFrame):
        return d if len(d.columns) else None
    if isinstance(val, (list, tuple)) and len(val) > 0:
        if all(isinstance(x, (list, tuple)) for x in val):
            return pd.DataFrame(list(val))
        return pd.DataFrame({"prediction": list(val)})
    return None


def _resolve_predictions(question: dict, ctx: GradeContext, cfg: dict):
    """Return (preds_df, error). error is a precise message only when nothing is found."""
    # (a) predictions.csv — collected by the runner, or a student upload of that name
    raw = ctx.artifacts.get("predictions.csv") or ctx.produced_files.get("predictions.csv")
    if raw is not None:
        return _read_csv(raw), None

    # (b) a dumped predictions variable
    names = ([cfg["pred_var"]] if cfg.get("pred_var") else list(_PRED_VAR_NAMES))
    for name in names:
        if name and name in ctx.answers and ctx.answers[name] is not None:
            df = _df_from_variable(ctx.answers[name])
            if df is not None:
                return df, None

    # (c) the only *.csv the student produced (any name)
    csvs = [b for n, b in ctx.produced_files.items() if n.lower().endswith(".csv")]
    if len(csvs) == 1:
        return _read_csv(csvs[0]), None
    if len(csvs) > 1:
        return None, ("Multiple CSV files were produced — name your predictions file "
                      "`predictions.csv` (or set a predictions variable) so it can be graded.")
    return None, ("No predictions found — write a `predictions.csv`, or assign your predictions "
                  "to a `predictions` variable (list / array / DataFrame).")


def _normalize_thresholds(thresholds, pts: float) -> list[tuple[float, float]]:
    """Accept either shape the authoring UIs produce and return [(bound, award_pts), ...]:
      * list of pairs: [[30000, "full"], [40000, 7], [60000, 4]]
      * object:        {"pass": 30000}  → full points at that bound
                       {"full": 30000, "7": 40000, "4": 60000}  → key = award
    """
    pairs: list[tuple[float, float]] = []
    if isinstance(thresholds, dict):
        for k, v in thresholds.items():
            key = str(k).lower()
            award = pts if key in ("pass", "full", "true") else float(k)
            pairs.append((float(v), award))
    elif isinstance(thresholds, (list, tuple)):
        for item in thresholds:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                award = pts if str(item[1]).lower() == "full" else float(item[1])
                pairs.append((float(item[0]), award))
    return pairs


def _score_from_thresholds(metric: str, value: float, thresholds, pts: float) -> float:
    lo = lower_is_better(metric)
    best = 0.0
    for bound, award_pts in _normalize_thresholds(thresholds, pts):
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

    labels_raw = ctx.labels.get(qid)
    if labels_raw is None:
        return QResult(qid, 0.0, pts, "error", "Hidden labels missing for this question.")

    try:
        preds, err = _resolve_predictions(question, ctx, cfg)
    except Exception as e:
        return QResult(qid, 0.0, pts, "error", f"Could not read predictions: {e}")
    if preds is None:
        return QResult(qid, 0.0, pts, "fail", err or "No predictions found.")

    try:
        labels = _read_csv(labels_raw)
    except Exception as e:
        return QResult(qid, 0.0, pts, "error", f"Could not read labels: {e}")

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
