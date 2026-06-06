"""mint_register — give any autonomous actor a persistent cryptographic identity.

FREE. Registration builds the network; identity is never gated. Maps the actor
onto Forge's (oem, model, serial) identity triple:
    oem    = actor_type   ("ai_agent" | "machine" | "iot_device" | "service")
    model  = name         ("ResearchBot-7")
    serial = stable uuid  derived from (actor_type, name[, operator]) so the same
             logical actor is idempotent across calls, exactly like a machine
             serial. Two different operators can both run a "ResearchBot-7"
             without colliding.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Optional

import actor_registry
import forge_client

VALID_ACTOR_TYPES = {"ai_agent", "machine", "iot_device", "service"}

# Stable namespace so the derived serial is reproducible for the same logical
# actor (idempotent registration) but distinct per operator.
_SERIAL_NS = uuid.UUID("4d494e54-0000-4000-8000-000000000001")  # "MINT" prefix


def _derive_serial(actor_type: str, name: str, operator: Optional[str]) -> str:
    seed = f"{actor_type}|{name}|{operator or ''}".lower()
    return uuid.uuid5(_SERIAL_NS, seed).hex


def register(mcp) -> None:
    @mcp.tool
    async def mint_register(
        actor_type: str,
        name: str,
        capabilities: Optional[list] = None,
        operator: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Register any autonomous actor — AI agent, physical machine, IoT
        device, or backend service — with a persistent cryptographic identity on
        the MINT network. Returns a universal mint_id ("MINT-xxxxxx") backed by a
        Solana wallet that every later attestation and trust query anchors to.

        Idempotent: registering the same (actor_type, name, operator) again
        returns the SAME mint_id with `registered` reflecting whether it already
        existed. Always call this before mint_attest for a new actor.

        FREE — identity is never gated; registration is what grows the network.

        Args:
            actor_type: one of "ai_agent", "machine", "iot_device", "service".
            name: human-readable actor name, e.g. "ResearchBot-7".
            capabilities: optional list of capability tags, e.g.
                ["web_research", "report_generation"].
            operator: optional owning company/operator name. Scopes the identity
                so two operators can run identically-named actors without
                colliding.
            metadata: optional free-form JSON attached to the identity.
        """
        atype = (actor_type or "").strip().lower()
        if atype not in VALID_ACTOR_TYPES:
            return {
                "error": "bad_request",
                "detail": f"actor_type must be one of {sorted(VALID_ACTOR_TYPES)}, got {actor_type!r}",
            }
        if not (name or "").strip():
            return {"error": "bad_request", "detail": "name is required"}
        if not forge_client.configured():
            return {"error": "not_configured",
                    "detail": "FORGE_API_KEY is not set on this server; cannot provision identity."}

        serial = _derive_serial(atype, name, operator)
        meta = dict(metadata or {})
        # Carry the semantic identity into Forge metadata so it's recoverable
        # off-instance even though the relay doesn't model actor types.
        meta.update({
            "mint_actor_type":   atype,
            "mint_actor_name":   name,
            "mint_capabilities": capabilities or [],
        })
        if operator:
            meta["mint_operator"] = operator

        resp = await forge_client.identify(
            oem=atype, model=name, serial=serial,
            site=operator, metadata=meta,
        )
        if "error" in resp:
            return resp

        mint_id   = resp.get("mint_id")
        created   = resp.get("created")
        first_seen = resp.get("first_seen")
        machine   = resp.get("machine") or {}

        if mint_id:
            actor_registry.remember(
                mint_id, actor_type=atype, name=name,
                capabilities=capabilities, operator=operator,
            )

        return {
            "mint_id":      mint_id,
            "actor_type":   atype,
            "name":         name,
            "capabilities": capabilities or [],
            "operator":     operator,
            "registered":   True,
            "newly_registered": bool(created),
            "first_seen":   first_seen,
            "wallet_address": machine.get("wallet_address"),
            "status":       machine.get("status", "active"),
            "note": (
                "Identity is persistent and on-chain. Use this mint_id for "
                "mint_attest (prove work) and mint_verify (query trust)."
            ),
        }
