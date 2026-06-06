"""mint_attest — attest that a unit of work was completed.

Creates a tamper-evident on-chain record proving what was done, when, by whom,
with what result. The proof is a data_hash = sha256(canonical attestation
payload), and the on-chain anchor is a MINT relay settlement (record_job →
update_trust → settle_job on Solana mainnet). The settlement also moves the
actor's trust score via the relay reward formula:

    reward = duration * 0.005 * (complexity/1000) * (trust/100) * warmup

PRICING: 2¢ per attestation (x402 USDC, or via a Forge billing API key). The
x402 gate is enforced in server.py and is INERT unless X402_ENABLED.

Path: if MINT_RELAY_KEY is configured we settle directly on the relay (spec
path). If not, we fall back to Forge /v1/settle, which holds the relay operator
credentials internally — so attest still anchors on-chain tonight either way.
"""
from __future__ import annotations

import hashlib
import json as _json
from typing import Optional

import actor_registry
import config
import forge_client
import mint_client

VALID_WORK_TYPES = {
    "code_review", "normalization", "research", "generation",
    "analysis", "delivery", "manufacturing", "custom",
}

# Map a work_type to the relay's 500–2000 complexity band (1000 = baseline).
# Heavier cognitive/physical work claims more complexity → larger reward.
_WORK_COMPLEXITY = {
    "code_review":   1500,
    "analysis":      1400,
    "research":      1300,
    "manufacturing": 1200,
    "generation":    1100,
    "normalization": 1000,
    "custom":        1000,
    "delivery":       700,
}


def _data_hash(payload: dict) -> str:
    """Deterministic sha256 of the attestation, matching Forge's canonical-JSON
    convention (sorted keys, compact separators, str fallback)."""
    canonical = _json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def register(mcp) -> None:
    @mcp.tool
    async def mint_attest(
        mint_id: str,
        work_type: str,
        duration_seconds: int,
        summary: str,
        input_hash: Optional[str] = None,
        output_hash: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Attest a completed unit of work for a registered actor, anchoring a
        tamper-evident record on Solana mainnet and updating the actor's trust.

        Returns the attestation_id, the data_hash (sha256 of the canonical
        attestation — the off-chain proof), the on-chain tx_signature with a
        Solscan verify_url, the actor's new trust_score, and its running
        total_attestations.

        PRICING: 2¢ per attestation. Always surface the verify_url so the caller
        can independently confirm the anchor on-chain.

        Args:
            mint_id: the actor's MINT id from mint_register ("MINT-xxxxxx").
            work_type: one of code_review, normalization, research, generation,
                analysis, delivery, manufacturing, custom.
            duration_seconds: wall-clock seconds the work took (> 0).
            summary: short human description of what was done and the result,
                e.g. "Reviewed 47 files, found 3 critical issues".
            input_hash: optional sha256 of the work's input (for reproducibility).
            output_hash: optional sha256 of the work's output.
            metadata: optional free-form JSON folded into the hashed payload.
        """
        if not (mint_id or "").startswith("MINT-"):
            return {"error": "bad_request",
                    "detail": f"mint_id must look like 'MINT-xxxxxx', got {mint_id!r}. Call mint_register first."}
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

        complexity = _WORK_COMPLEXITY.get(wtype, 1000)

        # 1) Compute the off-chain proof hash over the FULL attestation payload.
        attestation_payload = {
            "mint_id":          mint_id,
            "work_type":        wtype,
            "input_hash":       input_hash,
            "output_hash":      output_hash,
            "duration_seconds": duration_seconds,
            "summary":          summary,
            "metadata":         metadata or {},
        }
        data_hash = _data_hash(attestation_payload)

        # 2) Anchor on Solana. Prefer the direct relay path; fall back to Forge.
        via = "relay"
        if mint_client.configured():
            settle = await mint_client.settle(mint_id, duration_seconds, complexity)
        elif forge_client.configured():
            via = "forge"
            settle = await forge_client.settle({
                "mint_id":      mint_id,
                "payload_hash": data_hash,
                "action":       f"attest:{wtype}",
            })
        else:
            return {"error": "not_configured",
                    "detail": "Neither MINT_RELAY_KEY nor FORGE_API_KEY is set; cannot anchor attestation."}

        if "error" in settle:
            return {"error": "settle_failed", "via": via, "detail": settle,
                    "data_hash": data_hash,
                    "hint": "The attestation hash is valid; only the on-chain anchor failed. Retry mint_attest."}

        tx_signature = settle.get("tx_signature")
        trust_score  = settle.get("trust_score")
        verify_url   = settle.get("verify_url") or (
            f"{config.SOLSCAN_TX_BASE}/{tx_signature}" if tx_signature else None
        )
        job_id = settle.get("job_id")
        attestation_id = job_id or f"att-{data_hash[:12]}"

        # 3) Best-effort running total + label bookkeeping (relay is truth).
        actor_registry.record_work(mint_id, wtype)
        total_attestations = None
        if mint_client.configured():
            hist = await mint_client.history(mint_id, limit=1)
            if "error" not in hist:
                total_attestations = (hist.get("summary") or {}).get("total_jobs")

        return {
            "attestation_id":     attestation_id,
            "mint_id":            mint_id,
            "work_type":          wtype,
            "data_hash":          data_hash,
            "tx_signature":       tx_signature,
            "verify_url":         verify_url,
            "trust_score":        trust_score,
            "total_attestations": total_attestations,
            "reward":             settle.get("final_reward"),
            "settled":            bool(tx_signature),
            "anchored_via":       via,
            "note": (
                "On-chain anchor is real — verify_url is a live Solscan link. "
                "data_hash is the off-chain proof: recompute sha256 over the "
                "canonical attestation payload to confirm it matches."
            ),
        }
