"""mint_verify — verify an attestation OR an actor's trust against the chain.

PAID ($0.005) as of the 2026-06-30 pivot: attestation is free (the distribution
channel), reading/verifying the trust graph is the product. Thin MCP wrapper over
core.do_verify, gated by read_gate. Call WITHOUT payment_tx first to receive the
402 (Stripe subscription OR a keyless x402 USDC quote), then retry with payment_tx.
A caller on the REST surface can instead present an fnet_ Bearer key to bypass.
"""
from __future__ import annotations

from typing import Optional

import core
import read_gate


def register(mcp) -> None:
    @mcp.tool
    async def mint_verify(
        mint_id: Optional[str] = None,
        actor_name: Optional[str] = None,
        actor_type: Optional[str] = None,
        attestation_hash: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Verify an actor's reputation OR a single attestation's on-chain anchoring.

        PAID: $0.005 USDC per query. Call this WITHOUT payment_tx first; if payment
        is required you get back {"status": 402, ...} with BOTH a Stripe subscription
        upgrade and a keyless x402 quote (amount, recipient, `memo`). Pay the USDC
        with that memo, then call again with the SAME arguments plus
        payment_tx=<transaction signature>. (Attestation — mint_attest — is free.)

        Two modes:
          • Pass `attestation_hash` to verify ONE attestation. If it's anchored you
            get merkle_root + merkle_proof + anchor_tx (Solscan link) — confirm
            inclusion under the on-chain root yourself, no trust required.
          • Pass `mint_id` (or `actor_name` [+ `actor_type`]) to get the actor's full
            trust profile: score, total verified attestations, work-type breakdown,
            recent ratings/recommendations, and recent attestations with anchor status.

        Args:
            mint_id: the actor's MINT id ("MINT-xxxxxx").
            actor_name: the actor's registered name, e.g. "ResearchBot-7".
            actor_type: optional disambiguator when resolving by name.
            attestation_hash: the sha256 attestation handle from mint_attest.
            payment_tx: Solana signature of the $0.005 USDC payment (second call).
        """
        args = {"mint_id": mint_id, "actor_name": actor_name,
                "actor_type": actor_type, "attestation_hash": attestation_hash}
        decision = await read_gate.precheck("mint_verify", args, payment_tx, api_key=None)
        if decision["gate"] == "blocked":
            return decision["body"]
        result = await core.do_verify(mint_id, actor_name, actor_type,
                                      attestation_hash=attestation_hash)
        note = read_gate.billing_note(decision)
        if note and isinstance(result, dict) and "error" not in result:
            result["billing"] = note
        return result
