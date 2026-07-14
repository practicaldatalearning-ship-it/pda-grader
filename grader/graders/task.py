"""`task` — open work (produce a plot, follow a style). Graded by a completion
check plus an optional LLM-judge rubric.
config: {rubric?: [{text, points}], require?: "figure"|"file", require_file?: "name"}
expected: null

Completion: if `require` is set, first verify the artifact exists (a produced
figure = the executed notebook has image output; a file = it appears in artifacts).
Then, if a rubric + judge are present, score against it. If neither → completion
alone (full marks for producing the required artifact).
"""
from __future__ import annotations

from . import GradeContext, QResult, _verdict


def _produced_figure(ctx: GradeContext) -> bool:
    # A produced plot leaves image/* output in the executed notebook.
    exe = ctx.artifacts.get("executed.ipynb")
    if not exe:
        return False
    try:
        import nbformat
        nb = nbformat.reads(exe.decode("utf-8"), as_version=4)
        for cell in nb.cells:
            for out in cell.get("outputs", []) or []:
                data = out.get("data", {}) or {}
                if any(k.startswith("image/") for k in data):
                    return True
    except Exception:
        return False
    return False


def grade(question: dict, ctx: GradeContext) -> QResult:
    qid = str(question.get("id"))
    pts = float(question.get("points") or 0)
    cfg = question.get("config") or {}
    require = (cfg.get("require") or "").strip().lower()
    rubric = cfg.get("rubric") or []

    produced = True
    detail = ""
    if require == "figure":
        produced = _produced_figure(ctx)
        detail = "figure produced" if produced else "no figure output found"
    elif require == "file":
        want = cfg.get("require_file") or "predictions.csv"
        produced = want in ctx.artifacts
        detail = f"{want} present" if produced else f"{want} not produced"

    if not produced:
        return QResult(qid, 0.0, pts, "fail", f"Required output missing ({detail}).")

    # Completion only.
    if not rubric or ctx.judge is None:
        return QResult(qid, pts, pts, "pass",
                       f"Completed{(' — ' + detail) if detail else ''}.")

    # Completion + rubric judge on the notebook's textual answer if any.
    answer = ctx.answers.get(question.get("var_name"))
    answer_text = "" if answer is None else (answer if isinstance(answer, str) else str(answer))
    try:
        jr = ctx.judge.judge(
            question_prompt=cfg.get("prompt") or "Assess the produced work.",
            rubric=rubric, student_answer=answer_text or "(see produced artifact)", qtype="task")
    except Exception as e:
        return QResult(qid, pts, pts, "review", f"Produced; rubric judge unavailable ({e}).")
    scaled = round(min(pts, max(0.0, pts * (jr.score / jr.max) if jr.max else pts)), 4)
    threshold = float(cfg.get("threshold") or 0.6)
    verdict = "review" if jr.confidence < threshold else _verdict(scaled, pts)
    return QResult(qid, scaled, pts, verdict, f"{detail}. {jr.feedback}".strip(". ") + ".")
