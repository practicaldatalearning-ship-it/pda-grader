"""Tag -> grader dispatch.

Each grader is `grade(question: dict, ctx: GradeContext) -> QResult`.
Objective tags (exact/set/property/output_match/tests/prediction) are here;
written/task delegate to the LLM-judge (grader.llm_judge).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class GradeContext:
    """Everything a grader may need for one submission."""
    answers: dict[str, Any] = field(default_factory=dict)      # var_name -> dumped value
    artifacts: dict[str, bytes] = field(default_factory=dict)  # out_dir files (predictions.csv, tests.json…)
    labels: dict[str, bytes] = field(default_factory=dict)     # question_id -> hidden label CSV bytes
    data_files: dict[str, bytes] = field(default_factory=dict) # data file name -> bytes (for `property`)
    tests_results: dict[str, float] = field(default_factory=dict)  # question_id -> fraction passed
    judge: Optional[Callable] = None                           # llm_judge.judge or None


@dataclass
class QResult:
    question_id: str
    score: float
    max: float
    verdict: str        # 'pass' | 'partial' | 'fail' | 'review' | 'error'
    feedback: str

    def as_dict(self) -> dict:
        return {
            "question_id": self.question_id, "score": round(float(self.score), 4),
            "max": float(self.max), "verdict": self.verdict, "feedback": self.feedback,
        }


def _verdict(score: float, mx: float) -> str:
    if mx <= 0:
        return "pass"
    if score >= mx:
        return "pass"
    if score <= 0:
        return "fail"
    return "partial"


# populated at the bottom once the grader fns are imported
GRADERS: dict[str, Callable] = {}


def grade_question(question: dict, ctx: GradeContext) -> QResult:
    tag = (question.get("tag") or "").strip()
    qid = str(question.get("id"))
    pts = float(question.get("points") or 0)
    fn = GRADERS.get(tag)
    if fn is None:
        return QResult(qid, 0.0, pts, "error", f"Unknown question tag: {tag!r}")
    try:
        return fn(question, ctx)
    except Exception as e:  # a single question never crashes the batch
        return QResult(qid, 0.0, pts, "error", f"Grader error: {e}")


# --- register objective graders (import after types are defined) ---
from . import exact, set_, property_, output_match, tests_, prediction, written, task  # noqa: E402

GRADERS.update({
    "exact": exact.grade,
    "set": set_.grade,
    "property": property_.grade,
    "output_match": output_match.grade,
    "tests": tests_.grade,
    "prediction": prediction.grade,
    "written": written.grade,
    "task": task.grade,
})
