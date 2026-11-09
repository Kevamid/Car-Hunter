-- Car Hunter: "Assess this car" from the dashboard. The page queues a request; the 15-minute pipeline
-- runs the MOT assessment (DVSA keys live only in GitHub secrets) and writes the report back.
-- Run ONCE in Supabase: SQL Editor -> New query -> paste -> Run. Safe to re-run.

create table if not exists public.assessments (
  id bigint generated always as identity primary key,
  url text,                                   -- the dashboard car (cars.url), if any
  registration text,
  title text,
  price integer,
  mileage integer,
  year integer,
  status text not null default 'queued',     -- queued | done | error
  report text,
  requested_at timestamptz not null default now(),
  done_at timestamptz
);
create index if not exists assessments_url on public.assessments (url, requested_at desc);

alter table public.assessments enable row level security;

-- the dashboard may read, queue and delete; only the pipeline (secret key) writes reports
drop policy if exists "allowed read assessments" on public.assessments;
create policy "allowed read assessments" on public.assessments for select to authenticated using (public.is_allowed());

drop policy if exists "allowed queue assessments" on public.assessments;
create policy "allowed queue assessments" on public.assessments for insert to authenticated
  with check (public.is_allowed() and status = 'queued' and report is null);

drop policy if exists "allowed delete assessments" on public.assessments;
create policy "allowed delete assessments" on public.assessments for delete to authenticated using (public.is_allowed());
