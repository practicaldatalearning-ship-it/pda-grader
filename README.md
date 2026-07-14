# pda-grader

**A free, no-server auto-grader for data-science / ML / DL notebook assignments.**

Students train and run their heavy work on their *own* machine (or free Kaggle/Colab), then submit
a finished Jupyter notebook. This grader runs in **GitHub Actions** (no server, no credit card),
executes each submission in a locked-down sandbox, and grades it automatically — objective cells by
comparison to an instructor solution, predictions by a metric, and written answers by an LLM-judge.

> ⚠️ **This repo is PUBLIC. Read [`STRICT-INSTRUCTIONS.md`](./STRICT-INSTRUCTIONS.md) before any
> commit or push, and run `./scripts/security-check.sh` before every push.** No secret, private
> key, or real student data ever belongs in this repository.

## The problem it solves
Auto-grading *heavy* ML/DL assignments (not just tiny code snippets) is usually expensive — you
either pay a code-execution API or run GPU servers. This flips it:
- **Training happens on the student's side** → your grading cost is near-zero.
- **Grading = compare-to-solution + a metric + an LLM-judge** → cheap, deterministic, no human.
- **Compute = GitHub Actions on a public repo** → unlimited free minutes, no credit card, ephemeral
  isolated runners.
- **A throttled hourly batch (10–20 submissions/run)** → a hard, predictable cost ceiling; a backlog
  just waits, it never spikes.

## How it works (high level)
```
Instructor: upload a solved notebook + tag the answer cells (exact / prediction / written / …)
Student:    download the question notebook → do the work → upload the finished .ipynb
Trigger:    an hourly cron fires this workflow (via GitHub's dispatch API)
Grader:     claim 10–20 queued submissions → run each in a --network-none, capped, ephemeral
            container → grade by tag → write score + per-question feedback back to the datastore
```
Full architecture, data model, question tags, and UI live in the PDA strategy docs
(`assignment-engine-BUILD-PLAN.md`).

## Question tags → grading strategy
`exact` · `set` · `property` · `output_match` · `tests` · `prediction` · `written` · `task`
(see the build plan for what each means and how it's graded).

## Security model (short version)
- Every credential is injected at run time from a secret store (GitHub Actions Secrets / Supabase
  Vault) — **never** in this repo. A ZIP download exposes only grading code, nothing sensitive.
- Student code runs sandboxed: `--network none`, non-root, resource + time caps, ephemeral.
- The grader has **least-privilege** datastore access (claim-work + write-result only), not a full
  admin key.
- A **mandatory pre-push security scan** (`scripts/security-check.sh`) + a CI check block secrets
  from ever landing.

See [`STRICT-INSTRUCTIONS.md`](./STRICT-INSTRUCTIONS.md) for the full, non-negotiable rules.

## Status
✅ **Engine built** (phases G0–G7). The grader claims a batch, runs each notebook in a
locked-down Docker sandbox, grades every tag (`exact` · `set` · `property` · `output_match`
· `tests` · `prediction` · `written` · `task`), writes scores + per-question feedback, rolls
a pass into lesson progress, and flags low-confidence written answers for review. Also runs
the **author job** (G0b): runs the instructor solution to capture expected answers + strips
solutions into the student notebook.

- Grader service: [`grader/`](./grader/) · sandbox: [`sandbox/Dockerfile`](./sandbox/Dockerfile)
- DB surface (grader role + `grader_*` RPCs + storage RLS): [`db/schema.sql`](./db/schema.sql)
- Workflow: [`.github/workflows/grade.yml`](./.github/workflows/grade.yml)
- Unit tests (every tag, Docker-free): [`tests/test_graders.py`](./tests/test_graders.py) — `pytest -q`
- **Go-live checklist:** [`doc/SETUP.md`](./doc/SETUP.md) · hourly trigger: [`doc/TRIGGER.md`](./doc/TRIGGER.md)
  · hardening: [`doc/HARDENING.md`](./doc/HARDENING.md)

**Remaining human steps** (need dashboard/secret access — not committable): apply
`db/schema.sql` to Supabase, mint `GRADER_KEY`, add the four GitHub Actions Secrets, and
schedule the `pg_cron` trigger. All are in [`doc/SETUP.md`](./doc/SETUP.md).

> **Authoring convention:** a `written`/`task` answer is captured as a Python **variable**
> (the question's `var_name`, e.g. `explanation = "..."`) via the injected answer-dump — the
> admin authoring UI scaffolds these cells; students edit the string.

## Quick start (contributors)
```bash
bash scripts/install-hooks.sh     # installs the pre-push security guard (once per clone)
bash scripts/security-check.sh    # run any time; MUST pass before you push
```

## License
TBD (intended to be open-source once hardened).
