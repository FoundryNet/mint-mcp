"""Supabase (PostgREST) client for the MINT trust layer — Layers 6 + 7.

mint-mcp stays a thin layer: identity + attestation go through Forge, but the
trust/discovery aggregates (ratings, recommendations, trust scores, the actor
directory) live in the Foundry Supabase project and are read/written here
directly via PostgREST with the service-role key.

No supabase-py dependency — we already speak HTTP (httpx via http_util) and only
need a handful of table operations, so we call PostgREST directly to keep the
container lean. Every function returns plain data or {"error": …} (mirroring
forge_client), never raising across the MCP frame. When SUPABASE_SERVICE_KEY is
unset, `configured()` is False and callers degrade to identity-only behavior.

Tables owned here:        mint_actors, mint_ratings, mint_recommendations,
                          mint_trust_scores, mint_payments (x402 revenue ledger +
                          used-tx store), mint_attest_credits (retry credits),
                          mint_attestations (the primary attestation store —
                          merkle batch anchoring), mint_anchor_batches (anchor ledger),
                          mint_agents (per-agent trust state — ported on-chain
                          MachineState), mint_network_state (rolling window — NetworkState)
Forge tables read here:   forge_trigger_executions (attestation events),
                          forge_agent_machines (key→owned mint_ids)
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

import config
from http_util import request_json

logger = logging.getLogger("mint.supa")

ATTEST_EVENT = "attestation"
_WORKTYPE_SAMPLE = 1000   # rows scanned for the work_type breakdown (display only)


def configured() -> bool:
    return bool(config.SUPABASE_URL and config.SUPABASE_SERVICE_KEY)


def _headers(extra: Optional[dict] = None) -> dict:
    h = {
        "apikey":        config.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _url(table: str) -> str:
    return f"{config.SUPABASE_URL}/rest/v1/{table}"


async def _select(table: str, params: dict) -> list:
    """GET rows; returns a list (possibly empty) or [] on error (logged)."""
    if not configured():
        return []
    r = await request_json("GET", _url(table), headers=_headers(),
                           params=params, timeout=config.REQUEST_TIMEOUT)
    if isinstance(r, list):
        return r
    logger.warning(f"supa select {table} failed: {r}")
    return []


async def _write(method: str, table: str, body, *, params: Optional[dict] = None,
                 prefer: str = "return=representation") -> dict:
    """POST/PATCH; returns {"data": [...]}, or {"error": …} on failure."""
    if not configured():
        return {"error": "not_configured", "detail": "SUPABASE_SERVICE_KEY unset"}
    r = await request_json(method, _url(table), headers=_headers({"Prefer": prefer}),
                           body=body, params=params, timeout=config.REQUEST_TIMEOUT)
    if isinstance(r, list):
        return {"data": r}
    if isinstance(r, dict) and "error" not in r:
        return {"data": [r]}
    return r if isinstance(r, dict) else {"error": "bad_response", "detail": str(r)}


async def _count(table: str, params: dict) -> int:
    """Exact row count via PostgREST's Content-Range header (range 0-0)."""
    if not configured():
        return 0
    q = dict(params)
    q.setdefault("select", "id")
    headers = _headers({"Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"})
    try:
        async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
            resp = await client.get(_url(table), headers=headers, params=q)
        cr = resp.headers.get("content-range", "")   # e.g. "0-0/12345" or "*/0"
        total = cr.split("/")[-1] if "/" in cr else ""
        return int(total) if total.isdigit() else 0
    except Exception as e:
        logger.warning(f"supa count {table} failed: {e}")
        return 0


# ── mint_actors ──────────────────────────────────────────────────────────────

async def upsert_actor(mint_id: str, *, name: str, actor_type: str,
                       capabilities: Optional[list] = None,
                       operator: Optional[str] = None,
                       mcp_endpoint: Optional[str] = None,
                       description: Optional[str] = None,
                       last_active: Optional[str] = None) -> dict:
    row = {"mint_id": mint_id, "name": name, "actor_type": actor_type,
           "capabilities": capabilities or []}
    if operator is not None:     row["operator"] = operator
    if mcp_endpoint is not None:  row["mcp_endpoint"] = mcp_endpoint
    if description is not None:   row["description"] = description
    if last_active is not None:   row["last_active"] = last_active
    return await _write("POST", "mint_actors", row,
                        prefer="resolution=merge-duplicates,return=representation")


async def get_actor(mint_id: str) -> Optional[dict]:
    rows = await _select("mint_actors", {"mint_id": f"eq.{mint_id}", "limit": "1"})
    return rows[0] if rows else None


# ── mint_trust_scores ────────────────────────────────────────────────────────

async def get_trust(mint_id: str) -> Optional[dict]:
    rows = await _select("mint_trust_scores", {"mint_id": f"eq.{mint_id}", "limit": "1"})
    return rows[0] if rows else None


async def upsert_trust(mint_id: str, fields: dict) -> dict:
    row = {"mint_id": mint_id, **fields}
    return await _write("POST", "mint_trust_scores", row,
                        prefer="resolution=merge-duplicates,return=representation")


# ── mint_ratings ─────────────────────────────────────────────────────────────

async def insert_rating(row: dict) -> dict:
    return await _write("POST", "mint_ratings", row)


async def ratings_for(rated_mint_id: str, limit: int = 1000) -> list:
    return await _select("mint_ratings", {
        "rated_mint_id": f"eq.{rated_mint_id}",
        "select": "score,accuracy,would_use_again,tags,comment,rater_mint_id,attestation_id,created_at",
        "order": "created_at.desc", "limit": str(limit)})


async def rating_exists(attestation_id: str, rater_mint_id: str) -> bool:
    rows = await _select("mint_ratings", {
        "attestation_id": f"eq.{attestation_id}",
        "rater_mint_id":  f"eq.{rater_mint_id}",
        "select": "id", "limit": "1"})
    return bool(rows)


# ── mint_recommendations ─────────────────────────────────────────────────────

async def insert_recommendation(row: dict) -> dict:
    return await _write("POST", "mint_recommendations", row)


async def recommendations_for(recommended_mint_id: str, limit: int = 1000) -> list:
    return await _select("mint_recommendations", {
        "recommended_mint_id": f"eq.{recommended_mint_id}",
        "select": "recommender_mint_id,context,score,note,attestation_id,created_at",
        "order": "created_at.desc", "limit": str(limit)})


async def count_recommendations_given(recommender_mint_id: str) -> int:
    return await _count("mint_recommendations",
                        {"recommender_mint_id": f"eq.{recommender_mint_id}"})


# ── Forge tables (read-only) ─────────────────────────────────────────────────

async def owner_mint_ids(user_id: str) -> list:
    """The mint_ids a Forge account (user_id) owns — used to bind a rater/
    recommender to an identity they actually control."""
    rows = await _select("forge_agent_machines",
                         {"user_id": f"eq.{user_id}", "select": "mint_id", "limit": "1000"})
    return [r["mint_id"] for r in rows if r.get("mint_id")]


async def attestation_count(mint_id: str) -> int:
    return await _count("forge_trigger_executions",
                        {"mint_id": f"eq.{mint_id}", "event_type": f"eq.{ATTEST_EVENT}"})


async def attestation_last_active(mint_id: str) -> Optional[str]:
    rows = await _select("forge_trigger_executions", {
        "mint_id": f"eq.{mint_id}", "event_type": f"eq.{ATTEST_EVENT}",
        "select": "occurred_at", "order": "occurred_at.desc", "limit": "1"})
    return rows[0].get("occurred_at") if rows else None


async def attestation_work_types(mint_id: str) -> dict:
    """work_type → count over a bounded recent sample (display only; the exact
    total comes from attestation_count). Capped at _WORKTYPE_SAMPLE rows."""
    rows = await _select("forge_trigger_executions", {
        "mint_id": f"eq.{mint_id}", "event_type": f"eq.{ATTEST_EVENT}",
        "select": "payload", "order": "occurred_at.desc", "limit": str(_WORKTYPE_SAMPLE)})
    out: dict = {}
    for r in rows:
        wt = ((r.get("payload") or {}).get("work_type")) or "custom"
        out[wt] = out.get(wt, 0) + 1
    return out


# ── mint_payments (x402 revenue ledger + used-tx / double-spend store) ────────
# Every verified attestation payment lands here. `tx_signature` is UNIQUE, so the
# insert itself IS the double-spend guard: a replayed signature 409s. The same row
# is the revenue ledger (timestamp, amount, payer wallet, intent/attestation_id).

async def insert_payment(row: dict) -> dict:
    """Record a verified payment. Relies on a UNIQUE constraint on tx_signature;
    a duplicate signature returns an error whose detail carries the 409/conflict."""
    return await _write("POST", "mint_payments", row, prefer="return=representation")


async def payment_tx_used(tx_signature: str) -> bool:
    rows = await _select("mint_payments",
                         {"tx_signature": f"eq.{tx_signature}", "select": "id", "limit": "1"})
    return bool(rows)


async def finalize_payment(tx_signature: str, fields: dict) -> dict:
    """Patch a payment row after the attestation resolves (status + attestation_id)."""
    return await _write("PATCH", "mint_payments", fields,
                        params={"tx_signature": f"eq.{tx_signature}"})


# ── mint_attest_credits (retry credits for paid-but-failed attestations) ──────
# If the agent paid but the attestation itself failed, a one-shot credit keyed to
# its mint_id lets it retry once for free. Credits carry an expires_at; consuming
# one flips consumed=true so it can't be reused.

async def insert_credit(row: dict) -> dict:
    return await _write("POST", "mint_attest_credits", row, prefer="return=representation")


async def active_credit(mint_id: str, now_iso: str) -> Optional[dict]:
    """The newest unconsumed, unexpired credit for an actor, or None."""
    rows = await _select("mint_attest_credits", {
        "mint_id": f"eq.{mint_id}", "consumed": "eq.false",
        "expires_at": f"gt.{now_iso}",
        "select": "id,mint_id,expires_at,source_tx", "order": "created_at.desc", "limit": "1"})
    return rows[0] if rows else None


async def consume_credit(credit_id) -> dict:
    """Atomically claim a credit: only flips it if still unconsumed (the
    consumed=eq.false filter makes a double-consume a no-op, returning [])."""
    return await _write("PATCH", "mint_attest_credits", {"consumed": True},
                        params={"id": f"eq.{credit_id}", "consumed": "eq.false"})


# ── mint_attestations (primary attestation store — merkle batch anchoring) ────
# One row per attested unit of work. Inserted as status 'attested' the moment the
# payment clears; flipped to 'anchored' (with proof + root + anchor_tx) when the
# batch anchorer writes the batch's merkle root on-chain. attestation_hash is the
# merkle leaf AND the public verify handle (UNIQUE).

async def insert_attestation(row: dict) -> dict:
    return await _write("POST", "mint_attestations", row, prefer="return=representation")


_SOLSCAN = "https://solscan.io/tx/"


def _derive_source(name: str, summary: str) -> str:
    """The specific server/agent behind an attestation. Network attestations
    share two umbrella actors (foundrynet-data-network, foundrynet-agent-fleet),
    but the originating server/agent is encoded in the summary, e.g.
    'content_generator completed: …', 'Daily financial-signals brief: …',
    'anomaly_alert query result'. Falls back to the actor name."""
    import re
    s = (summary or "").strip()
    m = re.match(r"^([A-Za-z][\w-]+) completed", s)          # fleet agents
    if m:
        return m.group(1)
    m = re.match(r"^Daily ([\w-]+) brief", s)                # data-server daily brief
    if m:
        return m.group(1) + "-mcp"
    m = re.match(r"^([a-z][a-z0-9_]+) query result", s)      # premium tool result
    if m:
        return m.group(1)
    return name


async def recent_attestations(limit: int = 50) -> list:
    """Newest attestations across ALL actors, enriched with the originating
    server/agent source, the actor's trust score, ML confidence, anchor status,
    merkle root, and a Solscan link. Powers the public live feed. Read-only."""
    n = min(max(int(limit or 50), 1), 100)
    rows = await _select("mint_attestations", {
        "select": "mint_id,work_type,summary,attestation_hash,status,created_at,"
                  "anchored_at,merkle_root,anchor_tx,ml_confidence,trust_weighted_score",
        "order": "created_at.desc", "limit": str(n)})
    if not rows:
        return []
    ids = list({r["mint_id"] for r in rows if r.get("mint_id")})
    names: dict = {}
    trust: dict = {}
    if ids:
        inlist = "in.(" + ",".join(ids) + ")"
        for a in await _select("mint_actors",
                               {"select": "mint_id,name,actor_type", "mint_id": inlist,
                                "limit": str(len(ids))}):
            names[a["mint_id"]] = a
        for t in await _select("mint_trust_scores",
                               {"select": "mint_id,trust_score", "mint_id": inlist,
                                "limit": str(len(ids))}):
            trust[t["mint_id"]] = t.get("trust_score")
    out = []
    for r in rows:
        mid = r.get("mint_id")
        a = names.get(mid, {})
        anchored = r.get("status") == "anchored"
        atx = r.get("anchor_tx")
        out.append({
            "mint_id": mid,
            "name": a.get("name") or mid,
            "source": _derive_source(a.get("name") or mid, r.get("summary")),
            "actor_type": a.get("actor_type"),
            "work_type": r.get("work_type"),
            "summary": r.get("summary"),
            "attestation_hash": r.get("attestation_hash"),
            "merkle_root": r.get("merkle_root"),
            "anchor_tx": atx,
            "solscan_url": (_SOLSCAN + atx) if (anchored and atx) else None,
            "status": r.get("status"),
            "anchored": anchored,
            "anchored_at": r.get("anchored_at"),
            "confidence": r.get("ml_confidence"),
            "trust_score": trust.get(mid),
            "trust_weighted_score": r.get("trust_weighted_score"),
            "created_at": r.get("created_at"),
        })
    return out


async def feed_stats() -> dict:
    """Showcase counters for the live-feed stats bar: attestations today, distinct
    active sources (24h), operational servers (from network_health), avg trust."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
    since = datetime.now(timezone.utc).timestamp() - 86400
    since_iso = datetime.fromtimestamp(since, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    attest_today = await _count("mint_attestations", {"created_at": f"gte.{today}"})

    # Distinct active sources + avg trust over the last 24h of attestations.
    recent = await _select("mint_attestations", {
        "select": "mint_id,summary", "created_at": f"gte.{since_iso}",
        "order": "created_at.desc", "limit": "500"})
    sources = set()
    actor_ids = set()
    for r in recent:
        actor_ids.add(r.get("mint_id"))
        sources.add(_derive_source(r.get("mint_id") or "", r.get("summary")))
    avg_trust = None
    if actor_ids:
        ids = [i for i in actor_ids if i]
        if ids:
            inlist = "in.(" + ",".join(ids) + ")"
            ts = await _select("mint_trust_scores",
                               {"select": "trust_score", "mint_id": inlist, "limit": "200"})
            vals = [t["trust_score"] for t in ts if t.get("trust_score") is not None]
            if vals:
                avg_trust = round(sum(vals) / len(vals))

    # Operational servers from the network_health table (latest row per endpoint).
    servers_up = servers_total = None
    nh = await _select("network_health", {
        "select": "endpoint,healthy,checked_at", "order": "checked_at.desc", "limit": "300"})
    if nh:
        latest: dict = {}
        for row in nh:
            latest.setdefault(row["endpoint"], row)
        servers_total = len(latest)
        servers_up = sum(1 for v in latest.values() if v.get("healthy"))

    return {
        "attestations_today": attest_today,
        "active_sources": len(sources),
        "servers_up": servers_up,
        "servers_total": servers_total,
        "avg_trust": avg_trust,
    }


async def get_attestation_by_hash(attestation_hash: str) -> Optional[dict]:
    rows = await _select("mint_attestations",
                         {"attestation_hash": f"eq.{attestation_hash}", "select": "*", "limit": "1"})
    return rows[0] if rows else None


async def attestations_for_mint(mint_id: str, limit: int = 20) -> list:
    return await _select("mint_attestations", {
        "mint_id": f"eq.{mint_id}", "select": "*",
        "order": "created_at.desc", "limit": str(limit)})


async def list_attested(limit: int = 1000) -> list:
    """Unanchored attestations, oldest first — what the next anchor batch drains."""
    return await _select("mint_attestations", {
        "status": "eq.attested", "select": "*",
        "order": "created_at.asc", "limit": str(limit)})


async def mark_attestation_anchored(att_id: str, fields: dict) -> dict:
    """Flip ONE attestation to 'anchored' with its proof/root/anchor_tx. The
    status=eq.attested filter makes the PATCH idempotent (a row already anchored by
    a concurrent pass is left untouched and returns [])."""
    return await _write("PATCH", "mint_attestations", {"status": "anchored", **fields},
                        params={"id": f"eq.{att_id}", "status": "eq.attested"})


async def attested_count() -> int:
    return await _count("mint_attestations", {"status": "eq.attested"})


async def anchored_count() -> int:
    return await _count("mint_attestations", {"status": "eq.anchored"})


# trust union: these mirror the forge_trigger_executions readers above so trust.py
# counts attestations from BOTH stores (the two are disjoint — a given attestation
# lives in exactly one — so summing is exact, never double-counts).

async def mint_attestation_count(mint_id: str) -> int:
    return await _count("mint_attestations", {"mint_id": f"eq.{mint_id}"})


async def mint_attestation_last_active(mint_id: str) -> Optional[str]:
    rows = await _select("mint_attestations", {
        "mint_id": f"eq.{mint_id}", "select": "created_at",
        "order": "created_at.desc", "limit": "1"})
    return rows[0].get("created_at") if rows else None


async def mint_attestation_work_types(mint_id: str) -> dict:
    rows = await _select("mint_attestations", {
        "mint_id": f"eq.{mint_id}", "select": "work_type",
        "order": "created_at.desc", "limit": str(_WORKTYPE_SAMPLE)})
    out: dict = {}
    for r in rows:
        wt = r.get("work_type") or "custom"
        out[wt] = out.get(wt, 0) + 1
    return out


# ── mint_agents + mint_network_state (trust engine — ported on-chain scoring) ─
# mint_agents mirrors the on-chain MachineState (per-agent trust/probation/ban);
# mint_network_state is the single-row (id=1) rolling weekly window used for
# complexity normalization. Created in sql/0003_trust_engine.sql. Trust starts at
# trust_engine.TRUST_START (100) via the column default.

# Defaults returned when Supabase is unconfigured OR a row is missing, so the
# engine always has something to score against (degrades gracefully, never raises).
_AGENT_DEFAULTS = {
    "trust_score": 100, "job_count": 0, "total_duration": 0, "complexity_sum": 0,
    "is_banned": False, "on_probation": False, "probation_count": 0,
    "probation_started_at": None, "last_job_at": None,
}
_NETWORK_DEFAULTS = {
    "id": 1, "total_jobs": 0, "total_duration": 0, "total_complexity_sum": 0,
    "window_jobs": 0, "window_duration": 0, "window_complexity_sum": 0,
    "window_start": None,
}


async def get_agent(mint_id: str) -> Optional[dict]:
    rows = await _select("mint_agents", {"mint_id": f"eq.{mint_id}", "select": "*", "limit": "1"})
    return rows[0] if rows else None


async def create_agent(mint_id: str) -> dict:
    """Insert the agent's trust-state row at registration. Idempotent: a repeat
    register sends only mint_id, so on-conflict it no-ops (existing trust state
    preserved); a new row picks up the column defaults (trust_score=100 etc.)."""
    return await _write("POST", "mint_agents", {"mint_id": mint_id},
                        prefer="resolution=merge-duplicates,return=representation")


async def update_agent(mint_id: str, fields: dict) -> dict:
    return await _write("PATCH", "mint_agents", fields, params={"mint_id": f"eq.{mint_id}"})


async def get_or_create_agent(mint_id: str) -> dict:
    """The agent's trust state, creating it on first sight. Always returns a dict
    (falls back to in-memory defaults when Supabase is unconfigured)."""
    if not configured():
        return {"mint_id": mint_id, **_AGENT_DEFAULTS}
    row = await get_agent(mint_id)
    if row:
        return row
    await create_agent(mint_id)
    return (await get_agent(mint_id)) or {"mint_id": mint_id, **_AGENT_DEFAULTS}


async def jobs_in_last_hour(mint_id: str) -> int:
    """Count this agent's attestations in the trailing hour — the rolling input for
    the ml_scorer's jobs_last_hour_machine rate-anomaly feature. One cheap COUNT;
    0 when Supabase is unconfigured. Excludes the in-flight attestation (it isn't
    recorded until after scoring)."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return await _count("mint_attestations",
                        {"mint_id": f"eq.{mint_id}", "created_at": f"gt.{since}"})


async def get_network_state() -> dict:
    """The single-row network aggregate (id=1), creating it if absent. Always
    returns a dict (defaults when unconfigured/missing)."""
    if not configured():
        return dict(_NETWORK_DEFAULTS)
    rows = await _select("mint_network_state", {"id": "eq.1", "select": "*", "limit": "1"})
    if rows:
        return rows[0]
    await _write("POST", "mint_network_state", {"id": 1},
                 prefer="resolution=merge-duplicates,return=representation")
    rows = await _select("mint_network_state", {"id": "eq.1", "select": "*", "limit": "1"})
    return rows[0] if rows else dict(_NETWORK_DEFAULTS)


async def update_network_state(fields: dict) -> dict:
    return await _write("PATCH", "mint_network_state", fields, params={"id": "eq.1"})


# ── mint_anchor_batches (one row per on-chain anchor tx) ──────────────────────

async def insert_anchor_batch(row: dict) -> dict:
    return await _write("POST", "mint_anchor_batches", row, prefer="return=representation")


async def last_anchor_batch() -> Optional[dict]:
    rows = await _select("mint_anchor_batches",
                         {"select": "*", "order": "anchored_at.desc", "limit": "1"})
    return rows[0] if rows else None


async def anchor_batch_count() -> int:
    return await _count("mint_anchor_batches", {})


# ── discovery ────────────────────────────────────────────────────────────────

async def actor_pool(actor_type: Optional[str], limit: int = 500) -> list:
    """Candidate actors for discovery. actor_type filtered in the DB; capability/
    text matching happens in Python (PostgREST array-text search is brittle)."""
    params: dict = {"select": "*", "order": "registered_at.desc", "limit": str(limit)}
    if actor_type:
        params["actor_type"] = f"eq.{actor_type}"
    return await _select("mint_actors", params)


async def trust_for_ids(mint_ids: list) -> dict:
    """mint_id → trust row, for a set of actors (one IN query)."""
    if not mint_ids:
        return {}
    ids = ",".join(mint_ids)
    rows = await _select("mint_trust_scores", {"mint_id": f"in.({ids})", "select": "*"})
    return {r["mint_id"]: r for r in rows if r.get("mint_id")}


async def recommendations_for_ids(mint_ids: list) -> dict:
    """mint_id → list of recommendation rows, for a set of recommended actors."""
    if not mint_ids:
        return {}
    ids = ",".join(mint_ids)
    rows = await _select("mint_recommendations", {
        "recommended_mint_id": f"in.({ids})",
        "select": "recommended_mint_id,recommender_mint_id,context,score,note",
        "order": "created_at.desc", "limit": "2000"})
    out: dict = {}
    for r in rows:
        out.setdefault(r["recommended_mint_id"], []).append(r)
    return out
