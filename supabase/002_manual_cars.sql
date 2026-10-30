-- Car Hunter: let the dashboard add, edit and delete MANUAL cars (adverts Kev finds himself).
-- Run ONCE in Supabase: SQL Editor -> New query -> paste -> Run. Safe to re-run.
--
-- Only signed-in allowed users, and only rows marked details.added_by = 'manual'. Cars the pipeline found
-- stay read-only from the dashboard (the pipeline writes with the secret key, which bypasses these rules).

drop policy if exists "allowed insert manual cars" on public.cars;
create policy "allowed insert manual cars" on public.cars for insert to authenticated
  with check (public.is_allowed() and details ->> 'added_by' = 'manual');

drop policy if exists "allowed update manual cars" on public.cars;
create policy "allowed update manual cars" on public.cars for update to authenticated
  using (public.is_allowed() and details ->> 'added_by' = 'manual')
  with check (public.is_allowed() and details ->> 'added_by' = 'manual');

drop policy if exists "allowed delete manual cars" on public.cars;
create policy "allowed delete manual cars" on public.cars for delete to authenticated
  using (public.is_allowed() and details ->> 'added_by' = 'manual');
