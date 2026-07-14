# pda-grader — full build runbook (autonomous)

> **For the assistant picking this up in a new session:** this is a complete, self-contained
> build plan. Read §0 first, then build phases **G0 → G7 in order**. Each phase has a
> **Definition of Done (DoD)** you must satisfy (with a test) before moving on. Make the sensible
> default choice wherever something is unspecified — do **not** wait for the user. Run
> `bash scripts/security-check.sh` before every commit/push and obey `STRICT-INSTRUCTIONS.md`.

---

## 0. Start here (read this whole section)

**What you are building:** `pda-grader` — a free, no-server auto-grader for heavy Jupyter-notebook
ML/DS/EDA assignments. It runs as a **GitHub Actions** workflow (public repo → unlimited free
minutes, no credit card), pulls queued submissions from Supabase in an hourly batch, runs each
notebook in a locked-down sandbox, grades every answer cell by its **tag**, and writes scores +
per-question feedback back to Supabase.

**Read these first (full context — they are the source of truth for design decisions):**
- `../pda-public/doc/strategy/assignment-engine-BUILD-PLAN.md` — the master plan (decisions,
  data model, tags, UI, architecture, trigger + security).
- `../pda-public/doc/strategy/assignment-types-and-grading.md` — the tag → grading-strategy detail.
- `../pda-public/doc/strategy/heavy-compute-autograding.md` — why GitHub Actions, cost model.
- `./STRICT-INSTRUCTIONS.md` — non-negotiable security rules for this PUBLIC repo.
- `./README.md` — the product one-pager.

**Tools you have:** filesystem + shell; the **Supabase MCP** (create schema/RPCs, run SQL,
inspect the live DB — use it for all DB work); the sibling repos `../pda-public` (student UI) and
`../pda-admin` (authoring UI). The shared DB already has the `mobile_le_*` learning model and the
`pda-coach` worker (LLM) + AI-credit wallet — reuse the coach for the LLM-judge.

**Hard rules while building (from STRICT-INSTRUCTIONS.md):**
1. No secret ever in the repo. All creds via env / GitHub Actions Secrets / Supabase Vault.
2. The grader gets **least privilege** — only the `grader_*` RPCs below, never the full
   `service_role` key in code.
3. Student code always runs sandboxed: `--network none`, non-root, CPU/RAM/PID/time caps, ephemeral.
4. Run `scripts/security-check.sh` before every push; a failure blocks the push.
5. Public repo: keep secret-scanning + push-protection ON; trigger only via dispatch, never PRs.

**Decisions already locked (do not re-ask the user):**
- Placement: assignments live in the lesson **Task tab** (student UI is in pda-public — see §9).
- Authoring: admin uploads a **solved `.ipynb`** and tags cells (admin UI in pda-admin — §9).
- Compute: **GitHub Actions**, public repo. Sandbox = Docker container in the runner.
- Trigger: **Supabase `pg_cron` + `pg_net` → GitHub `repository_dispatch`** (§ G6). Also allow
  `workflow_dispatch` for manual runs.
- Notebook execution: **papermill** (+ nbformat to inject cells).
- Batch size: **15** per run (configurable via `GRADER_BATCH` env, default 15).
- Pass mark for lesson-progress rollup: **60%** (configurable per assignment; default 60).
- Language of the grader: **Python 3.11**.

**Out of scope for THIS repo (build separately in the named repo — see §9 for the exact contract):**
- Student submit/results UI → `pda-public` (lesson Task tab).
- Admin "Create Assignment" authoring + review queue → `pda-admin`.
This runbook builds the **DB schema/RPCs (G0)** and the **grader service (G1–G7)** — the engine.

---

## 1. Target repo tree
```
pda-grader/
├── README.md                       (exists)
├── STRICT-INSTRUCTIONS.md          (exists)
├── BUILD-PLAN.md                   (this file)
├── .gitignore                      (exists)
├── requirements.txt                (grader runtime: supabase, requests, nbformat, papermill…)
├── .github/workflows/
│   ├── security-check.yml          (exists — required CI)
│   └── grade.yml                   (G5 — the grading workflow)
├── scripts/
│   ├── security-check.sh           (exists)
│   └── install-hooks.sh            (exists)
├── sandbox/
│   └── Dockerfile                  (G1 — image student notebooks execute in)
├── grader/
│   ├── __init__.py
│   ├── main.py                     (G2 — entrypoint: claim → grade loop → write)
│   ├── config.py                   (G2 — env loading; fail loudly if missing)
│   ├── supa.py                     (G2 — Supabase REST/RPC client + storage IO)
│   ├── runner.py                   (G2 — sandboxed notebook execution via Docker+papermill)
│   ├── extract.py                  (G2 — inject answer-dump cell; read tagged vars/outputs)
│   ├── metrics.py                  (G3 — rmse/mae/r2/accuracy/f1/precision/recall/auc/logloss…)
│   ├── llm_judge.py                (G4 — call pda-coach for written/task)
│   └── graders/
│       ├── __init__.py             (tag → grader dispatch table)
│       ├── exact.py  set_.py  property_.py  output_match.py
│       ├── tests_.py  prediction.py  written.py  task.py
├── db/
│   └── schema.sql                  (G0 — reference copy of the migration you apply via MCP)
└── tests/
    ├── fixtures/                   (G2 — a full sample assignment + a student submission)
    └── test_graders.py             (G3 — pytest per tag)
```

---

## 2. The data contract (what the grader consumes/produces)
Tables (created in G0). The grader only touches them via the `grader_*` RPCs.
- **`assignment_submissions`**: `id, assignment_id, user_id, notebook_path, extra_paths[],
  status('queued'|'grading'|'graded'|'error'|'needs_review'), attempt_no, submitted_at, graded_at`.
- **`assignment_questions`**: `id, assignment_id, cell_ref, var_name, tag, points,
  config(jsonb), expected(jsonb)`. `tag ∈ {exact,set,property,output_match,tests,prediction,
  written,task}`. `config`/`expected` per §4.
- **`assignments`**: `id, lesson_id, title, brief_md, student_notebook_path,
  solution_notebook_path, data_paths[], total_points, pass_mark(default 60),
  max_submissions_per_day(default 5), is_published`.
- **`assignment_results`**: `id, submission_id (unique), total_score, per_question(jsonb), graded_at`.
- **`review_queue`**: `id, submission_id, question_id, reason, suggested_score, status`.

Storage buckets (private): `assignment-content` (solution notebook, data, hidden labels — service
only) and `assignment-submissions` (student uploads). The grader downloads via signed URLs from the
`grader_*` RPCs (never expose raw paths client-side).

**Grader input per submission:** the student notebook + the assignment's questions (tags + config +
expected) + any hidden data (e.g. `labels.csv` for `prediction`).
**Grader output per submission:** `total_score` (0..total_points), `per_question` =
`[{question_id, score, max, verdict('pass'|'partial'|'fail'|'review'), feedback}]`, and a final
`status`.

---

## 3. Phases & Definition of Done

### G0 — Supabase schema, RPCs, buckets  (use the Supabase MCP)
Create the tables in §2, RLS (students read only their own submissions/results + the published
student notebook/data; solution/expected/labels are service-role only), the two private buckets,
and these **`grader_*` RPCs** (the grader's ONLY DB surface — grant EXECUTE to a dedicated
`grader` role / the key you'll store in GitHub Secrets; revoke from public):

```sql
-- Atomically claim a batch (concurrency-safe). Marks them 'grading' and returns them.
create or replace function public.grader_claim_batch(p_limit int default 15)
returns setof public.assignment_submissions
language plpgsql security definer set search_path=public as $$
begin
  return query
  update public.assignment_submissions s set status='grading'
  where s.id in (select id from public.assignment_submissions
                 where status='queued' order by submitted_at limit p_limit
                 for update skip locked)
  returning s.*;
end $$;

-- Fetch everything the grader needs for one submission (questions + signed URLs).
create or replace function public.grader_submission_bundle(p_submission uuid)
returns jsonb language plpgsql security definer set search_path=public as $$
  -- return { assignment, questions[], notebook_url, data_urls[], label_urls{} }
  -- using storage signed URLs (storage.create_signed_url) valid ~10 min.
$$;

-- Write the result + set final status; roll into lesson progress if >= pass_mark.
create or replace function public.grader_write_result(
  p_submission uuid, p_total numeric, p_per_question jsonb, p_status text, p_error text default null)
returns void language plpgsql security definer set search_path=public as $$
begin
  insert into public.assignment_results(submission_id,total_score,per_question)
    values (p_submission,p_total,p_per_question)
    on conflict (submission_id) do update
      set total_score=excluded.total_score, per_question=excluded.per_question, graded_at=now();
  update public.assignment_submissions set status=p_status, graded_at=now() where id=p_submission;
  -- if p_total/total_points >= assignments.pass_mark → mark the lesson task complete
  --   (reuse the existing progress plumbing; see mobile_le_* completion).
end $$;

-- Enqueue any low-confidence written answers for human review.
create or replace function public.grader_flag_review(
  p_submission uuid, p_question uuid, p_reason text, p_suggested numeric)
returns void language plpgsql security definer set search_path=public as $$ ... $$;
```
Save the SQL you apply to `db/schema.sql` (reference copy; the live change is via MCP
`apply_migration`). **DoD:** tables + RPCs exist; `grader_claim_batch(1)` returns rows for a
hand-seeded `queued` submission; RLS verified (a normal user cannot read solution/labels).

### G0b — Author job (authoring support for admin "Publish")
The hourly run ALSO processes `assignments` with `author_status='pending'` **before** grading
submissions: download the solution notebook + `assignment_questions`; **strip** each tagged answer
cell's solution → produce the student notebook (upload to `student_notebook_path`); **run** the
solution in the sandbox (G1/G2) and **capture** each question's `expected` (value for `exact/set`,
output for `output_match`, etc.) → update `assignment_questions.expected`; set `author_status='ready'`
+ `is_published=true` (or `author_status='error'` + message). Add RPCs
`grader_claim_author_jobs(p_limit)` + `grader_write_authored(p_assignment, p_student_nb_path,
p_expected jsonb, p_status)` mirroring the grading pair. (Consumed by the app-side admin authoring —
see `../pda-public/doc/strategy/assignment-app-BUILD-PLAN.md`.) **DoD:** a seeded solved notebook +
tagged questions → after a run, `student_notebook_path` + every `expected` are populated and
`author_status='ready'`.

### G1 — Sandbox image  (`sandbox/Dockerfile`)
A small image the student notebook executes inside. Non-root user; the data-science stack.
```dockerfile
FROM python:3.11-slim
RUN useradd -m -u 1000 runner
RUN pip install --no-cache-dir numpy pandas scikit-learn scipy matplotlib \
    papermill ipykernel nbformat && python -m ipykernel install --name python3
USER runner
WORKDIR /work
```
Build once per run in CI (cache by hash). **DoD:** `docker build -t pda-sandbox sandbox/` succeeds;
`docker run --rm --network none pda-sandbox python -c "import pandas,sklearn;print('ok')"` prints ok.

### G2 — Grader core (end-to-end on a trivial `exact` question)
Implement `config.py`, `supa.py`, `runner.py`, `extract.py`, `main.py`, and a `fixtures/` sample.

- **`config.py`** — load required env (fail loudly if missing): `SUPABASE_URL`, `GRADER_KEY`
  (the restricted key), `COACH_URL`, `COACH_KEY`, `GRADER_BATCH` (default 15). Never a literal.
- **`supa.py`** — thin client over Supabase REST: `claim_batch()`, `submission_bundle(id)`,
  `write_result(...)`, `flag_review(...)`, `download(url)->bytes`. Auth header from `GRADER_KEY`.
- **`runner.py`** — `run_notebook(nb_path, data_dir, out_dir, timeout=300) -> RunResult`:
  ```
  docker run --rm --network none --memory 2g --cpus 1 --pids-limit 256 --read-only \
    --tmpfs /tmp -v {work}:/work:ro -v {out}:/out -w /work --user 1000:1000 \
    pda-sandbox timeout {timeout} papermill /work/nb.ipynb /out/executed.ipynb -k python3
  ```
  Capture stdout/stderr + exit code; on timeout/error return `RunResult(ok=False, error=...)`.
- **`extract.py`** — before running, use `nbformat` to **append an answer-dump cell** that
  serializes each question's `var_name` to `/out/answers.json` (scalars as-is; DataFrame →
  `{columns, rows}`; ndarray/Series → list; unserializable → null). Also copy any produced
  `predictions.csv` from the notebook's writes. After the run, read `/out/answers.json`.
- **`main.py`** — `claim_batch → for each: bundle → download notebook+data → run → extract →
  grade (G3) → write_result`. On any exception: `write_result(..., status='error', error=...)`
  so a bad submission never blocks the batch. Idempotent: only claim `queued`.

**DoD:** with a fixture assignment that has one `exact` numeric question and a matching student
notebook, `python -m grader.main` (pointed at a test Supabase project or a seeded row) grades it and
writes `assignment_results` with the correct score. No secret in code.

### G3 — All objective tag graders + metrics + unit tests
Implement `graders/` with a dispatch table `{tag: grade_fn}` and `metrics.py`. Each grader gets
`(student_value, question)` and returns `(score, verdict, feedback)`.
- **`exact`**: equal to `expected` (± `config.tolerance` for numbers; case/whitespace-normalized for
  strings). Full points or 0.
- **`set`**: `student_value ∈ config.accepted`.
- **`property`**: run `config.property` (a whitelisted check, e.g. `corr>0.5`) against the student's
  answer + the dataset — pass/fail. (Define a small safe DSL or a fixed set of checks; NO `eval` of
  student-supplied code.)
- **`output_match`**: compare the student's produced output to `expected` output — DataFrame equality
  with float tolerance, order-insensitive if `config.unordered`.
- **`tests`**: inject `config.tests` (hidden assert cells) after the student's code, run in the
  sandbox, pass if no assertion fails; partial credit = fraction of asserts passed.
- **`prediction`**: download hidden labels + the student's `predictions.csv`; compute
  `config.metric` (rmse/mae/r2/accuracy/f1/precision/recall/auc/logloss) via `metrics.py`; map to
  score by `config.thresholds` (e.g. `[[30000,'full'],[40000,7],[60000,4]]`). Leaderboard rank
  optional.
Write `tests/test_graders.py` (pytest) covering each tag with fixtures (happy + wrong + edge).
**DoD:** `pytest -q` green; a fixture assignment mixing several tags grades to the expected total.

### G4 — Written/open grading via the LLM-judge + review queue
- **`llm_judge.py`** — `judge(question_prompt, rubric, student_answer) -> {score, per_item[],
  confidence}` by calling the **pda-coach** worker (reuse `COACH_URL`/`COACH_KEY` + AI wallet).
  Structured prompt: score each rubric item 0/1 + an overall confidence 0..1.
- **`written`**: score = Σ rubric items × (points / #items). If `confidence < 0.6` →
  `grader_flag_review(...)` and set the question verdict to `review` (still return a provisional
  score). **`task`**: same idea; for "produced a plot / followed style" use a completion check or a
  vision-judge later (start with completion + rubric).
**DoD:** a fixture written answer returns a sensible rubric score; a deliberately ambiguous one is
flagged into `review_queue`. LLM failures degrade gracefully (question → `review`, batch continues).

### G5 — GitHub Actions workflow (`.github/workflows/grade.yml`)
```yaml
name: grade
on:
  repository_dispatch: { types: [grade-batch] }
  workflow_dispatch:
concurrency: { group: grade, cancel-in-progress: false }
permissions: { contents: read }
jobs:
  grade:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - run: docker build -t pda-sandbox sandbox/
      - run: python -m grader.main
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          GRADER_KEY:   ${{ secrets.GRADER_KEY }}
          COACH_URL:    ${{ secrets.COACH_URL }}
          COACH_KEY:    ${{ secrets.COACH_KEY }}
```
Add the GitHub Actions **Secrets**: `SUPABASE_URL`, `GRADER_KEY`, `COACH_URL`, `COACH_KEY`.
**DoD:** a manual **Run workflow** (workflow_dispatch) drains a seeded queue and writes results; the
run logs show no secret values; `security-check.yml` passes.

### G6 — The hourly trigger (Supabase `pg_cron` + `pg_net`)
Store a fine-grained GitHub PAT (this repo only, `actions:write`) in **Supabase Vault** as
`GH_DISPATCH_TOKEN`, then:
```sql
select cron.schedule('grader-hourly','0 * * * *', $$
  select net.http_post(
    url    := 'https://api.github.com/repos/<owner>/pda-grader/dispatches',
    headers:= jsonb_build_object('Authorization','Bearer '||(select decrypted_secret from vault.decrypted_secrets where name='GH_DISPATCH_TOKEN'),
                                 'Accept','application/vnd.github+json','User-Agent','pda-grader'),
    body   := jsonb_build_object('event_type','grade-batch')) $$);
```
**DoD:** the cron fires the workflow hourly (verify one real fire); disabling the cron stops it.

### G7 — Hardening
Timeouts + retries (a stuck 'grading' > 1h is reset to 'queued' by a small reaper in `main.py` or a
cron); idempotency (never double-write results; `on conflict` handles it); resubmission cap
(enforced at submit time in pda-public, but the grader always grades the latest attempt); structured
logs (no secrets); a random **audit re-run** sample for anti-cheat (optional); result caching
(skip identical notebook hash). **DoD:** kill a run mid-batch → claimed rows get requeued and grade
on the next run; a malformed notebook yields `status='error'` with a helpful message, not a crash.

---

## 4. Tag config/expected shapes (author contract)
Each `assignment_questions` row:
```
exact        config:{tolerance?}                         expected:{value}
set          config:{accepted:[...]}                     expected:null
property     config:{property:"corr>0.5", target:"y"}    expected:null
output_match config:{unordered?, tolerance?}             expected:{columns:[...],rows:[[...]]}
tests        config:{tests:"<python assert cells>"}       expected:null
prediction   config:{metric:"rmse", thresholds:[[30000,"full"],[40000,7],[60000,4]], label_path}
written      config:{rubric:[{text,points}]}             expected:null
task         config:{rubric:[...], require:"figure|file"} expected:null
```

## 5. Testing (must pass before calling it done)
- `pytest -q` (unit graders) green.
- End-to-end fixture: seed one `assignments` row (mixed tags) + solution answers + one student
  submission → `python -m grader.main` → `assignment_results.total_score` equals the hand-computed
  expected. Include a correct, a partial, and an error submission.
- Security: `bash scripts/security-check.sh` passes; no secret in git history.

## 6. Definition of done (whole grader)
A queued submission, with **no human involvement**, is picked up within the hour, executed safely,
graded across all tags, and its score + per-question feedback appear in `assignment_results` (and
roll into lesson progress at ≥ pass_mark); low-confidence written answers land in `review_queue`;
the public repo holds zero secrets and the security check + CI are green.

## 7. Build order checklist (tick as you go)
- [x] G0 `grader_*` RPCs + grader role + storage RLS + indexes → `db/schema.sql`
      (tables/buckets/app+admin RPCs already existed in the `mobile` schema — verified live).
      **Live apply pending human go-ahead** (RBAC change on the shared backend; see doc/SETUP.md §1).
- [x] G0b author-job RPCs (`grader_claim_author_jobs`, `grader_write_authored`) + author flow in `main.py`
- [x] G1 `sandbox/Dockerfile`
- [x] G2 grader core (`config/supa/runner/extract/main`) + `tests/fixtures/`
- [x] G3 all objective graders + `metrics.py` + `tests/test_graders.py` (runs in CI; no local Python)
- [x] G4 LLM-judge (`written`/`task`) via pda-coach + review queue, graceful degrade
- [x] G5 `.github/workflows/grade.yml` (test + grade jobs). **GitHub Secrets pending** (doc/SETUP.md §3)
- [x] G6 trigger documented in `doc/TRIGGER.md`. **pg_cron schedule + Vault token pending** (human)
- [x] G7 hardening: reaper (`grader_requeue_stuck`), idempotency (unique index + on-conflict),
      per-submission/per-question failure containment, structured no-secret logs → `doc/HARDENING.md`
- [x] `scripts/security-check.sh` green. **Repo init / pre-push hook / make PUBLIC pending** (human, §5)

**What's left is all human-gated** (dashboard/secret access, not committable): apply the migration,
mint `GRADER_KEY`, add the 4 Actions Secrets, schedule the cron. Full runbook: `doc/SETUP.md`.

---

## 8. Judgment calls (make them; don't ask)
- Metric library: use scikit-learn's metrics where possible (already in the sandbox).
- `property` DSL: implement a small fixed set (`corr>`, `corr<`, `count==`, `dtype==`, `shape==`,
  `nunique==`, `in_range`) — never execute student-provided expressions.
- If the coach worker's exact request/response shape is unknown, inspect pda-public's coach client
  (`pda-public/src/lib/ai/coach-worker.ts`) and mirror it.
- If a Supabase interface detail is unknown, inspect the live DB via MCP (`list_tables`,
  `execute_sql`) and the existing `mobile_le_*` functions — match their conventions.

## 9. Interface contract for the UI repos (build later, separately)
Give these to whoever builds the student/admin UI so the engine stays untouched:
- **Student (pda-public, lesson Task tab):** GET published `student_notebook_path` + `data_paths`
  (signed URLs); POST a submission = upload `.ipynb`(+files) to `assignment-submissions` and insert
  `assignment_submissions(status='queued', attempt_no)`; poll `assignment_results` for the graded
  view (score + `per_question` feedback + optional leaderboard). Enforce `max_submissions_per_day`.
- **Admin (pda-admin, "Create Assignment"):** upload solved `.ipynb` + data → detect cells → tag
  each answer cell (tag/points/config) → on Save: strip solutions → `student_notebook_path`, run the
  solution in the sandbox to capture `assignment_questions.expected`, publish. Plus a **review
  queue** screen over `review_queue`.
