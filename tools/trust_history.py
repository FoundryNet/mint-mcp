"""mint_trust_history — full attestation audit trail for an agent. PAID ($0.25).

Thin MCP wrapper over core.do_trust_history, gated by read_gate.
"""
from __future__ import annotations

from typing import Optional

import core
import read_gate


def register(mcp) -> None:
    @mcp.tool
    async def mint_trust_history(agent_id: str, days: int = 30,
                                 payment_tx: Optional[str] = None) -> dict:
        """Full attestation history for an agent over the last `days`. PAID: $0.25 USDC.

        Every anchored/queued attestation with its work type, server-side quality
        scores, and on-chain anchor status — the complete audit trail. Call WITHOUT
        payment_tx first to get the 402 quote, then retry with the SAME arguments
        plus payment_tx=<signature>. An fnet_ Bearer key on REST bypasses.

        Args:
            agent_id: the agent's MINT id ("MINT-xxxxxx").
            days: lookback window in days (1-365, default 30).
            payment_tx: Solana signature of the $0.25 USDC payment (second call).
        """
        decision = await read_gate.precheck(
            "mint_trust_history", {"agent_id": agent_id, "days": days},
            payment_tx, api_key=None)
        if decision["gate"] == "blocked":
            return decision["body"]
        result = await core.do_trust_history(agent_id, days=days)
        note = read_gate.billing_note(decision)
        if note and isinstance(result, dict) and "error" not in result:
            result["billing"] = note
        return result
