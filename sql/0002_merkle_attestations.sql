create table if not exists public.mint_attestations (
    id               uuid        primary key default gen_random_uuid(),
    mint_id          text        not null,
    work_type        text        not null,
    data_hash        text        not null,
    duration_seconds integer     not null,
    summary          text,
    payment_tx       text,
    attestation_hash text        not null,
    status           text        not null default 'attested',
    batch_id         uuid,
    merkle_proof     jsonb,
    merkle_root      text,
    anchor_tx        text,
    created_at       timestamptz not null default now(),
    anchored_at      timestamptz
);

create unique index if not exists mint_attestations_hash_uidx
    on public.mint_attestations (attestation_hash);

create index if not exists mint_attestations_unanchored_idx
    on public.mint_attestations (status, created_at)
    where status = 'attested';

create index if not exists mint_attestations_mint_idx
    on public.mint_attestations (mint_id, created_at desc);

create index if not exists mint_attestations_batch_idx
    on public.mint_attestations (batch_id);

create table if not exists public.mint_anchor_batches (
    id            uuid        primary key default gen_random_uuid(),
    merkle_root   text        not null,
    batch_size    integer     not null,
    anchor_tx     text        not null unique,
    memo          text,
    anchored_at   timestamptz not null default now()
);

create index if not exists mint_anchor_batches_time_idx
    on public.mint_anchor_batches (anchored_at desc);
