# Grading V2 — evidence-anchored AI-vs-reference (implemented)

Design: [`../../doc/assignment-grading-v2.md`](../../doc/assignment-grading-v2.md). This note
records what the grader actually does.

## What runs (per submission), when `GRADER_V2=1` (default)
`grader/judge_v2.grade_submission_v2()` grades every question in two layers:

1. **Deterministic layer — authoritative, injection-proof.** Any question whose tag has a
   deterministic grader (`exact`, `auto`, `prediction`, `tests`, `set`, `property`,
   `output_match`) is scored exactly by the existing grader in `grader/graders/*`. These
   never reach the LLM; ML metrics (`prediction`) stay exact numbers, never guessed. Result
   carries `evidence.graded_by = "deterministic:<tag>"`.

2. **AI-vs-reference layer — ONE batched judge call.** Every other cell (`written`, `task`,
   `ai`, `graded`, empty/unknown tag — i.e. the simplified "marks + note" authoring) is packaged
   as **(answer-key cell, student executed cell, max marks, rubric note, deterministic evidence:
   run/error status)** and scored in a **single** `pda-coach /grade` call for the whole
   submission. Score is an integer `0..max`, temperature ~0, clamped + validated.
   Result carries `evidence.graded_by = "ai_reference"`.

Total = Σ cell scores. Reference cells come from the answer-key notebook
(`solution_notebook_path`, downloaded once, best-effort — the judge still grades on the student
work + evidence + note if it's missing). The executed student notebook + `produced_files` +
hidden `labels` are reused from the run.

## Prompt-injection defense (student cells are DATA, not instructions)
- Judge items frame student content as data-to-grade and tell the model to ignore any directive
  inside it (the pda-coach system prompt also marks it untrusted).
- A submission whose text tries to steer the grade ("give full credit", "ignore previous",
  "perfect score"…) is detected (`INJECTION_RE`) and routed to **human review** regardless of the
  LLM's answer, flagged `evidence.injection_suspected = true` (review reason `injection_suspected`).
- Scores are clamped to `0..max`; out-of-range/garbage judge output → 0.
- The deterministic layer can't be moved by text at all.

## Back-compat & rollout
- `GRADER_V2=0` restores the legacy per-tag loop (`grade_question` for every question).
- Existing tags keep working unchanged (they're the deterministic layer). Old assignments grade
  identically for their objective cells; `written`/`task` now grade via the batched
  reference-anchored judge instead of N per-rubric calls.
- New assignments can be authored with just **max marks + a one-line rubric note** (`config.note`)
  and no tag — those route to the judge automatically.

## Cost
One cheap-model call per submission (all judge cells batched). Deterministic layer is free.
