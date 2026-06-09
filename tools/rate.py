"""mint_rate — post-work feedback on a completed attestation.

FREE. Thin MCP wrapper over core.do_rate (same logic as REST /v1/rate + the SDK).
A rating is bound to an identity the caller's key owns, can't target yourself,
and is unique per (attestation, rater); recording one recomputes the rated
actor's trust score. Over MCP the server's FORGE_API_KEY identifies the rater;
over REST the caller's fnet_ Bearer key does.
"""
from __future__ import annotations

from typing import Optional

import config
import core


def register(mcp) -> None:
    @mcp.tool
    async def mint_rate(
        attestation_id: str,
        rated_mint_id: str,
        score: int,
        rater_mint_id: Optional[str] = None,
        accuracy: bool = True,
        would_use_again: bool = True,
        tags: Optional[list] = None,
        comment: Optional[str] = None,
    ) -> dict:
        """Rate a completed unit of work (an attestation) 1–5 and update the
        rated actor's trust score. FREE.

        Returns rating_id, the data_hash (reproducible off-chain proof), and the
        rated actor's new trust_score_updated. You can't rate yourself, and each
        rater may rate a given attestation once.

        Args:
            attestation_id: the attestation being rated (from mint_attest).
            rated_mint_id: the actor that did the work ("MINT-xxxxxx").
            score: integer 1–5.
            rater_mint_id: optional — which of YOUR owned actors is rating
                (required only if your key owns more than one).
            accuracy: whether the work was accurate (default true).
            would_use_again: whether you'd use this actor again (default true).
            tags: optional descriptors, e.g. ["fast", "thorough"].
            comment: optional free-text comment.
        """
        return await core.do_rate(
            attestation_id, rated_mint_id, score, rater_mint_id=rater_mint_id,
            accuracy=accuracy, would_use_again=would_use_again, tags=tags,
            comment=comment, api_key=config.FORGE_API_KEY)
