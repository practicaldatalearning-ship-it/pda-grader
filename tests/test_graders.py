"""Unit tests per tag — exercise the graders directly (no Docker needed).

Each test builds a GradeContext (as main.py would after a sandbox run) and asserts
score/verdict for happy, wrong, and edge cases. Run: `pytest -q`.
"""
from __future__ import annotations

import io

import pandas as pd
import pytest

from grader.graders import GradeContext, grade_question


def q(tag, points, *, var_name="ans", config=None, expected=None, qid="q1", cell_ref="1"):
    return {"id": qid, "cell_ref": cell_ref, "var_name": var_name, "tag": tag,
            "points": points, "config": config or {}, "expected": expected}


# --- exact ----------------------------------------------------------------
def test_exact_numeric_within_tolerance():
    ctx = GradeContext(answers={"ans": 42.01})
    r = grade_question(q("exact", 2, config={"tolerance": 0.1}, expected={"value": 42.0}), ctx)
    assert r.score == 2 and r.verdict == "pass"


def test_exact_numeric_wrong():
    ctx = GradeContext(answers={"ans": 50})
    r = grade_question(q("exact", 2, expected={"value": 42.0}), ctx)
    assert r.score == 0 and r.verdict == "fail"


def test_exact_string_normalized():
    ctx = GradeContext(answers={"ans": "  Income "})
    r = grade_question(q("exact", 1, expected={"value": "income"}), ctx)
    assert r.score == 1


def test_exact_missing_value():
    ctx = GradeContext(answers={})
    r = grade_question(q("exact", 1, expected={"value": 1}), ctx)
    assert r.score == 0 and r.verdict == "fail"


# --- set ------------------------------------------------------------------
def test_set_accepted():
    ctx = GradeContext(answers={"ans": "age"})
    r = grade_question(q("set", 1, config={"accepted": ["income", "age"]}), ctx)
    assert r.score == 1


def test_set_rejected():
    ctx = GradeContext(answers={"ans": "zipcode"})
    r = grade_question(q("set", 1, config={"accepted": ["income", "age"]}), ctx)
    assert r.score == 0


# --- property -------------------------------------------------------------
def _csv_bytes():
    df = pd.DataFrame({"x": [1, 2, 3, 4, 5], "y": [2, 4, 6, 8, 10], "z": [5, 3, 6, 2, 9]})
    return {"train.csv": df.to_csv(index=False).encode()}


def test_property_corr_pass():
    ctx = GradeContext(answers={"ans": "x"}, data_files=_csv_bytes())
    r = grade_question(q("property", 2, config={"property": "corr>0.9", "target": "y"}), ctx)
    assert r.score == 2  # x is perfectly correlated with y


def test_property_corr_fail():
    ctx = GradeContext(answers={"ans": "z"}, data_files=_csv_bytes())
    r = grade_question(q("property", 2, config={"property": "corr>0.9", "target": "y"}), ctx)
    assert r.score == 0


def test_property_in_range():
    ctx = GradeContext(answers={"ans": 0.75})
    r = grade_question(q("property", 1, config={"property": "in_range", "min": 0.5, "max": 1.0}), ctx)
    assert r.score == 1


def test_property_nunique():
    ctx = GradeContext(answers={"ans": "z"}, data_files=_csv_bytes())
    r = grade_question(q("property", 1, config={"property": "nunique==5"}), ctx)
    assert r.score == 1


# --- output_match ---------------------------------------------------------
def test_output_match_scalar():
    ctx = GradeContext(answers={"ans": 3.14159})
    r = grade_question(q("output_match", 2, config={"tolerance": 0.001}, expected={"value": 3.1416}), ctx)
    assert r.score == 2


def test_output_match_dataframe():
    df_dump = {"__df__": True, "columns": ["a", "b"], "rows": [[1, "x"], [2, "y"]]}
    ctx = GradeContext(answers={"ans": df_dump})
    r = grade_question(q("output_match", 3, expected={"value": df_dump}), ctx)
    assert r.score == 3


def test_output_match_dataframe_unordered():
    student = {"__df__": True, "columns": ["a"], "rows": [[2], [1]]}
    expected = {"__df__": True, "columns": ["a"], "rows": [[1], [2]]}
    ctx = GradeContext(answers={"ans": student})
    r = grade_question(q("output_match", 2, config={"unordered": True}, expected={"value": expected}), ctx)
    assert r.score == 2


# --- tests ----------------------------------------------------------------
def test_tests_full_pass():
    ctx = GradeContext(tests_results={"q1": 1.0})
    r = grade_question(q("tests", 4, config={"tests": "assert True"}), ctx)
    assert r.score == 4 and r.verdict == "pass"


def test_tests_partial():
    ctx = GradeContext(tests_results={"q1": 0.5})
    r = grade_question(q("tests", 4, config={"tests": "assert x"}), ctx)
    assert r.score == 2 and r.verdict == "partial"


def test_tests_did_not_run():
    ctx = GradeContext(tests_results={})
    r = grade_question(q("tests", 4, config={"tests": "assert x"}), ctx)
    assert r.verdict == "error"


# --- prediction -----------------------------------------------------------
def test_prediction_rmse_full():
    preds = pd.DataFrame({"id": [1, 2, 3], "target": [100, 200, 300]})
    labels = pd.DataFrame({"id": [1, 2, 3], "target": [110, 190, 305]})
    ctx = GradeContext(
        artifacts={"predictions.csv": preds.to_csv(index=False).encode()},
        labels={"q1": labels.to_csv(index=False).encode()},
    )
    cfg = {"metric": "rmse", "id_col": "id", "target_col": "target",
           "thresholds": [[30, "full"], [50, 5], [100, 2]]}
    r = grade_question(q("prediction", 10, config=cfg), ctx)
    assert r.score == 10  # rmse ~8.5 < 30


def test_prediction_rmse_partial():
    preds = pd.DataFrame({"id": [1, 2, 3], "target": [100, 200, 300]})
    labels = pd.DataFrame({"id": [1, 2, 3], "target": [160, 260, 360]})  # rmse ~60
    ctx = GradeContext(
        artifacts={"predictions.csv": preds.to_csv(index=False).encode()},
        labels={"q1": labels.to_csv(index=False).encode()},
    )
    cfg = {"metric": "rmse", "id_col": "id", "target_col": "target",
           "thresholds": [[30, "full"], [50, 5], [100, 2]]}
    r = grade_question(q("prediction", 10, config=cfg), ctx)
    assert r.score == 2  # 60 > 50, <= 100


def test_prediction_thresholds_object_form():
    # the admin authoring UI writes {"pass": N}, not a list of pairs
    preds = pd.DataFrame({"id": [1, 2, 3], "price": [100000, 200000, 300000]})
    labels = pd.DataFrame({"id": [1, 2, 3], "price": [110000, 190000, 305000]})  # rmse ~8.5k
    ctx = GradeContext(
        artifacts={"predictions.csv": preds.to_csv(index=False).encode()},
        labels={"q1": labels.to_csv(index=False).encode()},
    )
    cfg = {"id_col": "id", "target_col": "price", "thresholds": {"pass": 30000}}  # metric defaults to rmse
    r = grade_question(q("prediction", 50, config=cfg), ctx)
    assert r.score == 50 and r.verdict == "pass"


def test_prediction_thresholds_object_form_fail():
    preds = pd.DataFrame({"id": [1, 2, 3], "price": [100000, 200000, 300000]})
    labels = pd.DataFrame({"id": [1, 2, 3], "price": [160000, 260000, 360000]})  # rmse ~60k
    ctx = GradeContext(
        artifacts={"predictions.csv": preds.to_csv(index=False).encode()},
        labels={"q1": labels.to_csv(index=False).encode()},
    )
    cfg = {"id_col": "id", "target_col": "price", "thresholds": {"pass": 30000}}
    r = grade_question(q("prediction", 50, config=cfg), ctx)
    assert r.score == 0 and r.verdict == "fail"


def test_prediction_missing_predictions():
    ctx = GradeContext(labels={"q1": b"id,target\n1,2\n"})
    r = grade_question(q("prediction", 10, config={"metric": "rmse"}), ctx)
    assert r.score == 0 and r.verdict == "fail"


def test_prediction_accuracy_higher_is_better():
    preds = pd.DataFrame({"id": [1, 2, 3, 4], "target": [1, 0, 1, 1]})
    labels = pd.DataFrame({"id": [1, 2, 3, 4], "target": [1, 0, 1, 0]})  # 3/4 = 0.75
    ctx = GradeContext(
        artifacts={"predictions.csv": preds.to_csv(index=False).encode()},
        labels={"q1": labels.to_csv(index=False).encode()},
    )
    cfg = {"metric": "accuracy", "id_col": "id", "target_col": "target",
           "thresholds": [[0.9, "full"], [0.7, 5], [0.5, 2]]}
    r = grade_question(q("prediction", 10, config=cfg), ctx)
    assert r.score == 5  # 0.75 >= 0.7 but < 0.9


# --- written (LLM-judge, mocked) -----------------------------------------
class _FakeJudge:
    def __init__(self, score, mx, confidence):
        self._score, self._mx, self._conf = score, mx, confidence

    def judge(self, question_prompt, rubric, student_answer, solution="", qtype="explanation"):
        from grader.llm_judge import JudgeResult
        return JudgeResult(score=self._score, max=self._mx, confidence=self._conf,
                           feedback="mock feedback", per_item=[])


def test_written_high_confidence_scores():
    ctx = GradeContext(answers={"ans": "Because variance rises with the mean."},
                       judge=_FakeJudge(score=2, mx=2, confidence=1.0))
    r = grade_question(q("written", 3, config={"rubric": [{"text": "correct", "points": 2}]}), ctx)
    assert r.verdict == "pass" and r.score == 3  # scaled 2/2 * 3


def test_written_low_confidence_review():
    ctx = GradeContext(answers={"ans": "maybe something"},
                       judge=_FakeJudge(score=1, mx=2, confidence=0.3))
    r = grade_question(q("written", 3, config={"rubric": [{"text": "a", "points": 1}, {"text": "b", "points": 1}]}), ctx)
    assert r.verdict == "review"


def test_written_no_judge_goes_to_review():
    ctx = GradeContext(answers={"ans": "text"}, judge=None)
    r = grade_question(q("written", 3, config={"rubric": []}), ctx)
    assert r.verdict == "review"


def test_written_empty_answer_fails():
    ctx = GradeContext(answers={"ans": ""}, judge=_FakeJudge(2, 2, 1.0))
    r = grade_question(q("written", 3, config={"rubric": []}), ctx)
    assert r.verdict == "fail"


# --- task -----------------------------------------------------------------
def test_task_file_completion():
    ctx = GradeContext(artifacts={"predictions.csv": b"id,target\n1,2\n"})
    r = grade_question(q("task", 2, config={"require": "file", "require_file": "predictions.csv"}), ctx)
    assert r.score == 2 and r.verdict == "pass"


def test_task_missing_required_file():
    ctx = GradeContext(artifacts={})
    r = grade_question(q("task", 2, config={"require": "file", "require_file": "predictions.csv"}), ctx)
    assert r.score == 0 and r.verdict == "fail"


# --- dispatch / robustness ------------------------------------------------
def test_unknown_tag_is_error_not_crash():
    ctx = GradeContext()
    r = grade_question(q("nonsense", 5), ctx)
    assert r.verdict == "error" and r.score == 0


def test_grader_exception_is_contained():
    # expected missing -> exact returns error verdict, never raises
    ctx = GradeContext(answers={"ans": 1})
    r = grade_question(q("exact", 2, expected=None), ctx)
    assert r.verdict == "error"


# --- mixed assignment totals (end-to-end-ish over graders) ----------------
def test_mixed_assignment_total():
    data = _csv_bytes()
    preds = pd.DataFrame({"id": [1, 2, 3], "target": [100, 200, 300]})
    labels = pd.DataFrame({"id": [1, 2, 3], "target": [105, 205, 295]})
    ctx = GradeContext(
        answers={"n_missing": 0, "top_feat": "x", "ans_df": {"__df__": True, "columns": ["a"], "rows": [[1]]}},
        data_files=data,
        artifacts={"predictions.csv": preds.to_csv(index=False).encode()},
        labels={"qp": labels.to_csv(index=False).encode()},
        tests_results={"qt": 1.0},
    )
    questions = [
        q("exact", 2, var_name="n_missing", expected={"value": 0}, qid="qe"),
        q("property", 2, var_name="top_feat", config={"property": "corr>0.9", "target": "y"}, qid="qpr"),
        q("output_match", 2, var_name="ans_df",
          expected={"value": {"__df__": True, "columns": ["a"], "rows": [[1]]}}, qid="qo"),
        q("tests", 4, config={"tests": "assert True"}, qid="qt"),
        q("prediction", 10, config={"metric": "rmse", "id_col": "id", "target_col": "target",
                                    "thresholds": [[30, "full"]]}, qid="qp"),
    ]
    total = sum(grade_question(qq, ctx).score for qq in questions)
    assert total == 20  # 2 + 2 + 2 + 4 + 10
