"""Entrypoint: claim → grade loop → write. Also runs pending author jobs (G0b)
and requeues stuck submissions (G7). Idempotent and crash-safe: one bad
submission never blocks the batch; a killed run leaves rows to be requeued.

    python -m grader.main
"""
from __future__ import annotations

import logging
import os
import posixpath
import tempfile
from pathlib import Path
from typing import Any, Optional

import nbformat

from .config import Config, load_config
from .extract import (inject_dump, inject_tests, read_answers, cell_assign_targets, cell_source)
from .graders import GradeContext, grade_question
from .llm_judge import make_judge
from .runner import RunResult, run_notebook
from .supa import Supa

logging.basicConfig(
    level=os.environ.get("GRADER_LOG", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("grader.main")

WRITTEN_TAGS = {"written", "task"}


def _err_detail(res: RunResult, fallback: str) -> str:
    """A DB-storable error string that includes the tail of the sandbox stderr
    (the real papermill traceback) so a failure is self-diagnosing, not opaque."""
    detail = res.error or fallback
    tail = (res.stderr or "").strip()
    if tail:
        detail = f"{detail} :: {tail[-400:]}"
    return detail[:500]


def _basename(path: str) -> str:
    return posixpath.basename(path or "")


# ---------------------------------------------------------------------------
# Grading one submission
# ---------------------------------------------------------------------------
def grade_submission(supa: Supa, cfg: Config, submission_id: str, judge) -> None:
    bundle = supa.submission_bundle(submission_id)
    questions: list[dict] = bundle.get("questions") or []
    assignment = bundle.get("assignment") or {}

    with tempfile.TemporaryDirectory(prefix="pda-grade-") as td:
        work = Path(td) / "work"
        work.mkdir(parents=True, exist_ok=True)

        # 1) download student notebook + assignment data
        nb_handle = bundle["notebook"]
        nb_bytes = supa.download(nb_handle["bucket"], nb_handle["path"])
        nb = nbformat.reads(nb_bytes.decode("utf-8", "replace"), as_version=4)

        data_files: dict[str, bytes] = {}
        for d in bundle.get("data") or []:
            try:
                b = supa.download(d["bucket"], d["path"])
                name = _basename(d["path"])
                data_files[name] = b
                (work / name).write_bytes(b)
            except Exception as e:
                log.warning("data download failed %s: %s", d.get("path"), e)
        # student's extra uploads (e.g. their own predictions.csv) land in /work
        for x in bundle.get("extra") or []:
            try:
                b = supa.download(x["bucket"], x["path"])
                (work / _basename(x["path"])).write_bytes(b)
            except Exception as e:
                log.warning("extra download failed %s: %s", x.get("path"), e)

        # hidden labels for prediction questions
        labels: dict[str, bytes] = {}
        for qid, lh in (bundle.get("labels") or {}).items():
            try:
                labels[qid] = supa.download(lh["bucket"], lh["path"])
            except Exception as e:
                log.warning("label download failed q=%s: %s", qid, e)

        # 2) inject answer-dump + hidden tests, write the runnable notebook.
        # Explicit var_names + the 'auto' vars captured at authoring (stored in expected).
        var_names = [q.get("var_name") for q in questions if q.get("var_name")]
        for q in questions:
            tag = q.get("tag") or ""
            exp = q.get("expected") or {}
            if tag == "auto":
                var_names += list((exp.get("values") or {}).keys())
            elif tag == "written":
                var_names += list(exp.get("vars") or [])
        nb = inject_dump(nb, var_names)
        tests_by_q = {str(q["id"]): (q.get("config") or {}).get("tests", "")
                      for q in questions if q.get("tag") == "tests" and (q.get("config") or {}).get("tests")}
        if tests_by_q:
            nb = inject_tests(nb, tests_by_q)
        nbformat.write(nb, str(work / "nb.ipynb"))

        # 3) run in the sandbox
        res: RunResult = run_notebook(cfg.docker_image, work, timeout=cfg.run_timeout)
        if not res.ok and not res.artifacts.get("answers.json"):
            # total failure — no answers dumped: record error, keep the batch moving
            supa.write_result(submission_id, 0.0, [], "error",
                              _err_detail(res, "notebook failed to execute"))
            log.info("submission %s -> error (%s)", submission_id, res.error)
            return

        # 4) extract answers + test results + predictions
        answers = read_answers(res.artifacts.get("answers.json"))
        tests_results = read_answers(res.artifacts.get("tests.json"))
        ctx = GradeContext(
            answers=answers,
            artifacts=res.artifacts,
            labels=labels,
            data_files=data_files,
            tests_results={k: float(v) for k, v in tests_results.items()},
            judge=judge,
        )

        # 5) grade each question
        per_question: list[dict] = []
        total = 0.0
        review_flags: list[tuple[str, str, float]] = []
        for q in questions:
            r = grade_question(q, ctx)
            total += r.score
            per_question.append(r.as_dict())
            if r.verdict == "review":
                review_flags.append((r.question_id, "llm_low_confidence", r.score))

        # 6) write result. Anything flagged → needs_review (provisional result still
        # persisted); a clean run → graded (which also rolls up lesson progress).
        status = "needs_review" if review_flags else "graded"
        supa.write_result(submission_id, round(total, 4), per_question, status)
        for qid, reason, suggested in review_flags:
            try:
                supa.flag_review(submission_id, qid, reason, suggested)
            except Exception as e:
                log.warning("flag_review failed %s/%s: %s", submission_id, qid, e)
        log.info("submission %s -> %s  score=%.2f/%s", submission_id, status, total,
                 assignment.get("total_points"))


# ---------------------------------------------------------------------------
# Author job (G0b): run the solution to capture expected answers + strip solutions
# ---------------------------------------------------------------------------
SOLUTION_MARKERS = ("# SOLUTION", "### SOLUTION", "# YOUR CODE HERE", "# BEGIN SOLUTION")


def _strip_solution_cell(src: str) -> str:
    """Replace a tagged answer cell's body with a student placeholder.

    Honours nbgrader-style ``# BEGIN SOLUTION`` / ``# END SOLUTION`` fences when
    present; otherwise replaces the whole cell with a stub.
    """
    if "# BEGIN SOLUTION" in src and "# END SOLUTION" in src:
        out, skip = [], False
        for ln in src.splitlines():
            if ln.strip() == "# BEGIN SOLUTION":
                skip = True
                out.append("# YOUR CODE HERE")
                continue
            if ln.strip() == "# END SOLUTION":
                skip = False
                continue
            if not skip:
                out.append(ln)
        return "\n".join(out)
    return "# YOUR CODE HERE\n"


def author_assignment(supa: Supa, cfg: Config, job: dict) -> None:
    assignment = job.get("assignment") or {}
    aid = str(assignment.get("id"))
    questions: list[dict] = job.get("questions") or []
    try:
        with tempfile.TemporaryDirectory(prefix="pda-author-") as td:
            work = Path(td) / "work"
            work.mkdir(parents=True, exist_ok=True)

            sol = job["solution"]
            sol_bytes = supa.download(sol["bucket"], sol["path"])
            nb = nbformat.reads(sol_bytes.decode("utf-8", "replace"), as_version=4)

            for d in job.get("data") or []:
                try:
                    (work / _basename(d["path"])).write_bytes(supa.download(d["bucket"], d["path"]))
                except Exception as e:
                    log.warning("author data dl failed %s: %s", d.get("path"), e)

            # Derive the vars to dump: explicit var_names + (for 'auto') the
            # assignment targets parsed from each graded cell of the SOLUTION.
            var_names = [q.get("var_name") for q in questions if q.get("var_name")]
            auto_vars: dict[str, list[str]] = {}
            for q in questions:
                tag = q.get("tag") or ""
                # 'auto' + var-name-free 'written' derive their vars from the cell.
                if tag == "auto" or (tag == "written" and not q.get("var_name")):
                    vs = cell_assign_targets(cell_source(nb, q.get("cell_ref")))
                    auto_vars[str(q["id"])] = vs
                    var_names += vs
            run_nb = inject_dump(nb, var_names)
            nbformat.write(run_nb, str(work / "nb.ipynb"))
            res = run_notebook(cfg.docker_image, work, timeout=cfg.run_timeout)
            if not res.ok and not res.artifacts.get("answers.json"):
                supa.write_authored(aid, None, {}, "error",
                                    _err_detail(res, "solution notebook failed"))
                log.info("author %s -> error (%s)", aid, res.error)
                return
            answers = read_answers(res.artifacts.get("answers.json"))

            # capture expected per question by tag
            expected: dict[str, Any] = {}
            for q in questions:
                qid = str(q["id"])
                tag = q.get("tag")
                if tag in ("exact", "output_match"):
                    expected[qid] = {"value": answers.get(q.get("var_name"))}
                elif tag == "auto":
                    # snapshot every variable this cell assigns in the solution
                    expected[qid] = {"values": {v: answers.get(v) for v in auto_vars.get(qid, [])}}
                elif tag == "written" and not q.get("var_name"):
                    # remember which vars hold the (student's) explanation to judge
                    expected[qid] = {"vars": auto_vars.get(qid, [])}
                # prediction/task/set/property/tests: expected stays null (config-driven)

            # strip solutions -> student notebook
            answer_cell_refs = {q.get("cell_ref") for q in questions}
            student_nb = nbformat.from_dict(nb)
            cells = []
            for idx, cell in enumerate(student_nb.cells):
                ref = str(idx)
                if cell.get("cell_type") == "code" and (ref in answer_cell_refs or
                        cell.get("metadata", {}).get("cell_ref") in answer_cell_refs):
                    cell = nbformat.from_dict(cell)
                    cell.source = _strip_solution_cell(cell.get("source", ""))
                    cell.outputs = []
                    cell.execution_count = None
                cells.append(cell)
            student_nb.cells = cells

            # Keep an admin-provided question notebook; otherwise generate one
            # from the stripped answer key. (write_authored coalesces None -> keep.)
            existing_student = (assignment.get("student_notebook_path") or "").strip()
            if existing_student:
                supa.write_authored(aid, None, expected, "ready")
                log.info("author %s -> ready (kept admin question nb; %d expected)", aid, len(expected))
            else:
                student_path = f"{aid}/student-notebook.ipynb"
                supa.upload("assignment-content", student_path,
                            nbformat.writes(student_nb).encode("utf-8"), "application/x-ipynb+json")
                supa.write_authored(aid, student_path, expected, "ready")
                log.info("author %s -> ready (%d expected captured)", aid, len(expected))
    except Exception as e:
        log.exception("author job %s failed", aid)
        try:
            supa.write_authored(aid, None, {}, "error", str(e)[:500])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Batch entrypoint
# ---------------------------------------------------------------------------
def run_once(cfg: Optional[Config] = None) -> dict:
    cfg = cfg or load_config()
    supa = Supa(cfg)
    judge = make_judge(cfg)

    # G7: reap stuck rows before claiming (crash recovery).
    try:
        n = supa.requeue_stuck("1 hour")
        if n:
            log.info("requeued %d stuck submission(s)", n)
    except Exception as e:
        log.warning("reaper skipped: %s", e)

    authored = 0
    if cfg.grade_author_jobs:
        try:
            for job in supa.claim_author_jobs(cfg.batch):
                author_assignment(supa, cfg, job)
                authored += 1
        except Exception as e:
            log.warning("author phase error: %s", e)

    graded = 0
    errors = 0
    for row in supa.claim_batch(cfg.batch):
        sid = str(row.get("id"))
        try:
            grade_submission(supa, cfg, sid, judge)
            graded += 1
        except Exception as e:
            errors += 1
            log.exception("submission %s crashed", sid)
            try:
                supa.write_result(sid, 0.0, [], "error", str(e)[:500])
            except Exception:
                pass
    summary = {"authored": authored, "graded": graded, "errors": errors}
    log.info("batch done: %s", summary)
    return summary


def main() -> int:
    run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
