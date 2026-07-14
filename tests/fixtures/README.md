# Fixtures — a full synthetic assignment + a student submission

**All synthetic. No real data / answers / student notebooks (STRICT-INSTRUCTIONS §1.8).**

This mirrors what the grader consumes at run time, so you can trace an end-to-end grade
without a live DB:

- `questions.json` — the tagged questions (as `grader_submission_bundle` returns them),
  mixing `exact`, `property`, `prediction`, and `written`.
- `solution.ipynb` — the instructor's solved notebook (author job runs this to capture
  `expected`). Produces `n_missing`, `top_feature`, and `predictions.csv`.
- `student_good.ipynb` — a correct student submission.
- `train.csv` / `labels.csv` — synthetic data + hidden labels.

The Docker-based end-to-end path (`runner.run_notebook`) needs the sandbox image and a
Linux Docker host, so it runs in CI (the `grade` workflow), not in the unit tests. The
unit tests (`tests/test_graders.py`) exercise every tag grader directly against a
`GradeContext` — no Docker required — and are the G3 DoD gate.
