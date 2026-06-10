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
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Attest a completed unit of work for a registered actor, anchoring a
        tamper-evident record on Solana mainnet and updating the actor's trust.

        PRICING: 2¢ USDC per attestation. Call this WITHOUT payment_tx first; if
        payment is required you get back {"status": 402, "payment_required": {...}}
        telling you the amount, recipient, and `memo` to put on a Solana USDC
        transfer. Make that payment, then call again with the SAME arguments plus
        payment_tx=<the transaction signature>.

        On success you get attestation_id, data_hash, and attestation_hash, with
        anchored=false + an anchor_eta: the attestation is recorded and paid
        immediately, then anchored on-chain in the next merkle batch — a SINGLE
        Solana tx anchors the whole batch, so on-chain cost per attestation is ~0.
        To get the on-chain proof, call mint_verify with the returned
        attestation_hash once it's anchored (it returns merkle_root + merkle_proof +
        anchor_tx, independently verifiable). Surface the attestation_hash so the
        work can be verified later.

        Args:
            mint_id: the actor's MINT id from mint_register ("MINT-xxxxxx").
            work_type: code_review|normalization|research|generation|analysis|
                delivery|manufacturing|custom.
            duration_seconds: wall-clock seconds the work took (> 0).
            summary: short human description of what was done and the result.
            input_hash: optional sha256 of the work's input.
            output_hash: optional sha256 of the work's output.
            metadata: optional free-form JSON folded into the hashed payload.
            payment_tx: Solana signature of the USDC payment for this attestation
                (the second call). Omit it on the first call to receive the 402
                payment instructions.
        """
        return await core.do_attest(mint_id, work_type, duration_seconds,
                                    summary=summary, input_hash=input_hash,
                                    output_hash=output_hash, metadata=metadata,
                                    payment_tx=payment_tx)
