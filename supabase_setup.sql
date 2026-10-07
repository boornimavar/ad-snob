-- Creative Lens: one-time setup for the team's Supabase project.
-- Paste all of this into Supabase -> SQL Editor -> New query, then click Run.
-- Safe to run again. Keep the table in step with `ads` in library.py.

create table if not exists public.ads (
  id                     bigint generated always as identity primary key,
  category_key           text not null,              -- lower-case, for matching
  category               text not null,              -- as first typed, for display
  country                text not null,
  image_hash             text not null,              -- not unique: same design, other price
  frame_variant_hash     text not null default '[]',
  ocr_fingerprint        text not null default '',
  brand                  text not null default '',
  image_url              text not null default '',   -- Meta's link; expires after days
  thumbnail_key          text,                       -- our copy in the "thumbnails" bucket
  rank_score             double precision,           -- null for excluded video frames
  filter_status          text not null default '',
  creative_variant_count integer not null default 0,
  scores                 jsonb not null default '{}',
  details                jsonb not null default '{}',
  human_label            text not null default '',   -- for 👍/👎 later
  first_seen_at          timestamptz not null,
  last_seen_at           timestamptz not null,
  times_seen             integer not null default 1
);
create index if not exists ads_category_idx on public.ads (category_key, country);
create index if not exists ads_last_seen_idx on public.ads (last_seen_at);
create index if not exists ads_rank_idx on public.ads (rank_score desc nulls last);

-- Who may do what: anyone (the gallery) reads; only a signed-in user (the
-- collector app) adds and updates; nobody deletes.
alter table public.ads enable row level security;
drop policy if exists "Anyone can read ads" on public.ads;
create policy "Anyone can read ads" on public.ads for select to anon, authenticated using (true);
drop policy if exists "The collector can add ads" on public.ads;
create policy "The collector can add ads" on public.ads for insert to authenticated with check (true);
drop policy if exists "The collector can update ads" on public.ads;
create policy "The collector can update ads" on public.ads for update to authenticated using (true) with check (true);
revoke delete on public.ads from anon, authenticated;

-- Per-category counts for the gallery's list (14 days = active, 0.5+ = design-led).
create or replace view public.ad_categories with (security_invoker = on) as
select
  category_key,
  min(category) as category,
  country,
  count(*) filter (where last_seen_at >= now() - interval '14 days') as active,
  count(*) filter (where last_seen_at <  now() - interval '14 days') as archived,
  count(*) filter (where last_seen_at >= now() - interval '14 days' and rank_score >= 0.5) as active_design_led,
  count(*) filter (where last_seen_at <  now() - interval '14 days' and rank_score >= 0.5) as archived_design_led
from public.ads
group by category_key, country
order by category_key, country;
grant select on public.ad_categories to anon, authenticated;

-- Pictures: the "thumbnails" bucket is public to read; only the collector may upload.
drop policy if exists "The collector can upload pictures" on storage.objects;
create policy "The collector can upload pictures" on storage.objects
  for insert to authenticated with check (bucket_id = 'thumbnails');
