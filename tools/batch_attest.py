"""mint_batch_attest — anchor many units of work in one call. FREE.

Thin MCP wrapper over core.do_batch_attest. Attestation is the distribution
channel — free and unlimited. Each item attests exactly like mint_attest and
drains into the next merkle batch, so a whole batch settles in one on-chain tx.
"""
from __future__ import annotations

from typing import List

import core


def register(mcp) -> None:
    @mcp.tool
    async def mint_batch_attest(attestations: List[dict]) -> dict:
        """Attest a batch of completed work items at once. FREE, unlimited.

        Each item is an object with the SAME fields mint_attest takes:
          {"mint_id": "MINT-xxx", "work_type": "analysis", "duration_seconds": 12,
           "summary": "...", "input_hash"?: "...", "output_hash"?: "...",
           "metadata"?: {...}}
        Per-item results (and any per-item errors) are returned inline; a single bad
        item never blocks the rest. Surface each returned attestation_hash so the
        work can be verified later via mint_verify.

        Args:
            attestations: list of attestation objects (1-100 per call).
        """
        return await core.do_batch_attest(attestations)
