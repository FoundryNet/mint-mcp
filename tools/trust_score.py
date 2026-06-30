"""mint_trust_score — agent reputation lookup. PAID ($0.01).

Thin MCP wrapper over core.do_trust_score, gated by read_gate. Returns the trust
score + headline counts for one MINT identity, computed from the same engine that
backs mint_verify. Call without payment_tx first to receive the 402 quote.
"""
from __future__ import annotations

from typing import Optional

import core
import read_gate


def register(mcp) -> None:
    @mcp.tool
    async def mint_trust_score(agent_id: str, payment_tx: Optional[str] = None) -> dict:
        """Look up an agent's trust score from the trust graph. PAID: $0.01 USDC.

        Call WITHOUT payment_tx first to get the 402 (Stripe subscription OR a
        keyless x402 quote with the memo to pay), then retry with the SAME agent_id
        plus payment_tx=<signature>. An fnet_ Bearer key on the REST surface bypasses.

        Args:
            agent_id: the agent's MINT id ("MINT-xxxxxx").
            payment_tx: Solana signature of the $0.01 USDC payment (second call).
        """
        decision = await read_gate.precheck(
            "mint_trust_score", {"agent_id": agent_id}, payment_tx, api_key=None)
        if decision["gate"] == "blocked":
            return decision["body"]
        result = await core.do_trust_score(agent_id)
        note = read_gate.billing_note(decision)
        if note and isinstance(result, dict) and "error" not in result:
            result["billing"] = note
        return result
