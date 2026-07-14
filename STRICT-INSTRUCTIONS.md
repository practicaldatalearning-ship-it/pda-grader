# STRICT INSTRUCTIONS — read before every commit & push

> **This repository is PUBLIC.** Anyone can read, clone, and download the ZIP. These rules are
> **non-negotiable**. They exist so that a public repo can never leak a secret, never expose data,
> and never let untrusted student code escape. **Breaking any rule below is a stop-the-line event.**

---

## 0. The golden rule
**Nothing secret, private, or real ever lives in this repository.** Only grading *code* and
*synthetic sample* fixtures. Every credential is injected at run time from a secret store. If you
are unsure whether something is safe to commit — **it is not.** Ask / leave it out.

## 1. Hard rules (NEVER)
1. **NEVER commit a secret.** No API keys, tokens, passwords, connection strings, JWTs,
   `.env` files, `*.pem` / `*.key` / `id_rsa`, `credentials.json`, or service-account files.
2. **NEVER hardcode** the Supabase URL+key, the GitHub token, or any credential in code, config,
   workflow YAML, comments, tests, or notebooks. Read them from the environment **only**:
   - GitHub Actions workflow → `${{ secrets.NAME }}`
   - Python grader → `os.environ["NAME"]` (fail loudly if missing; never a default literal).
3. **NEVER give the grader the full `service_role` key.** It gets **least privilege only** — a
   restricted Postgres role or a pair of SECURITY-DEFINER RPCs (`claim_batch`, `write_result`).
   It must be able to *claim work* and *write results* — nothing else.
4. **NEVER run student code unsandboxed.** Every submission runs in a container with **all** of:
   `--network none` · non-root user · read-only rootfs where possible · CPU/RAM/PID caps ·
   hard wall-clock timeout · ephemeral (destroyed after each run).
5. **NEVER let a fork/PR reach secrets.** Keep GitHub's default "require approval for fork PR
   workflows". **Do not use `pull_request_target`** with secrets. The grader is triggered by
   `repository_dispatch`/`workflow_dispatch` — never by student PRs.
6. **NEVER print a secret** to logs, artifacts, or error messages; never disable GitHub's secret
   masking; never `echo`/`print` an env var that holds a credential.
7. **NEVER use a classic broad-scope GitHub PAT.** The trigger token is **fine-grained**, scoped to
   **this repo only**, permission **`actions: write`** and nothing more.
8. **NEVER commit real student data, real answer keys, or real submission notebooks.** Those live in
   private Supabase Storage. The repo carries only tiny **synthetic** fixtures.
9. **NEVER turn off** GitHub **secret scanning** or **push protection** on this repo.

## 2. Where each secret lives (the only correct place)
| Secret | Lives in | Used for |
|---|---|---|
| GitHub token (fine-grained, this repo, `actions:write`) | **Supabase Vault** | Supabase `pg_cron`/`pg_net` triggers the workflow |
| Supabase access (restricted role / `claim_batch`+`write_result` RPC key) | **GitHub Actions Secrets** | the runner claims the queue + writes results |
| LLM / coach access (if used) | **GitHub Actions Secrets** | grading `written`/`task` cells |

Nothing above is ever in a file in this repo.

## 3. MANDATORY pre-push security check
**Before every `git push` you MUST run — and it MUST pass:**
```bash
./scripts/security-check.sh
```
- If it exits non-zero, **DO NOT PUSH.** Fix the finding first.
- The same check runs in CI (`.github/workflows/security-check.yml`) on every push and PR and
  **blocks merge** if it fails — so it cannot be skipped.
- Recommended once per clone: install the local guard so it runs automatically —
  `bash scripts/install-hooks.sh` (adds a `pre-push` git hook that runs the check).

The check scans for: tracked secret-shaped files (`.env`, keys, pem), real token/JWT/private-key
patterns, and hardcoded credential assignments. Green = safe to push.

## 4. If a secret was ever committed (incident procedure)
Deleting the file is **not enough** — git history keeps it, and on a public repo assume it is
already scraped.
1. **Rotate the secret immediately** (revoke the GitHub token / rotate the Supabase key). Rotation
   is the fix; history-scrubbing is secondary.
2. Remove it from the code, move it to the correct secret store (§2).
3. Purge history if feasible (`git filter-repo`), then force-push — but **only after rotation**.
4. Note the incident + the rotation in the PR.

## 5. Before making the repo public (one-time checklist)
- [ ] `./scripts/security-check.sh` passes on the full history, not just the diff.
- [ ] No secrets anywhere in git history (`git log -p | ./scripts/security-check.sh --stdin`, or a
      history scan tool).
- [ ] GitHub **secret scanning + push protection** enabled.
- [ ] Branch protection: CI security check **required** to merge; fork-PR approval required.
- [ ] Trigger token is fine-grained + minimal; Supabase access is a restricted role/RPC, not
      `service_role`.
- [ ] Only synthetic fixtures present; no real data/answers/notebooks.

---
_This file is the contract. If a change would require weakening any rule here, stop and redesign
instead._
