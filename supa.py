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
                          merkle batch anchoring), mint_anchor_batches (anchor ledger)
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
