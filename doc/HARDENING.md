# pda-grader — hardening (G7)

How the engine stays safe, cheap, and self-healing.

## Crash recovery / requeue (reaper)
`grader_requeue_stuck('1 hour')` runs at the start of every batch (`main.run_once`). Any
submission left in `grading` for >1h (a killed runner) is reset to `queued`; any assignment
stuck in `authoring` is reset to `pending`. So killing a run mid-batch never strands work —
it grades on the next fire.

## Idempotency
- `assignment_results` has a UNIQUE index on `submission_id`; `grader_write_result` uses
  `on conflict … do update`, so re-grading a submission overwrites, never duplicates.
- `grader_claim_batch` uses `for update skip locked` — concurrent runs never grab the same
  row. (Batches don't overlap anyway: the workflow `concurrency` group serializes them.)

## Failure containment
- One bad submission never blocks the batch: each is graded in its own try/except; on any
  error the submission is written `status='error'` with a short message and the loop moves on.
- One bad *question* never fails the submission: `grade_question` catches per-grader
  exceptions and returns an `error` verdict for just that question.
- One erroring **cell** never voids the whole submission: the notebook runs with
  nbclient `allow_errors=True`, so execution continues past a failing cell, the
  answer-dump still runs, and each cell is graded on its own (the broken cell scored
  low with `evidence: errored`, the rest graded normally) — partial credit, not a zero.
- A notebook that can't execute at all (kernel dies, total wall-clock timeout, no
  answer dump) → `status='error'` with a helpful reason (incl. the stderr tail), not a crash.
- LLM-judge failures degrade to the review queue (`verdict='review'`), batch continues.

## Sandbox (STRICT §1.4)
`--network none`, non-root (uid 1000), `--read-only` rootfs, `--cap-drop ALL`,
`--security-opt no-new-privileges`, `--memory`/`--cpus`/`--pids-limit` caps, tmpfs `/tmp`,
a hard `timeout` wall-clock, ephemeral `--rm`. The only writable path is the throwaway
`/work` bind mount. Student code can't reach the network, the host, or another submission.

## Secrets discipline
Credentials come only from the environment (fail-loud if missing). Logs never print an env
var that holds a credential. PostgREST/Storage errors are truncated and never echo the key.

## Resubmission cap
Enforced at submit time in `mobile_le_assignment_submit` (`max_submissions_per_day`, default
5). The grader always grades the latest queued attempt.

## Optional / future (documented, not blocking)
- **Result cache:** skip re-running an identical notebook by content hash. Not enabled — the
  hourly cap already bounds cost, and resubmissions are usually genuine changes.
- **Audit re-run sample:** re-grade a random N% to detect nondeterminism / tampering. Hook
  point: sample in `run_once` before `claim_batch`. Not enabled by default.
- **GPU overflow:** burst heavy re-verification to Modal later; no re-architecture needed
  (grading is CPU — we score predictions, we don't train).
