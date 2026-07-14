"""`set` — the student's value must be one of a small accepted list.
config: {accepted: [...], tolerance?}   expected: null
"""
from __future__ import annotations

from . import GradeContext, QResult, _verdict
from .exact import values_equal


def grade(question: dict, ctx: GradeContext) -> QResult:
    qid = str(question.get("id"))
    pts = float(question.get("points") or 0)
    cfg = question.get("config") or {}
    accepted = cfg.get("accepted") or []
    tol = float(cfg.get("tolerance") or 0)
    student = ctx.answers.get(question.get("var_name"))
    if not accepted:
        return QResult(qid, 0.0, pts, "error", "No accepted answers configured.")
    if student is None:
        return QResult(qid, 0.0, pts, "fail", f"No value found for `{question.get('var_name')}`.")
    ok = any(values_equal(student, a, tol) for a in accepted)
    score = pts if ok else 0.0
    fb = "Accepted." if ok else f"{student!r} is not one of the accepted answers."
    return QResult(qid, score, pts, _verdict(score, pts), fb)
