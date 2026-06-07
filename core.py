"""Shared register/attest/verify logic — ONE implementation behind both surfaces.

The MCP tools (tools/*.py, used by MCP/SSE clients) and the REST routes
(server.py /v1/register|attest|verify, used by the mint-attest Python SDK and any
HTTP client) both call these functions, so the two surfaces can never drift.

`api_key` is the per-request Forge key. Over MCP it's None → the server's service
key is used. Over REST it's the SDK developer's fnet_ key, passed through to Forge
so the actor + its attestations belong to THEIR account (Forge's /v1/attest
ownership check requires register + attest to use the same key).
"""
from __future__ import annotations

import uuid
from typing import Optional

import actor_registry
import config
import forge_client

# ── register ──────────────────────────────────────────────────────────────────
VALID_ACTOR_TYPES = {"ai_agent", "machine", "iot_device", "service"}
_SERIAL_NS = uuid.UUID("4d494e54-0000-4000-8000-000000000001")  # "MINT"


def derive_serial(actor_type: str, name: str, operator: Optional[str]) -> str:
    """Stable, idempotent serial for one logical actor; distinct per operator."""
    seed = f"{actor_type}|{name}|{operator or ''}".lower()
    return uuid.uuid5(_SERIAL_NS, seed).hex


async def do_register(actor_type: str, name: str,
                      capabilities: Optional[list] = None,
                      operator: Optional[str] = None,
                      metadata: Optional[dict] = None,
                      api_key: Optional[str] = None) -> dict:
    atype = (actor_type or "").strip().lower()
    if atype not in VALID_ACTOR_TYPES:
        return {"error": "bad_request",
                "detail": f"actor_type must be one of {sorted(VALID_ACTOR_TYPES)}, got {actor_type!r}"}
    if not (name or "").strip():
        return {"error": "bad_request", "detail": "name is required"}
    if not forge_client.configured(api_key):
        return {"error": "not_configured",
                "detail": "No Forge API key available (pass an fnet_ key or set FORGE_API_KEY)."}

    serial = derive_serial(atype, name, operator)
    meta = dict(metadata or {})
    meta.update({"mint_actor_type": atype, "mint_actor_name": name,
                 "mint_capabilities": capabilities or []})
    if operator:
        meta["mint_operator"] = operator

    resp = await forge_client.identify(oem=atype, model=name, serial=serial,
                                       site=operator, metadata=meta, api_key=api_key)
    if "error" in resp:
        return resp

    mint_id = resp.get("mint_id")
    machine = resp.get("machine") or {}
    if mint_id:
        actor_registry.remember(mint_id, actor_type=atype, name=name,
                                capabilities=capabilities, operator=operator)
    return {
        "mint_id": mint_id, "actor_type": atype, "name": name,
        "capabilities": capabilities or [], "operator": operator,
        "registered": True, "newly_registered": bool(resp.get("created")),
        "first_seen": resp.get("first_seen"),
        "wallet_address": machine.get("wallet_address"),
        "status": machine.get("status", "active"),
        "note": ("Identity is persistent and on-chain. Use this mint_id for "
                 "attest (prove work) and verify (query trust)."),
    }


# ── attest ────────────────────────────────────────────────────────────────────
VALID_WORK_TYPES = {"code_review", "normalization", "research", "generation",
                    "analysis", "delivery", "manufacturing", "custom"}
_WORK_COMPLEXITY = {"code_review": 1500, "analysis": 1400, "research": 1300,
                    "manufacturing": 1200, "generation": 1100, "normalization": 1000,
                    "custom": 1000, "delivery": 700}


async def do_attest(mint_id: str, work_type: str, duration_seconds,
                    summary: str = "", input_hash: Optional[str] = None,
                    output_hash: Optional[str] = None, metadata: Optional[dict] = None,
                    api_key: Optional[str] = None) -> dict:
    if not (mint_id or "").startswith("MINT-"):
        return {"error": "bad_request",
                "detail": f"mint_id must look like 'MINT-xxxxxx', got {mint_id!r}. Register first."}
    wtype = (work_type or "").strip().lower()
    if wtype not in VALID_WORK_TYPES:
        return {"error": "bad_request",
                "detail": f"work_type must be one of {sorted(VALID_WORK_TYPES)}, got {work_type!r}"}
    try:
        duration_seconds = int(duration_seconds)
    except (TypeError, ValueError):
        return {"error": "bad_request", "detail": "duration_seconds must be an integer"}
    if duration_seconds <= 0:
        return {"error": "bad_request", "detail": "duration_seconds must be > 0"}
    if not forge_client.configured(api_key):
        return {"error": "not_configured",
                "detail": "No Forge API key available (pass an fnet_ key or set FORGE_API_KEY)."}

    complexity = _WORK_COMPLEXITY.get(wtype, 1000)
    receipt = await forge_client.attest(
        mint_id, duration_seconds, complexity=complexity, work_type=wtype,
        input_hash=input_hash, output_hash=output_hash, summary=summary,
        metadata=metadata, api_key=api_key)
    if "error" in receipt:
        return {"error": "attest_failed", "detail": receipt,
                "hint": "On-chain anchor failed; nothing was minted. Retry."}

    actor_registry.record_work(mint_id, wtype)
    tx = receipt.get("tx_signature")
    verify_url = receipt.get("verify_url") or (
        f"{config.SOLSCAN_TX_BASE}/{tx}" if tx else None)
    return {
        "attestation_id": receipt.get("attestation_id"), "mint_id": mint_id,
        "work_type": wtype, "data_hash": receipt.get("data_hash"),
        "tx_signature": tx, "verify_url": verify_url,
        "trust_score": receipt.get("trust_score"), "reward": receipt.get("reward"),
        "settled": bool(receipt.get("settled", bool(tx))),
        "note": ("On-chain anchor is real; verify_url is a live Solscan link, and "
                 "this attestation permanently accrues to the actor's mint_id."),
    }


# ── verify ────────────────────────────────────────────────────────────────────
_PENDING_NOTE = (
    "Trust score + on-chain attestation history are served by Forge's trust-read "
    "endpoint, which is rolling out next. Attestations are already permanent "
    "on-chain and will surface here once the read endpoint is wired.")


async def do_verify(mint_id: Optional[str] = None, actor_name: Optional[str] = None,
                    actor_type: Optional[str] = None) -> dict:
    local: Optional[dict] = None
    if mint_id:
        local = actor_registry.lookup(mint_id)
    elif actor_name:
        found = actor_registry.find_by_name(actor_name, actor_type)
        if found:
            mint_id, local = found
    else:
        return {"error": "bad_request", "detail": "Provide either mint_id or actor_name."}

    if not mint_id:
        return {"error": "not_found",
                "detail": f"No mint_id known on this instance for actor_name={actor_name!r}. "
                          "Pass the mint_id directly.", "verifiable": True}
    if not mint_id.startswith("MINT-"):
        return {"error": "bad_request",
                "detail": f"mint_id must look like 'MINT-xxxxxx', got {mint_id!r}"}

    return {
        "mint_id": mint_id, "registered": local is not None,
        "actor_type": (local or {}).get("actor_type"), "name": (local or {}).get("name"),
        "capabilities": (local or {}).get("capabilities", []),
        "operator": (local or {}).get("operator"),
        "trust_score": "pending", "total_attestations": "pending",
        "work_types": (local or {}).get("work_types", {}),
        "recent_attestations": [], "verification": "on-chain", "verifiable": True,
        "trust_read_status": "pending_forge_endpoint", "note": _PENDING_NOTE,
    }
