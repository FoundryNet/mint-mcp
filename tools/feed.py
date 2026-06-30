"""mint_feed — live network attestation feed. FREE (discovery).

Thin MCP wrapper over core.do_feed (the same data as GET /v1/feed). Never gated:
the feed is the public showcase that makes the trust graph legible and draws new
agents in — discovery, not a paid read.
"""
from __future__ import annotations

import core


def register(mcp) -> None:
    @mcp.tool
    async def mint_feed(limit: int = 50) -> dict:
        """The newest attestations across the whole MINT network — originating
        agent, summary, trust score, ML confidence, anchor status, merkle root +
        Solscan link — plus showcase stats. FREE.

        Args:
            limit: how many recent attestations to return (1-200, default 50).
        """
        return await core.do_feed(limit)
