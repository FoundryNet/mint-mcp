"""mint_verify — query an actor's identity and (soon) trust + work history.

FREE. Thin MCP wrapper over core.do_verify. Trust-read is rolling out via Forge;
until then this returns identity + registration with trust_score/total_attestations
as "pending" (not faked). mint_attest is fully live.
"""
from __future__ import annotations

from typing import Optional

import core


def register(mcp) -> None:
    @mcp.tool
    async def mint_verify(
        mint_id: Optional[str] = None,
        actor_name: Optional[str] = None,
        actor_type: Optional[str] = None,
        attestation_hash: Optional[str] = None,
    ) -> dict:
        """Verify an actor's reputation OR a single attestation's on-chain anchoring.
        FREE — verification is never gated.

        Two modes:
          • Pass `attestation_hash` to verify ONE attestation. If it's anchored you
            get back merkle_root + merkle_proof + anchor_tx (with a Solscan link) —
            fold the proof into sha256(0x00||attestation_hash) and confirm it equals
            the root in the tx memo to prove inclusion yourself, no trust required.
            If it's recorded but not yet anchored you get anchored=false,
            pending_anchor=true and an anchor_eta.
          • Pass `mint_id` (or `actor_name` [+ `actor_type`]) to get the actor's full
            trust profile: trust score, total verified attestations, work-type
            breakdown, recent ratings/recommendations, and recent attestations with
            their anchor status.

        Args:
            mint_id: the actor's MINT id ("MINT-xxxxxx").
            actor_name: the actor's registered name, e.g. "ResearchBot-7".
            actor_type: optional disambiguator when resolving by name.
            attestation_hash: the sha256 attestation handle returned by mint_attest;
                verifies that specific attestation's anchoring + merkle proof.
        """
        return await core.do_verify(mint_id, actor_name, actor_type,
                                    attestation_hash=attestation_hash)
