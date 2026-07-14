"""`exact` — the student's value must equal the solution value.
config: {tolerance?}   expected: {value: ...}   (numbers ± tolerance; strings normalized)
"""
from __future__ import annotations

from typing import Any

from . import GradeContext, QResult, _verdict


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _norm_str(x) -> str:
    return " ".join(str(x).strip().lower().split())


def values_equal(student: Any, expected: Any, tol: float = 0.0) -> bool:
    sn, en = _num(student), _num(expected)
    if sn is not None and en is not None:
        return abs(sn - en) <= tol
    if isinstance(expected, (list, tuple)) or isinstance(student, (list, tuple)):
        s = student if isinstance(student, (list, tuple)) else [student]
        e = expected if isinstance(expected, (list, tuple)) else [expected]
        return len(s) == len(e) and all(values_equal(a, b, tol) for a, b in zip(s, e))
    return _norm_str(student) == _norm_str(expected)


def grade(question: dict, ctx: GradeContext) -> QResult:
    qid = str(question.get("id"))
    pts = float(question.get("points") or 0)
    cfg = question.get("config") or {}
    exp = question.get("expected") or {}
    if "value" not in (exp if isinstance(exp, dict) else {}):
        return QResult(qid, 0.0, pts, "error", "No expected value captured for this question.")
    student = ctx.answers.get(question.get("var_name"))
    tol = float(cfg.get("tolerance") or 0)
    if student is None:
        return QResult(qid, 0.0, pts, "fail", f"No value found for `{question.get('var_name')}`.")
    ok = values_equal(student, exp["value"], tol)
    score = pts if ok else 0.0
    fb = "Correct." if ok else f"Expected {exp['value']!r}, got {student!r}."
    return QResult(qid, score, pts, _verdict(score, pts), fb)
