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
    ) -> dict:
        """Look up an actor's trust profile: identity, trust score, total verified
        attestations, work-type breakdown, and recent on-chain attestations.
        FREE — reputation queries are never gated.

        Provide EITHER mint_id directly, OR actor_name (optionally actor_type).
        NOTE: trust-read is rolling out; until it lands trust_score /
        total_attestations come back as "pending" rather than fabricated.

        Args:
            mint_id: the actor's MINT id ("MINT-xxxxxx").
            actor_name: the actor's registered name, e.g. "ResearchBot-7".
            actor_type: optional disambiguator when resolving by name.
        """
        return await core.do_verify(mint_id, actor_name, actor_type)
