# pda-grader — operator setup (one-time)

Everything the grader needs at run time is injected from a secret store. **No secret
is ever committed** (STRICT-INSTRUCTIONS.md). This is the human-run checklist to go live.

---

## 1. Apply the DB surface (grader role + RPCs + storage RLS)

The grading tables, buckets, and app/admin RPCs already exist in the shared Supabase
backend (created by the pda-admin authoring build, in the isolated `mobile` schema).
This repo adds only the **grader's** surface: a least-privilege `grader` role and the
`grader_*` SECURITY DEFINER RPCs.

Apply [`db/schema.sql`](../db/schema.sql) to the shared project — via the Supabase MCP
`apply_migration`, the SQL editor, or `supabase db push`. It is idempotent
(`create or replace`, `if not exists`, `drop policy if exists`).

> This is an RBAC change on the shared backend — it needs a human go-ahead. Review the
> file first; it creates the `grader` role, grants it EXECUTE on the seven `grader_*`
> functions **only**, and revokes those from public/anon/authenticated.

## 2. Mint `GRADER_KEY` (the restricted JWT — NOT service_role)

The grader authenticates to PostgREST + Storage with a JWT whose `role` claim is
`grader`. It is **not** the service_role key (STRICT §1.3). Mint it **locally** from the
project's JWT secret (Supabase → Project Settings → API → JWT Secret). **Never commit the
JWT secret or the minted token** — paste the token straight into GitHub Secrets.

```python
# mint_grader_key.py  — run locally, DO NOT COMMIT. Needs: pip install pyjwt
import jwt, time, os
secret = os.environ["SUPABASE_JWT_SECRET"]          # from Supabase dashboard; never hardcode
token = jwt.encode(
    {"role": "grader", "iss": "supabase",
     "iat": int(time.time()),
     "exp": int(time.time()) + 10*365*24*3600},     # long-lived; rotate on suspicion
    secret, algorithm="HS256")
print(token)
```

Rotation = mint a new token and update the GitHub Secret; revoke by changing the JWT
secret (rotates everything) or by `revoke grader from authenticator` in an incident.

## 3. GitHub Actions Secrets (repo → Settings → Secrets and variables → Actions)

| Secret | Value |
|---|---|
| `SUPABASE_URL` | `https://<ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | the project **anon / publishable** key (public — Settings → API → Project API keys → `anon`). Sent as the `apikey` header to satisfy the Supabase gateway; the grader role comes from `GRADER_KEY`. |
| `GRADER_KEY` | the minted `grader`-role JWT from step 2 (sent as `Authorization: Bearer`) |
| `COACH_URL` | the pda-coach worker base URL (LLM-judge for `written`/`task`) |
| `COACH_KEY` | the pda-coach `x-coach-key` shared secret |
| `R2_ACCOUNT_ID` | Cloudflare account id (used to build the S3 endpoint if `R2_S3_ENDPOINT` is unset) |
| `R2_S3_ENDPOINT` | *(optional)* full S3 endpoint, e.g. `https://<account>.r2.cloudflarestorage.com` — overrides the account-id form |
| `R2_ACCESS_KEY_ID` | R2 API token **Access Key ID** |
| `R2_SECRET_ACCESS_KEY` | R2 API token **Secret Access Key** |
| `R2_BUCKET_CONTENT` | the real R2 bucket holding assignment content (solution/student notebooks, data, labels) — maps from the DB's logical `assignment-content` |
| `R2_BUCKET_SUBMISSIONS` | the real R2 bucket holding student uploads — maps from logical `assignment-submissions` |

> **Why two Supabase keys?** Supabase's API gateway rejects any `apikey` that isn't the
> anon/publishable or service_role key (`{"message":"Invalid API key"}`). A custom-role JWT
> is only valid as the `Authorization: Bearer` token. So the grader sends the public anon key
> as `apikey` (gateway pass) and the restricted `GRADER_KEY` as the Bearer (role = `grader`).

> **Storage is on Cloudflare R2 (S3 API), not Supabase Storage.** The grader reads/writes
> assignment objects via boto3 against R2; only the *database* stays on Supabase. The R2 API
> token should be **scoped to just the two assignment buckets, read+write** (least privilege —
> STRICT §1.3). The `grader_*` RPCs return **logical** bucket names (`assignment-content` /
> `assignment-submissions`); `config.r2_bucket_for()` maps those to your real R2 buckets.
>
> ⚠️ **Cross-app dependency:** because storage moved to R2, the **pda-admin** (author uploads)
> and **pda-public** (student uploads) surfaces must also read/write these assignment objects
> in the **same R2 buckets** — otherwise the grader looks in R2 while the files sit in Supabase
> Storage and downloads fail. Keep all three on R2 for these buckets.

The grader runs without `COACH_*` too — `written`/`task` questions then degrade to the
review queue instead of crashing (graceful).

## 4. Public-repo guards (STRICT §5)

- Secret scanning + push protection **ON**.
- Branch protection: `security-check` a **required** check; fork-PR approval required.
- Confirm the trigger token (step in [TRIGGER.md](./TRIGGER.md)) is fine-grained,
  this-repo-only, `actions: write`.
- `bash scripts/security-check.sh` green on the full history before going public.

## 5. Go

- Manual: **Actions → grade → Run workflow** drains the queue once.
- Automatic: wire the hourly trigger in [TRIGGER.md](./TRIGGER.md).

---

### How the grader touches the DB/storage (for reviewers)
- **DB:** only the seven `grader_*` RPCs (`grader_claim_batch`, `grader_submission_bundle`,
  `grader_write_result`, `grader_flag_review`, `grader_requeue_stuck`,
  `grader_claim_author_jobs`, `grader_write_authored`). No table access; RLS still denies
  the `grader` role direct reads/writes on `mobile.*`.
- **Storage:** downloads assignment content + submissions and uploads the generated student
  notebook from **Cloudflare R2** (S3 API via boto3, path-style, s3v4). The R2 token is
  scoped to the two assignment buckets, read+write. Supabase holds no assignment objects.
