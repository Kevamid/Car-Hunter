-- Car Hunter: show price drops on the dashboard ("↓ £500 · was £6,500").
-- Run ONCE in Supabase: SQL Editor -> New query -> paste -> Run. Safe to re-run.
-- The pipeline fills these from its price history every run.

alter table public.cars add column if not exists previous_price integer;      -- the price before the latest change
alter table public.cars add column if not exists price_changed_at timestamptz;
