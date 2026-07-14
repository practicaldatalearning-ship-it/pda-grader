-- ============================================================================
-- pda-grader — DB surface (G0 + G0b).  REFERENCE COPY.
-- The live change is applied to the shared Supabase backend via the Supabase
-- MCP `apply_migration` (needs a human go-ahead — it is an RBAC change).
--
-- Context (verified against the live DB, 2026-07-13):
--   * The assignment tables already exist in the ISOLATED `mobile` schema
--     (created by the pda-admin authoring build): mobile.assignments,
--     mobile.assignment_questions, mobile.assignment_submissions,
--     mobile.assignment_results, mobile.review_queue — with the tag CHECK,
--     status CHECK, author_status/author_error columns, and the app/admin RPCs
--     (mobile_le_assignment_*, mobile_svc_assignment_*).
--   * Private buckets exist: assignment-content (solution+student nb+data+labels),
--     assignment-submissions (student uploads).  assignment-dist is public.
--
-- What THIS file adds: the grader's ONLY DB surface — a least-privilege `grader`
-- role and a set of SECURITY DEFINER `grader_*` RPCs in `public` that operate on
-- the `mobile.*` tables.  The grader NEVER gets the service_role key
-- (STRICT-INSTRUCTIONS §1.3); it authenticates with GRADER_KEY, a JWT whose
-- `role` claim is `grader` (minted by a human from the project JWT secret and
-- stored in GitHub Actions Secrets).
--
-- Standing rules honoured: mobile schema stays isolated; nothing ALTERs public;
-- the grader has claim-work + write-result + author-support only, nothing else.
--
-- APPLIED to the live shared DB on 2026-07-14 (migration `grader_role_and_rpcs`)
-- after a per-column/constraint compatibility check.  The only live mismatch
-- found was that mobile.assignments' author_status CHECK omitted 'authoring'
-- (which grader_claim_author_jobs / grader_requeue_stuck write) — widened below
-- as a prerequisite (expand-only; 0 existing rows violated).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 0) PREREQUISITE (mobile schema, expand-only): allow author_status='authoring'.
--    The pda-admin authoring build only used pending/ready/error; the grader
--    adds the transient 'authoring' claim state.  Not an ALTER of `public`.
-- ----------------------------------------------------------------------------
alter table mobile.assignments drop constraint if exists assignments_author_status_check;
alter table mobile.assignments add constraint assignments_author_status_check
  check (author_status in ('pending','authoring','ready','error'));

-- ----------------------------------------------------------------------------
-- 1) The restricted role + storage access + idempotency indexes.
-- ----------------------------------------------------------------------------
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'grader') then
    create role grader nologin noinherit;
  end if;
end $$;
grant grader to authenticator;

grant usage on schema public to grader;   -- the grader_* RPCs live here
grant usage on schema mobile to grader;   -- so setof/return types resolve
-- No table privileges on mobile.* — the SECURITY DEFINER RPCs are the only surface.

-- Idempotency (one result per submission) + queue scan support.
create unique index if not exists assignment_results_submission_uidx
  on mobile.assignment_results(submission_id);
create index if not exists assignment_submissions_status_submitted_idx
  on mobile.assignment_submissions(status, submitted_at);
create index if not exists assignments_author_status_idx
  on mobile.assignments(author_status);

-- Storage: READ assignment content + submissions; WRITE the generated student
-- notebook back into assignment-content (author job).  RLS rows gated to grader.
grant usage on schema storage to grader;
grant select, insert, update on storage.objects to grader;

drop policy if exists grader_read_objects on storage.objects;
create policy grader_read_objects on storage.objects for select to grader
  using (bucket_id in ('assignment-content', 'assignment-submissions'));

drop policy if exists grader_write_content on storage.objects;
create policy grader_write_content on storage.objects for insert to grader
  with check (bucket_id = 'assignment-content');

drop policy if exists grader_update_content on storage.objects;
create policy grader_update_content on storage.objects for update to grader
  using (bucket_id = 'assignment-content')
  with check (bucket_id = 'assignment-content');

-- ----------------------------------------------------------------------------
-- 2) Grading RPCs.
-- ----------------------------------------------------------------------------

-- Atomically claim a batch of queued submissions (concurrency-safe).  Marks them
-- 'grading' and returns a compact JSON array.  Returns jsonb (not setof) so the
-- grader role needs no direct visibility into the mobile.* composite types.
create or replace function public.grader_claim_batch(p_limit int default 15)
returns jsonb
language plpgsql security definer set search_path to 'mobile', 'public' as $$
declare v_rows jsonb;
begin
  with claimed as (
    update mobile.assignment_submissions s set status = 'grading'
    where s.id in (
      select id from mobile.assignment_submissions
      where status = 'queued'
      order by submitted_at
      limit greatest(1, coalesce(p_limit, 15))
      for update skip locked)
    returning s.id, s.assignment_id, s.user_id, s.notebook_path, s.extra_paths, s.attempt_no)
  select coalesce(jsonb_agg(to_jsonb(claimed) order by claimed.id), '[]'::jsonb) into v_rows from claimed;
  return v_rows;
end $$;

-- Everything the grader needs to grade one submission: the submission, its
-- assignment (incl. solution notebook path + pass mark), the tagged questions
-- (tag/points/config/expected), and the storage {bucket,path} handles for the
-- student notebook, extra files, data, and any hidden prediction labels.
-- The grader downloads each handle from the authenticated storage endpoint with
-- its GRADER_KEY (role=grader) — no service_role key, no signing secret needed.
create or replace function public.grader_submission_bundle(p_submission uuid)
returns jsonb
language plpgsql security definer set search_path to 'mobile', 'public' as $$
declare v jsonb; v_a mobile.assignments%rowtype; v_s mobile.assignment_submissions%rowtype;
begin
  select * into v_s from mobile.assignment_submissions where id = p_submission;
  if v_s.id is null then return jsonb_build_object('error', 'submission not found'); end if;
  select * into v_a from mobile.assignments where id = v_s.assignment_id;

  select jsonb_build_object(
    'submission', jsonb_build_object(
      'id', v_s.id, 'assignment_id', v_s.assignment_id, 'user_id', v_s.user_id,
      'notebook_path', v_s.notebook_path, 'extra_paths', to_jsonb(v_s.extra_paths),
      'attempt_no', v_s.attempt_no, 'status', v_s.status),
    'assignment', jsonb_build_object(
      'id', v_a.id, 'lesson_id', v_a.lesson_id, 'title', v_a.title,
      'total_points', v_a.total_points, 'pass_mark', v_a.pass_mark,
      'data_paths', to_jsonb(v_a.data_paths),
      'solution_notebook_path', v_a.solution_notebook_path,
      'student_notebook_path', v_a.student_notebook_path),
    'questions', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', q.id, 'cell_ref', q.cell_ref, 'var_name', q.var_name,
               'tag', q.tag, 'points', q.points, 'config', q.config, 'expected', q.expected)
             order by q.sort, q.created_at)
      from mobile.assignment_questions q where q.assignment_id = v_a.id), '[]'::jsonb),
    'notebook', jsonb_build_object('bucket', 'assignment-submissions', 'path', v_s.notebook_path),
    'extra', coalesce((
      select jsonb_agg(jsonb_build_object('bucket', 'assignment-submissions', 'path', p))
      from unnest(v_s.extra_paths) p), '[]'::jsonb),
    'data', coalesce((
      select jsonb_agg(jsonb_build_object('bucket', 'assignment-content', 'path', p))
      from unnest(v_a.data_paths) p), '[]'::jsonb),
    'labels', coalesce((
      select jsonb_object_agg(q.id::text,
               jsonb_build_object('bucket', 'assignment-content', 'path', q.config->>'label_path'))
      from mobile.assignment_questions q
      where q.assignment_id = v_a.id and coalesce(q.config->>'label_path', '') <> ''), '{}'::jsonb)
  ) into v;
  return v;
end $$;

-- Write the result + set final status; roll a pass into lesson progress.
-- Idempotent via the unique index on (submission_id).
create or replace function public.grader_write_result(
  p_submission uuid, p_total numeric, p_per_question jsonb,
  p_status text, p_error text default null)
returns jsonb
language plpgsql security definer set search_path to 'mobile', 'public' as $$
declare v_s mobile.assignment_submissions%rowtype; v_a mobile.assignments%rowtype; v_pct numeric;
begin
  select * into v_s from mobile.assignment_submissions where id = p_submission;
  if v_s.id is null then return jsonb_build_object('error', 'submission not found'); end if;
  select * into v_a from mobile.assignments where id = v_s.assignment_id;

  -- Persist the result for a completed grade OR a provisional needs_review
  -- (so the student still sees a score + per-question feedback while a human
  -- review is pending). Only 'error' skips the results row.
  if p_status in ('graded', 'needs_review') then
    insert into mobile.assignment_results(submission_id, total_score, per_question)
      values (p_submission, coalesce(p_total, 0), coalesce(p_per_question, '[]'::jsonb))
      on conflict (submission_id) do update
        set total_score = excluded.total_score,
            per_question = excluded.per_question,
            graded_at = now();
  end if;

  update mobile.assignment_submissions
    set status = p_status, error = p_error, graded_at = now()
    where id = p_submission;

  -- Roll a pass into lesson progress (mirrors mobile_le_lesson_complete's upsert).
  if p_status = 'graded' and coalesce(v_a.total_points, 0) > 0 then
    v_pct := (coalesce(p_total, 0) / v_a.total_points) * 100.0;
    if v_pct >= coalesce(v_a.pass_mark, 60) then
      insert into mobile.le_lesson_progress as pr
        (user_id, lesson_id, status, marks_obtained, marks_total, completed_at, updated_at)
      values (v_s.user_id, v_a.lesson_id, 'completed', coalesce(p_total, 0), v_a.total_points, now(), now())
      on conflict (user_id, lesson_id) do update set
        status = 'completed',
        marks_obtained = greatest(pr.marks_obtained, excluded.marks_obtained),
        marks_total = greatest(pr.marks_total, excluded.marks_total),
        completed_at = coalesce(pr.completed_at, now()),
        updated_at = now();
    end if;
  end if;

  return jsonb_build_object('ok', true, 'status', p_status);
end $$;

-- Enqueue a low-confidence written/task answer for human review.
create or replace function public.grader_flag_review(
  p_submission uuid, p_question uuid, p_reason text, p_suggested numeric)
returns jsonb
language plpgsql security definer set search_path to 'mobile', 'public' as $$
begin
  insert into mobile.review_queue(submission_id, question_id, reason, suggested_score, status)
    values (p_submission, p_question, p_reason, p_suggested, 'open');
  return jsonb_build_object('ok', true);
end $$;

-- Reaper (G7): requeue submissions stuck in 'grading' (e.g. a runner was killed).
create or replace function public.grader_requeue_stuck(p_older_than interval default '1 hour')
returns integer
language plpgsql security definer set search_path to 'mobile', 'public' as $$
declare v_n integer;
begin
  update mobile.assignment_submissions
    set status = 'queued'
    where status = 'grading' and graded_at is null and submitted_at < now() - p_older_than;
  get diagnostics v_n = row_count;
  -- also reset assignments stuck mid-authoring
  update mobile.assignments
    set author_status = 'pending'
    where author_status = 'authoring' and created_at < now() - p_older_than;
  return v_n;
end $$;

-- ----------------------------------------------------------------------------
-- 3) Author-job RPCs (G0b) — the grader also authors pending assignments:
--    it runs the solution notebook to capture `expected` per question and
--    strips solutions to produce the student notebook.
-- ----------------------------------------------------------------------------

-- Claim assignments awaiting authoring (author_status='pending').  Marks them
-- 'authoring' and returns their bundles (solution notebook + data + questions).
create or replace function public.grader_claim_author_jobs(p_limit int default 15)
returns jsonb
language plpgsql security definer set search_path to 'mobile', 'public' as $$
declare v_rows jsonb;
begin
  with claimed as (
    update mobile.assignments a set author_status = 'authoring'
    where a.id in (
      select id from mobile.assignments
      where author_status = 'pending'
        and solution_notebook_path is not null and solution_notebook_path <> ''
      order by created_at
      limit greatest(1, coalesce(p_limit, 15))
      for update skip locked)
    returning a.id, a.lesson_id, a.title, a.total_points, a.pass_mark,
              a.data_paths, a.solution_notebook_path)
  select coalesce(jsonb_agg(
    jsonb_build_object(
      'assignment', to_jsonb(claimed),
      'solution', jsonb_build_object('bucket', 'assignment-content', 'path', claimed.solution_notebook_path),
      'data', coalesce((
        select jsonb_agg(jsonb_build_object('bucket', 'assignment-content', 'path', p))
        from unnest(claimed.data_paths) p), '[]'::jsonb),
      'questions', coalesce((
        select jsonb_agg(jsonb_build_object(
                 'id', q.id, 'cell_ref', q.cell_ref, 'var_name', q.var_name,
                 'tag', q.tag, 'points', q.points, 'config', q.config)
               order by q.sort, q.created_at)
        from mobile.assignment_questions q where q.assignment_id = claimed.id), '[]'::jsonb)
    ) order by claimed.id), '[]'::jsonb) into v_rows from claimed;
  return v_rows;
end $$;

-- Persist the authored artifacts: the generated student notebook path + the
-- per-question `expected` (a JSON object keyed by question_id).  Publishes on
-- success, or records the error.
create or replace function public.grader_write_authored(
  p_assignment uuid, p_student_nb_path text, p_expected jsonb,
  p_status text default 'ready', p_error text default null)
returns jsonb
language plpgsql security definer set search_path to 'mobile', 'public' as $$
declare k text;
begin
  if p_status = 'ready' then
    -- write each question's captured expected value
    if p_expected is not null then
      for k in select jsonb_object_keys(p_expected) loop
        update mobile.assignment_questions
          set expected = p_expected -> k
          where id = k::uuid and assignment_id = p_assignment;
      end loop;
    end if;
    update mobile.assignments
      set student_notebook_path = coalesce(p_student_nb_path, student_notebook_path),
          author_status = 'ready', is_published = true, author_error = null
      where id = p_assignment;
  else
    update mobile.assignments
      set author_status = 'error', author_error = p_error
      where id = p_assignment;
  end if;
  return jsonb_build_object('ok', true, 'author_status', p_status);
end $$;

-- ----------------------------------------------------------------------------
-- 4) Lock the surface: the grader role, and ONLY the grader role, may execute
--    these.  Never public / anon / authenticated.
-- ----------------------------------------------------------------------------
do $$
declare fn text;
begin
  foreach fn in array array[
    'public.grader_claim_batch(int)',
    'public.grader_submission_bundle(uuid)',
    'public.grader_write_result(uuid,numeric,jsonb,text,text)',
    'public.grader_flag_review(uuid,uuid,text,numeric)',
    'public.grader_requeue_stuck(interval)',
    'public.grader_claim_author_jobs(int)',
    'public.grader_write_authored(uuid,text,jsonb,text,text)'
  ] loop
    execute format('revoke all on function %s from public, anon, authenticated', fn);
    execute format('grant execute on function %s to grader', fn);
  end loop;
end $$;
