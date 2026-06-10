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

import hashlib
import json
import uuid
from typing import Optional

import actor_registry
import config
import forge_client
import merkle_batch
import payment_gate
import supa
import trust


def _data_hash(payload: dict) -> str:
    """sha256 over canonical JSON (sorted keys, no whitespace) — the reproducible
    off-chain commitment. Same canonicalization Forge /v1/attest uses, so a hash
    can be recomputed and checked independently."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        .encode("utf-8")).hexdigest()


async def _resolve_rater(api_key: Optional[str], claimed: Optional[str]) -> dict:
    """Bind a rater/recommender to a mint_id their fnet_ key actually owns.

    Returns {"mint_id": …} on success or {"error": …} otherwise. Anti-spam: only
    a real Forge account can rate, and only as an actor it controls. `claimed`
    (if given) must be among the key's owned mint_ids; otherwise we auto-pick when
    the key owns exactly one actor, and ask for disambiguation when it owns many.
    """
    if not api_key:
        return {"error": "not_configured",
                "detail": "An fnet_ API key is required to identify the rater "
                          "(pass Authorization: Bearer for REST, or set FORGE_API_KEY)."}
    who = await forge_client.whoami(api_key)
    if "error" in who:
        return who
    user_id = who.get("user_id")
    if not user_id:
        return {"error": "http_401", "detail": "Key did not resolve to an account."}
    owned = await supa.owner_mint_ids(user_id)
    if claimed:
        if claimed not in owned:
            return {"error": "forbidden",
                    "detail": f"{claimed} is not owned by this API key. You can only "
                              f"rate/recommend as an actor your key controls."}
        return {"mint_id": claimed}
    if len(owned) == 1:
        return {"mint_id": owned[0]}
    if not owned:
        return {"error": "bad_request",
                "detail": "This key owns no registered actor. Register one first "
                          "(mint_register) so your rating is attributable."}
    return {"error": "bad_request",
            "detail": f"This key owns {len(owned)} actors; pass rater_mint_id / "
                      f"recommender_mint_id to say which one is rating."}

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
                      mcp_endpoint: Optional[str] = None,
                      description: Optional[str] = None,
                      api_key: Optional[str] = None) -> dict:
    atype = (actor_type or "").strip().lower()
    if atype not in VALID_ACTOR_TYPES:
        return {"error": "bad_request",
                "detail": f"actor_type must be one of {sorted(VALID_ACTOR_TYPES)}, got {actor_type!r}"}
    if not (name or "").strip():
        return {"error": "bad_request", "detail": "name is required"}

    # Autonomous self-registration: the caller passed NO fnet_ key, so provision
    # a fresh MINT identity AND a scoped fnet_ key in one call — no human, no
    # signup. (A caller WITH a key registers under their own account, below.)
    if api_key is None:
        return await _autonomous_register(atype, name, capabilities, operator,
                                          metadata, mcp_endpoint, description)

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
        await _add_to_directory(mint_id, atype, name, capabilities, operator,
                                mcp_endpoint, description)
    return {
        "mint_id": mint_id, "actor_type": atype, "name": name,
        "capabilities": capabilities or [], "operator": operator,
        "mcp_endpoint": mcp_endpoint, "description": description,
        "registered": True, "newly_registered": bool(resp.get("created")),
        "first_seen": resp.get("first_seen"),
        "wallet_address": machine.get("wallet_address"),
        "status": machine.get("status", "active"), "trust_score": 50,
        "discoverable": supa.configured(),
        "note": ("Identity is persistent and on-chain. Use this mint_id for "
                 "attest (prove work), rate/recommend (build trust), and verify "
                 "or discover (query trust). New actors start at trust 50."),
    }


async def _add_to_directory(mint_id: str, actor_type: str, name: str,
                            capabilities: Optional[list], operator: Optional[str],
                            mcp_endpoint: Optional[str], description: Optional[str]) -> None:
    """Best-effort: make the actor discoverable + seed a neutral trust score.
    Never fails registration — identity already succeeded on Forge."""
    if not supa.configured():
        return
    await supa.upsert_actor(mint_id, name=name, actor_type=actor_type,
                            capabilities=capabilities, operator=operator,
                            mcp_endpoint=mcp_endpoint, description=description)
    if await supa.get_trust(mint_id) is None:
        await supa.upsert_trust(mint_id, {"trust_score": 50})


async def _autonomous_register(actor_type: str, name: str,
                               capabilities: Optional[list], operator: Optional[str],
                               metadata: Optional[dict],
                               mcp_endpoint: Optional[str] = None,
                               description: Optional[str] = None) -> dict:
    """No-key path: Forge mints a fresh identity AND a scoped fnet_ key in one
    anonymous call. The agent gets everything it needs to attest — no human."""
    resp = await forge_client.autonomous_register(
        actor_type=actor_type, name=name, capabilities=capabilities,
        operator=operator, metadata=metadata)
    if "error" in resp:
        return resp
    mint_id = resp.get("mint_id")
    if mint_id:
        actor_registry.remember(mint_id, actor_type=actor_type, name=name,
                                capabilities=capabilities, operator=operator)
        await _add_to_directory(mint_id, actor_type, name, capabilities, operator,
                                mcp_endpoint, description)
    return {
        "mint_id": mint_id,
        "api_key": resp.get("api_key"),           # one-shot — the agent MUST persist it
        "actor_type": actor_type, "name": name,
        "capabilities": capabilities or [], "operator": operator,
        "mcp_endpoint": mcp_endpoint, "description": description,
        "registered": True, "autonomous": True, "trust_score": 50,
        "discoverable": supa.configured(),
        "wallet_address": resp.get("wallet_address"),
        "daily_attest_limit": resp.get("daily_attest_limit"),
        "note": ("Identity + key provisioned with no human in the loop. PERSIST "
                 "api_key — it is shown once, is scoped to this mint_id, and is "
                 "required to attest. Register is free; attest is metered (free up "
                 "to the daily cap, then pay via x402 or a metered key)."),
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
                    payment_tx: Optional[str] = None, api_key: Optional[str] = None) -> dict:
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

    # Pay-per-attest gate (2¢ USDC on Solana). Inert unless armed; an fnet_ key or
    # a live retry credit bypasses it. A "blocked" decision returns the 402 body
    # verbatim — the REST layer maps error=payment_required to HTTP 402, and an MCP
    # client reads the {"status": 402, "payment_required": {…}} dict directly.
    intent = payment_gate.intent_id(mint_id, wtype, duration_seconds, summary,
                                    input_hash, output_hash, metadata)
    decision = await payment_gate.precheck(mint_id, intent, payment_tx, api_key)
    if decision["gate"] == "blocked":
        return decision["body"]

    # NEW — merkle batch flow (default): record the attestation off-chain and queue
    # it for batch anchoring, returning immediately. ONE on-chain tx anchors the
    # merkle root of a whole batch (merkle_batch.py), replacing the per-attestation
    # recordJob/settleJob/updateTrust that cost ~0.002 SOL each. The kill switch
    # MERKLE_ANCHOR_ENABLED=false drops to the per-attestation Forge path below.
    if config.MERKLE_ANCHOR_ENABLED:
        return await _attest_batched(mint_id, wtype, duration_seconds, summary,
                                     input_hash, output_hash, metadata, decision)

    complexity = _WORK_COMPLEXITY.get(wtype, 1000)
    receipt = await forge_client.attest(
        mint_id, duration_seconds, complexity=complexity, work_type=wtype,
        input_hash=input_hash, output_hash=output_hash, summary=summary,
        metadata=metadata, api_key=api_key)
    if "error" in receipt:
        # Attestation failed AFTER payment cleared — settle() grants a 24h retry
        # credit so the agent isn't out the 2¢, then we surface the failure.
        await payment_gate.settle(decision, mint_id, attestation_id=None, ok=False)
        out = {"error": "attest_failed", "detail": receipt,
               "hint": "On-chain anchor failed; nothing was minted. Retry."}
        if decision["gate"] in ("paid", "credit"):
            out["payment_status"] = "credited"
            out["hint"] = ("On-chain anchor failed; nothing was minted. Your payment "
                           "is preserved as a one-time retry credit (valid 24h) — "
                           "retry the SAME request with no new payment.")
        return out

    actor_registry.record_work(mint_id, wtype)
    attestation_id = receipt.get("attestation_id")
    # Attestation succeeded — finalize the revenue ledger row against the real
    # attestation_id (no-op for the api_key/open paths).
    await payment_gate.settle(decision, mint_id, attestation_id=attestation_id, ok=True)

    tx = receipt.get("tx_signature")
    verify_url = receipt.get("verify_url") or (
        f"{config.SOLSCAN_TX_BASE}/{tx}" if tx else None)
    out = {
        "attestation_id": attestation_id, "mint_id": mint_id,
        "work_type": wtype, "data_hash": receipt.get("data_hash"),
        "tx_signature": tx, "verify_url": verify_url,
        "trust_score": receipt.get("trust_score"), "reward": receipt.get("reward"),
        "settled": bool(receipt.get("settled", bool(tx))),
        "note": ("On-chain anchor is real; verify_url is a live Solscan link, and "
                 "this attestation permanently accrues to the actor's mint_id."),
    }
    if decision["gate"] == "paid":
        out["payment"] = {"method": "x402", "paid_usdc": decision.get("amount_usdc"),
                          "payment_tx": decision.get("payment_tx"), "payer": decision.get("payer")}
    elif decision["gate"] == "credit":
        out["payment"] = {"method": "retry_credit"}
    return out


async def _attest_batched(mint_id: str, wtype: str, duration_seconds: int,
                          summary: str, input_hash: Optional[str],
                          output_hash: Optional[str], metadata: Optional[dict],
                          decision: dict) -> dict:
    """Record the attestation off-chain (status 'attested') and queue it for the
    next merkle batch anchor, returning immediately. No per-attestation on-chain
    settlement — anchoring is one tx per batch, done asynchronously."""
    rec = await merkle_batch.record_attestation(
        mint_id=mint_id, work_type=wtype, duration_seconds=duration_seconds,
        summary=summary, input_hash=input_hash, output_hash=output_hash,
        metadata=metadata, payment_tx=decision.get("payment_tx"))
    if "error" in rec:
        # Recording failed AFTER payment cleared — make the agent whole with a
        # one-shot 24h retry credit, exactly like the old on-chain-failure path.
        await payment_gate.settle(decision, mint_id, attestation_id=None, ok=False)
        out = {"error": "attest_failed", "detail": rec.get("detail"),
               "hint": "Could not record the attestation; nothing accrued. Retry."}
        if decision["gate"] in ("paid", "credit"):
            out["payment_status"] = "credited"
            out["hint"] = ("Could not record the attestation; your payment is preserved "
                           "as a one-time retry credit (valid 24h) — retry the SAME "
                           "request with no new payment.")
        return out

    attestation_id = rec["attestation_id"]
    actor_registry.record_work(mint_id, wtype)
    # The attestation IS recorded and valid; anchoring is a later durability step
    # that never re-charges the agent. So payment settles successfully now.
    await payment_gate.settle(decision, mint_id, attestation_id=attestation_id, ok=True)

    # Reflect the new attestation in the actor's trust score (best-effort; the
    # trust layer counts mint_attestations alongside Forge history).
    trust_score = None
    if supa.configured():
        try:
            trust_score = (await trust.recompute(mint_id)).get("trust_score")
        except Exception:
            trust_score = None

    out = {
        "attestation_id": attestation_id, "mint_id": mint_id, "work_type": wtype,
        "data_hash": rec["data_hash"], "attestation_hash": rec["attestation_hash"],
        "status": "attested", "anchored": False, "pending_anchor": True,
        "anchor_eta": merkle_batch.next_anchor_eta(),
        "trust_score": trust_score,
        "note": ("Attestation recorded and paid. It will be anchored on-chain in the "
                 "next merkle batch — ONE Solana tx covers the entire batch, so "
                 "anchoring cost per attestation is ~0. Call mint_verify with this "
                 "attestation_hash to retrieve the on-chain merkle proof once anchored."),
    }
    if decision["gate"] == "paid":
        out["payment"] = {"method": "x402", "paid_usdc": decision.get("amount_usdc"),
                          "payment_tx": decision.get("payment_tx"), "payer": decision.get("payer")}
    elif decision["gate"] == "credit":
        out["payment"] = {"method": "retry_credit"}
    return out


# ── verify ────────────────────────────────────────────────────────────────────
_PENDING_NOTE = (
    "Trust score + on-chain attestation history are served by Forge's trust-read "
    "endpoint, which is rolling out next. Attestations are already permanent "
    "on-chain and will surface here once the read endpoint is wired.")


async def _verify_attestation(attestation_hash: str) -> dict:
    """Verify ONE attestation by its hash: where it sits in the anchoring pipeline
    and (once anchored) the merkle proof that lets anyone confirm its inclusion
    under the on-chain root without trusting FoundryNet."""
    row = await merkle_batch.get_attestation(attestation_hash)
    if not row:
        return {"error": "not_found",
                "detail": f"No attestation with attestation_hash={attestation_hash!r} "
                          "is known on this instance.", "verifiable": True}
    base = {
        "attestation_id": row.get("id"), "mint_id": row.get("mint_id"),
        "work_type": row.get("work_type"), "data_hash": row.get("data_hash"),
        "attestation_hash": row.get("attestation_hash"),
        "duration_seconds": row.get("duration_seconds"), "summary": row.get("summary"),
        "payment_tx": row.get("payment_tx"), "created_at": row.get("created_at"),
    }
    if row.get("status") == "anchored":
        root, proof, tx = row.get("merkle_root"), row.get("merkle_proof") or [], row.get("anchor_tx")
        return {
            **base, "status": "anchored", "anchored": True,
            "merkle_root": root, "merkle_proof": proof, "anchor_tx": tx,
            "batch_id": row.get("batch_id"), "anchored_at": row.get("anchored_at"),
            "verify_url": f"{config.SOLSCAN_TX_BASE}/{tx}" if tx else None,
            "proof_valid": merkle_batch.verify_proof(row.get("attestation_hash"), proof, root),
            "verification": "merkle-inclusion", "verifiable": True,
            "note": ("Independently verifiable: fold merkle_proof into "
                     "sha256(0x00 || attestation_hash) and confirm the result equals "
                     "merkle_root, which is written in the SPL-memo of anchor_tx on "
                     "Solana. No trust in FoundryNet required."),
        }
    return {
        **base, "status": "attested", "anchored": False, "pending_anchor": True,
        "anchor_eta": merkle_batch.next_anchor_eta(),
        "verification": "recorded", "verifiable": True,
        "note": ("Recorded and paid for, not yet anchored on-chain. It will be "
                 "included in the next merkle batch (one tx anchors the whole batch). "
                 "Re-verify with this attestation_hash to get the proof once anchored."),
    }


async def do_verify(mint_id: Optional[str] = None, actor_name: Optional[str] = None,
                    actor_type: Optional[str] = None,
                    attestation_hash: Optional[str] = None) -> dict:
    # Attestation-level verification: prove a specific unit of work is anchored.
    if attestation_hash:
        return await _verify_attestation(attestation_hash)

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

    # Trust layer live: serve the real profile (trust score, ratings,
    # recommendations, work-type breakdown) from Supabase, enriched with the
    # actor's recent attestations and their on-chain anchor status. Falls back to
    # the identity-only "pending" shape only if the trust store isn't configured.
    if supa.configured():
        prof = await trust.profile(mint_id, local)
        try:
            atts = await merkle_batch.attestations_for_mint(mint_id, limit=10)
            if atts:
                prof["recent_attestations"] = [
                    {"attestation_hash": a.get("attestation_hash"),
                     "work_type": a.get("work_type"), "status": a.get("status"),
                     "anchored": a.get("status") == "anchored",
                     "anchor_tx": a.get("anchor_tx"), "merkle_root": a.get("merkle_root"),
                     "at": a.get("created_at")}
                    for a in atts]
                prof["unanchored_attestations"] = sum(
                    1 for a in atts if a.get("status") != "anchored")
        except Exception:
            pass
        return prof

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


# ── rate ──────────────────────────────────────────────────────────────────────

async def do_rate(attestation_id: str, rated_mint_id: str, score,
                  rater_mint_id: Optional[str] = None, accuracy: bool = True,
                  would_use_again: bool = True, tags: Optional[list] = None,
                  comment: Optional[str] = None, api_key: Optional[str] = None) -> dict:
    """Record a 1–5 rating of a completed attestation and recompute the rated
    actor's trust. FREE.

    Enforced today: score range, no self-rating, one rating per (attestation,
    rater), and that the rater is bound to an identity their fnet_ key owns
    (anti-spam). NOTE: Forge attestations don't yet record a separate paying
    party, so the "rater must be the buyer of THIS attestation" check is not
    cryptographically enforced — the rater is bound to a real owned actor
    instead. The hook is here for when Forge records counterparties.
    """
    if not (attestation_id or "").strip():
        return {"error": "bad_request", "detail": "attestation_id is required"}
    if not (rated_mint_id or "").startswith("MINT-"):
        return {"error": "bad_request",
                "detail": f"rated_mint_id must look like 'MINT-xxxxxx', got {rated_mint_id!r}"}
    try:
        score = int(score)
    except (TypeError, ValueError):
        return {"error": "bad_request", "detail": "score must be an integer 1–5"}
    if not 1 <= score <= 5:
        return {"error": "bad_request", "detail": "score must be between 1 and 5"}
    if not supa.configured():
        return {"error": "not_configured", "detail": "Trust store (Supabase) is not configured."}

    resolved = await _resolve_rater(api_key, rater_mint_id)
    if "error" in resolved:
        return resolved
    rater = resolved["mint_id"]
    if rater == rated_mint_id:
        return {"error": "bad_request", "detail": "You can't rate yourself."}

    if await supa.rating_exists(attestation_id, rater):
        return {"error": "conflict",
                "detail": f"{rater} already rated attestation {attestation_id}."}

    tags = [str(t) for t in (tags or [])]
    payload = {"attestation_id": attestation_id, "rater_mint_id": rater,
               "rated_mint_id": rated_mint_id, "score": score, "accuracy": bool(accuracy),
               "would_use_again": bool(would_use_again), "tags": tags, "comment": comment or ""}
    data_hash = _data_hash(payload)
    row = {**payload, "data_hash": data_hash}

    res = await supa.insert_rating(row)
    if "error" in res:
        # unique-violation ⇒ raced with another rating
        if str(res.get("error")).endswith("409") or "duplicate" in str(res).lower():
            return {"error": "conflict",
                    "detail": f"{rater} already rated attestation {attestation_id}."}
        return {"error": "rate_failed", "detail": res}
    rating_id = (res.get("data") or [{}])[0].get("id")

    updated = await trust.recompute(rated_mint_id)
    return {
        "rating_id": rating_id, "attestation_id": attestation_id,
        "rated_mint_id": rated_mint_id, "rater_mint_id": rater, "score": score,
        "tags": tags, "data_hash": data_hash,
        "trust_score_updated": updated.get("trust_score"),
        "status": "recorded",
        "note": ("Rating recorded and the rated actor's trust score recomputed. "
                 "data_hash is the reproducible off-chain commitment."),
    }


# ── recommend ─────────────────────────────────────────────────────────────────

async def do_recommend(recommended_mint_id: str, context: str, score,
                       note: Optional[str] = None, recommender_mint_id: Optional[str] = None,
                       attestation_id: Optional[str] = None,
                       api_key: Optional[str] = None) -> dict:
    """Record a peer recommendation (1–5) for an actor in a named context and
    recompute that actor's trust. FREE.

    Enforced today: score range, no self-recommendation, one recommendation per
    (recommender, recommended, context), and recommender bound to a key-owned
    identity. NOTE: "must have worked with the actor" requires a cross-actor
    transaction record Forge doesn't expose yet, so it's not enforced here —
    documented rather than faked.
    """
    if not (recommended_mint_id or "").startswith("MINT-"):
        return {"error": "bad_request",
                "detail": f"recommended_mint_id must look like 'MINT-xxxxxx', got {recommended_mint_id!r}"}
    if not (context or "").strip():
        return {"error": "bad_request", "detail": "context is required"}
    try:
        score = int(score)
    except (TypeError, ValueError):
        return {"error": "bad_request", "detail": "score must be an integer 1–5"}
    if not 1 <= score <= 5:
        return {"error": "bad_request", "detail": "score must be between 1 and 5"}
    if not supa.configured():
        return {"error": "not_configured", "detail": "Trust store (Supabase) is not configured."}

    resolved = await _resolve_rater(api_key, recommender_mint_id)
    if "error" in resolved:
        return resolved
    recommender = resolved["mint_id"]
    if recommender == recommended_mint_id:
        return {"error": "bad_request", "detail": "You can't recommend yourself."}

    context = context.strip()
    payload = {"recommender_mint_id": recommender, "recommended_mint_id": recommended_mint_id,
               "context": context, "score": score, "note": note or "",
               "attestation_id": attestation_id}
    data_hash = _data_hash(payload)
    row = {**payload, "data_hash": data_hash}

    res = await supa.insert_recommendation(row)
    if "error" in res:
        if str(res.get("error")).endswith("409") or "duplicate" in str(res).lower():
            return {"error": "conflict",
                    "detail": (f"{recommender} already recommended {recommended_mint_id} "
                               f"for context {context!r}.")}
        return {"error": "recommend_failed", "detail": res}
    recommendation_id = (res.get("data") or [{}])[0].get("id")

    updated = await trust.recompute(recommended_mint_id)
    return {
        "recommendation_id": recommendation_id,
        "recommended_mint_id": recommended_mint_id,
        "recommender_mint_id": recommender, "context": context, "score": score,
        "data_hash": data_hash, "trust_score_updated": updated.get("trust_score"),
        "status": "recorded",
        "note": "Recommendation recorded and the recommended actor's trust recomputed.",
    }


# ── discover ──────────────────────────────────────────────────────────────────

_SORTS = {"trust_score", "recommendations", "recent"}


def _cap_match(actor: dict, q: str) -> bool:
    """Loose capability/text match: normalize spaces↔underscores, case-fold, and
    substring-match against capabilities, name, and description."""
    norm = q.lower().replace(" ", "_")
    hay = "_".join([
        *(str(c).lower() for c in (actor.get("capabilities") or [])),
        str(actor.get("name") or "").lower().replace(" ", "_"),
        str(actor.get("description") or "").lower().replace(" ", "_"),
    ])
    return norm in hay or q.lower() in (actor.get("description") or "").lower()


async def do_discover(capability: Optional[str] = None, actor_type: Optional[str] = None,
                      min_trust_score: float = 0, min_recommendations: int = 0,
                      sort_by: str = "trust_score", limit: int = 10) -> dict:
    """Trust-ranked search of the actor directory. FREE, no auth."""
    if not supa.configured():
        return {"error": "not_configured", "detail": "Discovery store (Supabase) is not configured."}
    sort_by = sort_by if sort_by in _SORTS else "trust_score"
    try:
        limit = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        limit = 10
    try:
        min_trust_score = float(min_trust_score or 0)
    except (TypeError, ValueError):
        min_trust_score = 0.0
    try:
        min_recommendations = int(min_recommendations or 0)
    except (TypeError, ValueError):
        min_recommendations = 0

    pool = await supa.actor_pool(actor_type)
    if capability:
        pool = [a for a in pool if _cap_match(a, capability)]
    ids = [a["mint_id"] for a in pool if a.get("mint_id")]
    trust_by_id = await supa.trust_for_ids(ids)
    recs_by_id = await supa.recommendations_for_ids(ids)

    results = []
    for a in pool:
        mid = a.get("mint_id")
        t = trust_by_id.get(mid) or {}
        recs = recs_by_id.get(mid) or []
        tscore = float(t.get("trust_score") or 50)
        n_recs = len(recs)
        if tscore < min_trust_score or n_recs < min_recommendations:
            continue
        results.append({
            "mint_id": mid, "name": a.get("name"), "actor_type": a.get("actor_type"),
            "trust_score": tscore,
            "total_attestations": t.get("total_attestations", 0),
            "avg_rating": t.get("avg_rating", 0),
            "total_ratings": t.get("total_ratings", 0),
            "recommendations": n_recs,
            "capabilities": a.get("capabilities") or [],
            "mcp_endpoint": a.get("mcp_endpoint"),
            "description": a.get("description"),
            "last_active": t.get("last_active") or a.get("last_active"),
            "registered_at": a.get("registered_at"),
            "top_recommendations": [
                {"from": r.get("recommender_mint_id"), "context": r.get("context"),
                 "score": r.get("score"), "note": r.get("note")}
                for r in recs[:3]
            ],
        })

    if sort_by == "recommendations":
        results.sort(key=lambda r: (r["recommendations"], r["trust_score"]), reverse=True)
    elif sort_by == "recent":
        results.sort(key=lambda r: (r.get("last_active") or r.get("registered_at") or ""), reverse=True)
    else:
        results.sort(key=lambda r: r["trust_score"], reverse=True)

    total = len(results)
    return {
        "results": results[:limit], "total_matches": total,
        "query": {"capability": capability, "actor_type": actor_type,
                  "min_trust_score": min_trust_score, "min_recommendations": min_recommendations,
                  "sort_by": sort_by, "limit": limit},
    }
