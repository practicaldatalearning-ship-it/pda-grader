"""`tests` — hidden assert cells run inside the sandbox after the student's code.
config: {tests: "<python assert lines>"}   expected: null
Partial credit = fraction of the question's assert cells that passed.
ctx.tests_results is populated by main.py from the injected tests.json artifact.
"""
from __future__ import annotations

from . import GradeContext, QResult, _verdict


def grade(question: dict, ctx: GradeContext) -> QResult:
    qid = str(question.get("id"))
    pts = float(question.get("points") or 0)
    frac = ctx.tests_results.get(qid)
    if frac is None:
        return QResult(qid, 0.0, pts, "error",
                       "Hidden tests did not run (notebook may have failed before the test cell).")
    frac = max(0.0, min(1.0, float(frac)))
    score = round(pts * frac, 4)
    if frac >= 1.0:
        fb = "All hidden tests passed."
    elif frac <= 0.0:
        fb = "Hidden tests failed."
    else:
        fb = f"{int(frac * 100)}% of hidden tests passed."
    return QResult(qid, score, pts, _verdict(score, pts), fb)
