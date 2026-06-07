"""mint_attest — attest that a unit of work was completed.

Thin MCP wrapper over core.do_attest (the same logic the REST /v1/attest surface
and the mint-attest SDK use). Maps work_type → settlement complexity and posts to
Forge /v1/attest, which settles against the actor's REAL mint_id (trust + earnings
+ on-chain history) and returns the receipt. mint-mcp never touches the relay.

PRICING: 2¢ per attestation. The x402 gate (server.py) is INERT unless X402_ENABLED.
"""
from __future__ import annotations

from typing import Optional

import core


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

        Returns attestation_id, data_hash (off-chain proof), tx_signature with a
        Solscan verify_url, the new trust_score, and the reward minted. Always
        surface the verify_url so the caller can confirm on-chain. PRICING: 2¢.

        Args:
            mint_id: the actor's MINT id from mint_register ("MINT-xxxxxx").
            work_type: code_review|normalization|research|generation|analysis|
                delivery|manufacturing|custom.
            duration_seconds: wall-clock seconds the work took (> 0).
            summary: short human description of what was done and the result.
            input_hash: optional sha256 of the work's input.
            output_hash: optional sha256 of the work's output.
            metadata: optional free-form JSON folded into the hashed payload.
        """
        return await core.do_attest(mint_id, work_type, duration_seconds,
                                    summary=summary, input_hash=input_hash,
                                    output_hash=output_hash, metadata=metadata)
