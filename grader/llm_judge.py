"""LLM-judge for `written`/`task` cells — reuses the existing pda-coach worker.

Contract (verified against pda-app/pda-coach/src/index.ts):
    POST {COACH_URL}/grade   header x-coach-key: COACH_KEY
    body  { "items": [{qid, points, type, prompt, solution, answer}] }
    resp  { "ok": true, "results": [{qid, score(int 0..points), feedback}] }

The coach returns a per-item integer score + feedback but no confidence, so we
grade EACH RUBRIC ITEM as its own coach item (points = the item's weight) and
derive a confidence from the pattern of item scores: clean all-or-nothing =
high confidence; mixed partials = lower confidence → route to the review queue.
Any coach failure degrades gracefully to a review flag (never crashes the batch).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

log = logging.getLogger("grader.judge")


@dataclass
class JudgeResult:
    score: float
    max: float
    confidence: float
    feedback: str
    per_item: list[dict] = field(default_factory=list)
    ok: bool = True
    error: Optional[str] = None


class CoachJudge:
    def __init__(self, coach_url: str, coach_key: str,
                 session: Optional[requests.Session] = None, model: str = ""):
        self.url = coach_url.rstrip("/")
        self.key = coach_key
        self.s = session or requests.Session()
        self.model = model

    def _grade_items(self, items: list[dict]) -> dict[str, dict]:
        body = {"items": items}
        if self.model:
            body["model"] = self.model
        r = self.s.post(f"{self.url}/grade", json=body,
                        headers={"content-type": "application/json", "x-coach-key": self.key},
                        timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"coach /grade -> {r.status_code}")
        data = r.json()
        if not data.get("ok", False):
            raise RuntimeError(f"coach error: {str(data.get('error'))[:200]}")
        return {str(x.get("qid")): x for x in data.get("results", [])}

    def judge(self, question_prompt: str, rubric: list[dict], student_answer: str,
              solution: str = "", qtype: str = "explanation") -> JudgeResult:
        """rubric: [{text, points}]. Returns JudgeResult with summed score + confidence."""
        rubric = rubric or [{"text": "Answer is correct, clear, and complete.", "points": 1}]
        total = float(sum(float(it.get("points") or 0) for it in rubric)) or float(len(rubric))
        items = []
        for i, it in enumerate(rubric):
            items.append({
                "qid": f"r{i}",
                "points": int(it.get("points") or 1),
                "type": qtype,
                "prompt": f"{question_prompt}\n\nRubric criterion: {it.get('text', '')}",
                "solution": solution,
                "answer": student_answer or "",
            })
        results = self._grade_items(items)

        score = 0.0
        per_item = []
        fulls = zeros = 0
        for i, it in enumerate(rubric):
            r = results.get(f"r{i}", {})
            pts = float(it.get("points") or 1)
            s = max(0.0, min(pts, float(r.get("score") or 0)))
            score += s
            if s >= pts:
                fulls += 1
            elif s <= 0:
                zeros += 1
            per_item.append({"criterion": it.get("text", ""), "score": s, "max": pts,
                             "feedback": str(r.get("feedback") or "")})
        # Confidence: decisive (each item clearly full or zero) → high; lots of
        # middling partials → low. n items, d = decisive fraction.
        n = max(1, len(rubric))
        confidence = round((fulls + zeros) / n, 3)
        fb = "; ".join(f"{p['criterion']}: {p['feedback']}" for p in per_item if p["feedback"])[:1000] \
            or "Graded against the rubric."
        return JudgeResult(score=round(score, 4), max=total, confidence=confidence,
                           feedback=fb, per_item=per_item)


def make_judge(cfg) -> Optional[CoachJudge]:
    if not cfg.has_coach:
        return None
    return CoachJudge(cfg.coach_url, cfg.coach_key)
