create table if not exists public.mint_agents (
    mint_id              text primary key,
    trust_score          integer     not null default 100 check (trust_score between 0 and 100),
    job_count            integer     not null default 0,
    total_duration       bigint      not null default 0,
    complexity_sum       bigint      not null default 0,
    is_banned            boolean     not null default false,
    on_probation         boolean     not null default false,
    probation_count      integer     not null default 0,
    probation_started_at timestamptz,
    registered_at        timestamptz not null default now(),
    last_job_at          timestamptz
);

create table if not exists public.mint_network_state (
    id                    integer primary key default 1 check (id = 1),
    total_jobs            bigint      not null default 0,
    total_duration        bigint      not null default 0,
    total_complexity_sum  bigint      not null default 0,
    window_jobs           bigint      not null default 0,
    window_duration       bigint      not null default 0,
    window_complexity_sum bigint      not null default 0,
    window_start          timestamptz not null default now()
);

insert into public.mint_network_state (id) values (1) on conflict (id) do nothing;

alter table public.mint_attestations
    add column if not exists ml_confidence         integer,
    add column if not exists trust_delta           integer,
    add column if not exists base_score            bigint,
    add column if not exists trust_weighted_score  bigint,
    add column if not exists complexity_claimed    integer,
    add column if not exists normalized_complexity integer;

create index if not exists idx_mint_attestations_mint_created
    on public.mint_attestations (mint_id, created_at desc);
