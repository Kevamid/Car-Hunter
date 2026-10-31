-- Car Hunter: the cars you're hunting ("searches"), edited from the dashboard's Searches panel.
-- Run ONCE in Supabase: SQL Editor -> New query -> paste -> Run. Safe to re-run.
--
-- The pipeline reads these every run (with the secret key). If Supabase can't be reached it uses the
-- last copy it read, then config.json. Your current five searches are copied in below.

create table if not exists public.searches (
  id bigint generated always as identity primary key,
  make text not null,
  model text not null,                         -- the label used for matching titles, e.g. "Clio Sport"
  enabled boolean not null default true,       -- off = no alerts for this car from any source
  min_price integer,
  max_price integer,
  aliases jsonb not null default '[]'::jsonb,  -- must-have words: at least one in the title (e.g. "182")
  ebay boolean not null default true,          -- also search eBay for it
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index if not exists searches_make_model on public.searches (lower(make), lower(model));

-- one row per setting, e.g. pass_through = true ("alert me on email cars that match no search")
create table if not exists public.settings (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now()
);

alter table public.searches enable row level security;
alter table public.settings enable row level security;

drop policy if exists "allowed manage searches" on public.searches;
create policy "allowed manage searches" on public.searches for all to authenticated
  using (public.is_allowed()) with check (public.is_allowed());

drop policy if exists "allowed manage settings" on public.settings;
create policy "allowed manage settings" on public.settings for all to authenticated
  using (public.is_allowed()) with check (public.is_allowed());

-- your searches as they were in config.json (skipped if already there)
insert into public.searches (make, model, max_price, aliases) values
  ('BMW', 'M140i', 22000, '[]'),
  ('Toyota', 'GT86', 15000, '[]'),
  ('Mazda', 'MX-5', 10000, '[]'),
  ('Renault', 'Clio Sport', 5500, '["172","182","197","200","rs","renaultsport","cup","trophy"]'),
  ('Renault', 'Megane RS', 10000, '["rs","renaultsport","r26","225","230","250","265","275","trophy"]')
on conflict do nothing;

insert into public.settings (key, value) values ('pass_through', 'false') on conflict do nothing;
