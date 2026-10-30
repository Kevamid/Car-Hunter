-- Car Hunter dashboard database (Supabase / Postgres). Run ONCE in Supabase: SQL Editor -> New query -> Run.
-- Safe to re-run: it only creates what is missing.
--
-- Who can see what:
--   * The pipeline (GitHub Actions) writes with the SECRET key, which bypasses these rules.
--   * The dashboard page uses the PUBLISHABLE key, and can only read/write when signed in as an email
--     listed in `allowed_users` (row level security below). Everyone else sees nothing.

create table if not exists public.allowed_users (
  email text primary key
);

-- One row per advert (same de-duplication key as the pipeline's SQLite: the advert URL without its query).
create table if not exists public.cars (
  url              text primary key,          -- de-duplication key (clean URL)
  link             text,                      -- what to open (e.g. Autotrader's own tracked link); null = url
  source           text not null,             -- eBay, PistonHeads, Car & Classic, Autotrader
  make             text,                      -- config labels, e.g. Mazda / MX-5
  model            text,
  title            text,
  price            integer,
  mileage          integer,
  year             integer,
  transmission     text,
  fuel             text,
  location         text,
  seller_type      text,
  images           jsonb not null default '[]'::jsonb,   -- photo URLs, first = main photo
  description      text,                      -- full text where the source allows it (eBay)
  details          jsonb not null default '{}'::jsonb,   -- extras: owners, MOT expiry, service history...
  market           text,                      -- price-against-market line
  duplicate_of     text,                      -- url of the same car on another site, if any
  first_seen_at    timestamptz not null default now(),
  last_seen_at     timestamptz not null default now()
);
create index if not exists cars_first_seen_idx on public.cars (first_seen_at desc);
create index if not exists cars_model_idx on public.cars (make, model);

create table if not exists public.price_history (
  id          bigint generated always as identity primary key,
  url         text not null references public.cars(url) on delete cascade,
  price       integer not null,
  recorded_at timestamptz not null default now()
);
create index if not exists price_history_url_idx on public.price_history (url, recorded_at);

create table if not exists public.favourites (
  url        text primary key references public.cars(url) on delete cascade,
  note       text,
  created_at timestamptz not null default now()
);

-- Row level security: only signed-in allowed users can read cars/history and manage favourites.
alter table public.allowed_users enable row level security;
alter table public.cars          enable row level security;
alter table public.price_history enable row level security;
alter table public.favourites    enable row level security;

create or replace function public.is_allowed() returns boolean
  language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.allowed_users where email = (auth.jwt() ->> 'email'));
$$;

drop policy if exists "allowed read cars" on public.cars;
create policy "allowed read cars" on public.cars for select to authenticated using (public.is_allowed());

drop policy if exists "allowed read history" on public.price_history;
create policy "allowed read history" on public.price_history for select to authenticated using (public.is_allowed());

drop policy if exists "allowed manage favourites" on public.favourites;
create policy "allowed manage favourites" on public.favourites for all to authenticated
  using (public.is_allowed()) with check (public.is_allowed());

-- No policies on allowed_users: the dashboard cannot read or change who is allowed.

-- LAST STEP: put the email you will sign in to the dashboard with between the quotes, then run.
-- (Kept out of the repo on purpose: edit it only in Supabase's SQL Editor.)
insert into public.allowed_users (email) values ('YOUR-EMAIL-HERE') on conflict do nothing;
