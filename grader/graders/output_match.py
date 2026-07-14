"""`output_match` — the student's produced output must match the solution's.
config: {unordered?, tolerance?}   expected: {value | {__df__, columns, rows}}
Handles scalars, lists, and DataFrames (float tolerance; order-insensitive if unordered).
"""
from __future__ import annotations

from typing import Any

from . import GradeContext, QResult, _verdict
from ..extract import deserialize_df
from .exact import values_equal


def _df_equal(a, b, tol: float, unordered: bool) -> bool:
    import pandas as pd
    try:
        da, db = deserialize_df(a), deserialize_df(b)
        if not hasattr(da, "shape") or not hasattr(db, "shape"):
            return False
        if list(da.columns) != list(db.columns) or da.shape != db.shape:
            return False
        if unordered:
            da = da.sort_values(by=list(da.columns)).reset_index(drop=True)
            db = db.sort_values(by=list(db.columns)).reset_index(drop=True)
        # numeric columns with tolerance, others exact
        for col in da.columns:
            sa, sb = da[col], db[col]
            if pd.api.types.is_numeric_dtype(sb) and pd.api.types.is_numeric_dtype(sa):
                if not ((sa.astype(float) - sb.astype(float)).abs() <= tol).all():
                    return False
            else:
                if not (sa.astype(str).values == sb.astype(str).values).all():
                    return False
        return True
    except Exception:
        return False


def grade(question: dict, ctx: GradeContext) -> QResult:
    qid = str(question.get("id"))
    pts = float(question.get("points") or 0)
    cfg = question.get("config") or {}
    exp = question.get("expected") or {}
    tol = float(cfg.get("tolerance") or 0)
    unordered = bool(cfg.get("unordered"))
    student = ctx.answers.get(question.get("var_name"))
    if not isinstance(exp, dict) or "value" not in exp:
        return QResult(qid, 0.0, pts, "error", "No expected output captured.")
    expected = exp["value"]
    if student is None:
        return QResult(qid, 0.0, pts, "fail", f"No output for `{question.get('var_name')}`.")

    is_df = isinstance(expected, dict) and expected.get("__df__") or \
            isinstance(student, dict) and student.get("__df__")
    ok = _df_equal(student, expected, tol, unordered) if is_df else values_equal(student, expected, tol)
    score = pts if ok else 0.0
    return QResult(qid, score, pts, _verdict(score, pts),
                   "Output matches." if ok else "Output does not match the expected result.")
