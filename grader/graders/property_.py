"""`property` — grade a *property* of the student's answer, not a fixed value.

The check is an AUTHOR-provided expression from a small, fixed, safe DSL. Student
input is treated purely as DATA — we NEVER eval student-supplied code (STRICT §
G3 property note). Supported forms:

    corr>0.5   corr<-0.3   corr>=0.5           (needs config.target; student answer = a column name)
    nunique==5  nunique>10                     (student answer = a column name)
    count==100                                 (column name -> non-null count; or numeric answer)
    dtype==float64                             (student answer = a column name)
    shape==100,5                               (student answer = a DataFrame in answers)
    in_range                                   (student answer numeric, within config.min..config.max)

config: {property: "corr>0.5", target?: "y", data?: "train.csv", min?, max?}
"""
from __future__ import annotations

import io
import operator
import re
from typing import Any

from . import GradeContext, QResult, _verdict

_OPS = {
    ">=": operator.ge, "<=": operator.le, "==": operator.eq,
    "!=": operator.ne, ">": operator.gt, "<": operator.lt,
}
_TOKEN = re.compile(r"^\s*([a-zA-Z_]+)\s*(>=|<=|==|!=|>|<)\s*(.+?)\s*$")


def _load_df(ctx: GradeContext, cfg: dict):
    import pandas as pd
    name = cfg.get("data")
    raw = None
    if name and name in ctx.data_files:
        raw = ctx.data_files[name]
    elif not name:
        for fn, b in ctx.data_files.items():
            if fn.lower().endswith(".csv"):
                raw = b
                break
    if raw is None:
        return None
    return pd.read_csv(io.BytesIO(raw))


def _coerce_rhs(rhs: str):
    try:
        return float(rhs)
    except ValueError:
        return rhs.strip()


def grade(question: dict, ctx: GradeContext) -> QResult:
    qid = str(question.get("id"))
    pts = float(question.get("points") or 0)
    cfg = question.get("config") or {}
    prop = (cfg.get("property") or "").strip()
    student = ctx.answers.get(question.get("var_name"))

    if prop == "in_range":
        try:
            v = float(student)
        except (TypeError, ValueError):
            return QResult(qid, 0.0, pts, "fail", f"Answer {student!r} is not numeric.")
        lo, hi = cfg.get("min"), cfg.get("max")
        ok = (lo is None or v >= float(lo)) and (hi is None or v <= float(hi))
        return QResult(qid, pts if ok else 0.0, pts, _verdict(pts if ok else 0.0, pts),
                       "In range." if ok else f"{v} outside [{lo}, {hi}].")

    m = _TOKEN.match(prop)
    if not m:
        return QResult(qid, 0.0, pts, "error", f"Unparseable property {prop!r}.")
    metric, op, rhs = m.group(1).lower(), m.group(2), _coerce_rhs(m.group(3))
    cmp = _OPS[op]

    df = _load_df(ctx, cfg)
    try:
        if metric == "corr":
            if df is None:
                return QResult(qid, 0.0, pts, "error", "No dataset available for corr check.")
            target = cfg.get("target")
            if not target or target not in df.columns or student not in df.columns:
                return QResult(qid, 0.0, pts, "fail", f"Column `{student}`/target `{target}` not found.")
            val = float(df[student].corr(df[target]))
        elif metric == "nunique":
            val = float(df[student].nunique()) if df is not None and student in df.columns else None
        elif metric == "count":
            if df is not None and student in df.columns:
                val = float(df[student].count())
            else:
                val = float(student)
        elif metric == "dtype":
            got = str(df[student].dtype) if df is not None and student in df.columns else None
            ok = (got == str(rhs)) if op == "==" else (got != str(rhs))
            return QResult(qid, pts if ok else 0.0, pts, _verdict(pts if ok else 0.0, pts),
                           f"dtype {got}." if ok else f"dtype {got} (wanted {rhs}).")
        elif metric == "shape":
            from ..extract import deserialize_df
            d = deserialize_df(student)
            shp = list(getattr(d, "shape", []))
            want = [int(x) for x in str(rhs).split(",")]
            ok = shp == want
            return QResult(qid, pts if ok else 0.0, pts, _verdict(pts if ok else 0.0, pts),
                           f"shape {shp}." if ok else f"shape {shp} (wanted {want}).")
        else:
            return QResult(qid, 0.0, pts, "error", f"Unsupported property metric {metric!r}.")

        if val is None:
            return QResult(qid, 0.0, pts, "fail", f"Could not evaluate `{metric}` on `{student}`.")
        ok = bool(cmp(val, float(rhs)))
        score = pts if ok else 0.0
        return QResult(qid, score, pts, _verdict(score, pts),
                       f"{metric}={val:.4g} {op} {rhs} → {'ok' if ok else 'no'}.")
    except Exception as e:
        return QResult(qid, 0.0, pts, "error", f"property check failed: {e}")
