"""`auto` — grade a cell against the answer key automatically.

config:   {tolerance?, scoring?}   scoring ∈ {"all_or_nothing" (default), "proportional"}
expected: {"values": {var_name: solution_value, ...}}

The variables were parsed from the graded cell of the answer key at authoring
time (extract.cell_assign_targets) and their solution values captured. Here we
compare the student's dumped values for the SAME variables, by type:
  * numbers   -> equal within `tolerance`
  * lists/tuples/arrays -> elementwise
  * DataFrames -> same columns, cells compared elementwise
  * dicts      -> same keys + equal values
  * strings/bools/None -> normalized equality
Scoring is all_or_nothing by default (full marks only if every variable matches);
set config.scoring="proportional" for points × matched/total. A cell with no
gradable variable, or whose answer-key value didn't serialize (null), routes to review.
"""
from __future__ import annotations

from typing import Any

from . import GradeContext, QResult, _verdict
from .exact import values_equal


def _auto_equal(student: Any, expected: Any, tol: float) -> bool:
    # DataFrame dump: {"__df__": true, "columns": [...], "rows": [[...], ...]}
    if isinstance(expected, dict) and expected.get("__df__"):
        if not (isinstance(student, dict) and student.get("__df__")):
            return False
        if list(expected.get("columns") or []) != list(student.get("columns") or []):
            return False
        er, sr = expected.get("rows") or [], student.get("rows") or []
        return len(er) == len(sr) and all(values_equal(s, e, tol) for s, e in zip(sr, er))
    # plain dict: same keys + equal values
    if isinstance(expected, dict) and isinstance(student, dict):
        if set(expected.keys()) != set(student.keys()):
            return False
        return all(_auto_equal(student.get(k), expected.get(k), tol) for k in expected)
    # numbers / lists / strings / bools / None -> exact.values_equal (type-aware)
    return values_equal(student, expected, tol)


def grade(question: dict, ctx: GradeContext) -> QResult:
    qid = str(question.get("id"))
    pts = float(question.get("points") or 0)
    cfg = question.get("config") or {}
    exp = question.get("expected") or {}
    values = exp.get("values") if isinstance(exp, dict) else None

    # Not authored yet vs. authored-but-nothing-to-grade → both go to human review
    # with a clear message, never a confusing "error".
    if values is None:
        return QResult(qid, 0.0, pts, "review",
                       "Not yet authored — re-publish so the grader can capture the answer key for this cell.")
    if len(values) == 0:
        return QResult(qid, 0.0, pts, "review",
                       "This cell has no gradable variable — nothing to auto-grade.")

    # A null captured value = the solution wasn't comparable (a model/figure/etc.);
    # those variables can't be auto-graded → route the cell to review.
    gradable = {v: e for v, e in values.items() if e is not None}
    ungradable = [v for v, e in values.items() if e is None]

    if not gradable:
        names = ", ".join(f"`{v}`" for v in ungradable)
        return QResult(qid, 0.0, pts, "review",
                       f"Cannot auto-grade: the answer key value(s) for {names} are not comparable "
                       f"(e.g. a model or figure). Needs human review.")

    tol = float(cfg.get("tolerance") or 0)
    total = len(gradable)
    matched = 0
    misses: list[str] = []
    for var, expected_val in gradable.items():
        student_val = ctx.answers.get(var)
        if _auto_equal(student_val, expected_val, tol):
            matched += 1
        elif student_val is None:
            misses.append(f"`{var}` not set")
        else:
            misses.append(f"`{var}`: expected {expected_val!r}, got {student_val!r}")

    # scoring: all_or_nothing (default) — full only if every gradable var matches;
    # proportional — points × matched/total. The pda-admin editor sends the choice.
    scoring = str(cfg.get("scoring") or "all_or_nothing").lower()
    if scoring == "proportional":
        score = round(pts * matched / total, 4)
    else:
        score = pts if matched == total else 0.0

    fb = "Correct." if matched == total else f"{matched}/{total} correct. " + "; ".join(misses)
    if ungradable:
        # provisional score, but flag for human review of the non-comparable vars
        note = ", ".join(f"`{v}`" for v in ungradable)
        return QResult(qid, score, pts, "review",
                       (fb + f" Note: {note} could not be auto-graded (non-comparable answer key) "
                        "— needs human review.")[:500])
    return QResult(qid, score, pts, _verdict(score, pts), fb[:500])
