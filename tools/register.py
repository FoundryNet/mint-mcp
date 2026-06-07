"""mint_register — give any autonomous actor a persistent cryptographic identity.

FREE. Thin MCP wrapper over core.do_register (the same logic the REST /v1/register
surface and the mint-attest SDK use). Maps the actor onto Forge's (oem, model,
serial) identity triple: oem=actor_type, model=name, serial=uuid5(type,name,operator).
"""
from __future__ import annotations

from typing import Optional

import core


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
        returns the SAME mint_id. Always call this before mint_attest for a new
        actor. FREE — identity is never gated.

        Args:
            actor_type: one of "ai_agent", "machine", "iot_device", "service".
            name: human-readable actor name, e.g. "ResearchBot-7".
            capabilities: optional capability tags, e.g. ["web_research"].
            operator: optional owning company/operator name (scopes the identity).
            metadata: optional free-form JSON attached to the identity.
        """
        return await core.do_register(actor_type, name, capabilities, operator, metadata)
