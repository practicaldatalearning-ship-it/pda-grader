"""`auto` — grade a cell against the answer key automatically.

config:   {tolerance?}
expected: {"values": {var_name: solution_value, ...}}

The variables were parsed from the graded cell of the answer key at authoring
time (extract.cell_assign_targets) and their solution values captured. Here we
compare the student's dumped values for the SAME variables, by type:
  * numbers   -> equal within `tolerance`
  * lists/tuples/arrays -> elementwise
  * DataFrames -> same columns, cells compared elementwise
  * dicts      -> same keys + equal values
  * strings/bools/None -> normalized equality
Score is proportional to how many of the cell's variables match.
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
    if not values:
        return QResult(qid, 0.0, pts, "error",
                       "No expected values captured — re-publish so the grader can author this cell.")

    tol = float(cfg.get("tolerance") or 0)
    total = len(values)
    matched = 0
    misses: list[str] = []
    for var, expected_val in values.items():
        student_val = ctx.answers.get(var)
        if _auto_equal(student_val, expected_val, tol):
            matched += 1
        elif student_val is None:
            misses.append(f"`{var}` not set")
        else:
            misses.append(f"`{var}`: expected {expected_val!r}, got {student_val!r}")

    score = round(pts * matched / total, 4) if total else 0.0
    fb = "Correct." if matched == total else f"{matched}/{total} correct. " + "; ".join(misses)
    return QResult(qid, score, pts, _verdict(score, pts), fb[:500])
