"""`written` — explanation/insight graded by the LLM-judge against a rubric.
config: {rubric: [{text, points}], threshold?}   expected: null

The student's answer text is read from the answer dump (var_name) OR, if the cell
is a markdown answer, from ctx.answers under the var_name the author assigned.
Low judge confidence (< threshold, default 0.6) → verdict 'review' + a review flag
is raised by main.py (which owns the DB handle).
"""
from __future__ import annotations

from . import GradeContext, QResult, _verdict

REVIEW_DEFAULT = 0.6


def grade(question: dict, ctx: GradeContext) -> QResult:
    qid = str(question.get("id"))
    pts = float(question.get("points") or 0)
    cfg = question.get("config") or {}
    rubric = cfg.get("rubric") or []
    threshold = float(cfg.get("threshold") or REVIEW_DEFAULT)
    answer = ctx.answers.get(question.get("var_name"))
    if answer is None:
        # var-name-free authoring: gather the cell's auto-derived string vars
        exp = question.get("expected") or {}
        parts = [ctx.answers.get(v) for v in (exp.get("vars") or [])]
        parts = [p for p in parts if isinstance(p, str) and p.strip()]
        answer = "\n".join(parts) if parts else None
    answer_text = "" if answer is None else (answer if isinstance(answer, str) else str(answer))

    if ctx.judge is None:
        # No coach configured → cannot auto-grade; send to human review with 0 provisional.
        return QResult(qid, 0.0, pts, "review",
                       "Awaiting human review (LLM-judge unavailable).")
    if not answer_text.strip():
        return QResult(qid, 0.0, pts, "fail", "No written answer provided.")

    try:
        jr = ctx.judge.judge(
            question_prompt=cfg.get("prompt") or question.get("cell_ref") or "Explain your reasoning.",
            rubric=rubric, student_answer=answer_text, qtype="explanation")
    except Exception as e:
        return QResult(qid, 0.0, pts, "review", f"LLM-judge unavailable — sent to review ({e}).")

    # Scale the rubric total onto the question's points.
    scaled = pts * (jr.score / jr.max) if jr.max else 0.0
    scaled = round(min(pts, max(0.0, scaled)), 4)
    if jr.confidence < threshold:
        return QResult(qid, scaled, pts, "review",
                       f"{jr.feedback} (low confidence {jr.confidence:.2f} → flagged for review)")
    return QResult(qid, scaled, pts, _verdict(scaled, pts), jr.feedback)
