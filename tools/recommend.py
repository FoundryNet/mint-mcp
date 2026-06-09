"""mint_recommend — peer-to-peer trust endorsement.

FREE. Thin MCP wrapper over core.do_recommend (same logic as REST /v1/recommend
+ the SDK). Bound to a key-owned identity, can't target yourself, unique per
(recommender, recommended, context); recording one recomputes the recommended
actor's trust score.
"""
from __future__ import annotations

from typing import Optional

import config
import core


def register(mcp) -> None:
    @mcp.tool
    async def mint_recommend(
        recommended_mint_id: str,
        context: str,
        score: int,
        note: Optional[str] = None,
        recommender_mint_id: Optional[str] = None,
        attestation_id: Optional[str] = None,
    ) -> dict:
        """Recommend another actor you've worked with, in a named context, 1–5.
        Updates the recommended actor's trust score. FREE.

        Returns recommendation_id, the data_hash, and the recommended actor's new
        trust_score_updated. You can't recommend yourself; each
        (you, them, context) triple is unique.

        Args:
            recommended_mint_id: the actor you're endorsing ("MINT-xxxxxx").
            context: what you're endorsing them for, e.g. "cross-oem normalization".
            score: integer 1–5.
            note: optional free-text, e.g. "Best for Fanuc + Siemens mixed fleets".
            recommender_mint_id: optional — which of YOUR owned actors is
                recommending (required only if your key owns more than one).
            attestation_id: optional attestation that backs this recommendation.
        """
        return await core.do_recommend(
            recommended_mint_id, context, score, note=note,
            recommender_mint_id=recommender_mint_id, attestation_id=attestation_id,
            api_key=config.FORGE_API_KEY)
