# pda-grader — the hourly trigger (G6)

The grader workflow is fired by **Supabase `pg_cron` → `pg_net` → GitHub
`repository_dispatch`**. The queue and the trigger both live in Supabase; GitHub only
runs the compute. (`workflow_dispatch` is also enabled for manual runs.)

## 1. Store a fine-grained GitHub token in Supabase Vault

Create a **fine-grained** GitHub PAT scoped to **this repo only**, permission
**Actions: write** and nothing else (STRICT §1.7). Store it in Supabase Vault — never in
this repo, never in the workflow.

```sql
-- run once in the Supabase SQL editor (service role)
select vault.create_secret('<THE_FINE_GRAINED_PAT>', 'GH_DISPATCH_TOKEN',
                           'Fine-grained PAT (pda-grader, actions:write) for the hourly grade trigger');
```

## 2. Schedule the hourly dispatch

```sql
-- requires the pg_cron + pg_net extensions (enable in Database → Extensions)
select cron.schedule('grader-hourly', '0 * * * *', $$
  select net.http_post(
    url     := 'https://api.github.com/repos/<OWNER>/pda-grader/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets
                                     where name = 'GH_DISPATCH_TOKEN'),
      'Accept', 'application/vnd.github+json',
      'User-Agent', 'pda-grader',
      'Content-Type', 'application/json'),
    body    := jsonb_build_object('event_type', 'grade-batch')
  );
$$);
```

Replace `<OWNER>` with the GitHub org/user that owns the repo.

## 3. Verify + operate

```sql
-- confirm it's scheduled
select jobid, schedule, jobname, active from cron.job where jobname = 'grader-hourly';

-- inspect the last few fires (did the POST return 204?)
select * from cron.job_run_details
  where jobid = (select jobid from cron.job where jobname = 'grader-hourly')
  order by start_time desc limit 5;

-- pause / resume / remove
select cron.alter_job((select jobid from cron.job where jobname='grader-hourly'), active := false);
select cron.unschedule('grader-hourly');
```

A successful `repository_dispatch` returns **204** and the **grade** workflow appears in
the Actions tab. Disabling the cron stops all future fires (the DoD).

## Why this design
- **Cost ceiling:** one fire per hour × `GRADER_BATCH` (default 15) submissions = a hard,
  predictable cap. A backlog just waits; it never spikes.
- **Reliability:** `pg_cron` fires more reliably than GitHub's own `schedule:` (which lags
  and auto-disables after 60 days of repo inactivity).
- **Least privilege, both directions:** Supabase→GitHub uses a repo-scoped `actions:write`
  PAT in Vault; GitHub→Supabase uses the restricted `grader` JWT in Actions Secrets.
  Neither is ever in the repo.
