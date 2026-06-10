-- x402 pay-per-attest: revenue ledger / used-tx store + retry credits.
-- Apply to the Foundry Supabase project (same DB as mint_actors/mint_ratings/…).
-- The MCP server reads/writes these via PostgREST with the service-role key
-- (see supa.py). RLS stays OFF for these server-only tables (no anon access).

-- ── mint_payments ────────────────────────────────────────────────────────────
-- One row per verified attestation payment. The UNIQUE(tx_signature) is the
-- double-spend guard: a replayed signature 409s on insert. This is also the
-- revenue ledger (timestamp, amount, payer wallet, intent → attestation_id).
create table if not exists public.mint_payments (
    id              uuid primary key default gen_random_uuid(),
    tx_signature    text        not null unique,   -- Solana sig; UNIQUE = replay guard
    intent          text        not null,          -- the memo the payment was bound to
    mint_id         text        not null,          -- actor the attestation is for
    amount_usdc     numeric     not null,          -- USDC actually transferred
    payer_wallet    text,                          -- fee payer (base58)
    recipient       text        not null,          -- operations wallet that received it
    block_time      bigint,                         -- on-chain unix seconds
    status          text        not null default 'verified',
                    -- verified → settled (attest ok) | paid_attest_failed (credited)
    attestation_id  text,                          -- set once the attestation settles
    created_at      timestamptz not null default now()
);

create index if not exists mint_payments_mint_id_idx on public.mint_payments (mint_id);
create index if not exists mint_payments_status_idx  on public.mint_payments (status);
create index if not exists mint_payments_created_idx on public.mint_payments (created_at desc);

-- ── mint_attest_credits ──────────────────────────────────────────────────────
-- One-shot retry credit: if the agent paid but the attestation itself failed
-- (Solana congestion, kernel error), a credit keyed to its mint_id lets it retry
-- once for free. Credits expire after 24h (expires_at) and flip consumed=true
-- when claimed (the consume is filtered on consumed=false, so it's single-use).
create table if not exists public.mint_attest_credits (
    id          uuid primary key default gen_random_uuid(),
    mint_id     text        not null,
    source_tx   text,                              -- the payment that earned the credit
    consumed    boolean     not null default false,
    expires_at  timestamptz not null,
    created_at  timestamptz not null default now()
);

create index if not exists mint_credits_lookup_idx
    on public.mint_attest_credits (mint_id, consumed, expires_at desc);

-- ── revenue view (convenience) ───────────────────────────────────────────────
create or replace view public.mint_revenue_daily as
    select date_trunc('day', created_at) as day,
           count(*)            as paid_attestations,
           sum(amount_usdc)    as usdc_collected
    from public.mint_payments
    where status = 'settled'
    group by 1
    order by 1 desc;
