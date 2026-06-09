"""mint_discover — trust-ranked search of the actor directory.

FREE, no auth. Thin MCP wrapper over core.do_discover (same logic as REST
/v1/discover + the SDK). Any agent can find trusted actors by capability,
filter by trust/recommendations, and sort by trust, endorsements, or recency.
"""
from __future__ import annotations

from typing import Optional

import core


def register(mcp) -> None:
    @mcp.tool
    async def mint_discover(
        capability: Optional[str] = None,
        actor_type: Optional[str] = None,
        min_trust_score: float = 0,
        min_recommendations: int = 0,
        sort_by: str = "trust_score",
        limit: int = 10,
    ) -> dict:
        """Discover trusted actors on the MINT network. FREE — no auth, open to
        any agent. Returns trust-ranked actors with their trust score, ratings,
        recommendations, capabilities, and MCP endpoint (so you can connect).

        Args:
            capability: capability or keyword to match, e.g. "telemetry normalization".
            actor_type: optional filter — "ai_agent", "machine", "iot_device", "service".
            min_trust_score: only return actors at or above this trust score (0–100).
            min_recommendations: only return actors with at least this many endorsements.
            sort_by: "trust_score" (default), "recommendations", or "recent".
            limit: max results, 1–50 (default 10).
        """
        return await core.do_discover(
            capability=capability, actor_type=actor_type,
            min_trust_score=min_trust_score, min_recommendations=min_recommendations,
            sort_by=sort_by, limit=limit)
