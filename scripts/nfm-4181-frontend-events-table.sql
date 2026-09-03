-- NFM-4181: production events table for the internal analytics pipeline.
--
-- Production destination for the NFM-4146 disclosure events (client:
-- apps/web/src/lib/analytics.ts).
-- The web app POSTs frontend analytics events to /api/analytics/events, which
-- inserts them here. The first consumers are the data-loss-notice disclosure
-- events (spec NFM-4134.A §6.4):
--   data_loss_notice.viewed | data_loss_notice.dismissed
--   | data_loss_notice.learn_more_clicked
--
-- Apply order: this script is idempotent; safe to re-run in staging or prod.

create table if not exists public.frontend_events (
    id            bigint generated always as identity primary key,
    -- Cross-product analytics contract: dotted event name, e.g.
    -- 'data_loss_notice.viewed'. Enforced client-side by an allowlist in
    -- the route; kept as text (not enum) so new events don't need DDL.
    event         text        not null,
    -- Verbatim JSON payload from the client (shape per event contract).
    payload       jsonb       not null default '{}'::jsonb,
    -- Millisecond epoch captured client-side at track() time.
    client_ts     bigint      not null,
    ingested_at   timestamptz not null default now()
);

create index if not exists frontend_events_event_ingested_at_idx
    on public.frontend_events (event, ingested_at desc);

alter table public.frontend_events enable row level security;

-- Inserts come from the Next.js API route. It prefers the service-role key
-- (bypasses RLS); the anon policy below is the fallback for deployments
-- where only the publishable key is available.
drop policy if exists "anon can insert frontend events" on public.frontend_events;
create policy "anon can insert frontend events"
    on public.frontend_events
    for insert
    to anon
    with check (true);

-- No select/update/delete grants: dashboards read via the warehouse replica
-- or the service role, never from the browser client.
