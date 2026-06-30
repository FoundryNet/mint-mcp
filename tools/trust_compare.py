"""mint_trust_compare — rank multiple agents by trust score. PAID ($0.05).

Thin MCP wrapper over core.do_trust_compare, gated by read_gate.
"""
from __future__ import annotations

from typing import List, Optional

import core
import read_gate


def register(mcp) -> None:
    @mcp.tool
    async def mint_trust_compare(agent_ids: List[str],
                                 payment_tx: Optional[str] = None) -> dict:
        """Compare trust scores across multiple agents — a head-to-head leaderboard.
        PAID: $0.05 USDC.

        Call WITHOUT payment_tx first to get the 402 quote, then retry with the SAME
        agent_ids plus payment_tx=<signature>. An fnet_ Bearer key on REST bypasses.

        Args:
            agent_ids: list of MINT ids to rank (2-25, e.g. ["MINT-aaa", "MINT-bbb"]).
            payment_tx: Solana signature of the $0.05 USDC payment (second call).
        """
        decision = await read_gate.precheck(
            "mint_trust_compare", {"agent_ids": agent_ids}, payment_tx, api_key=None)
        if decision["gate"] == "blocked":
            return decision["body"]
        result = await core.do_trust_compare(agent_ids)
        note = read_gate.billing_note(decision)
        if note and isinstance(result, dict) and "error" not in result:
            result["billing"] = note
        return result
