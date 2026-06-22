"""mint_settle_cell — evaluate (optional) + settle a FoundryNet work cell.

Submits `settle_cell` (optionally preceded by `evaluate_cell` when scores are
supplied), distributing the reward pool 96/2/2, returning stakes, and updating
each participant's on-chain trust score. The signer must be the cell creator.
"""
from __future__ import annotations

from typing import List, Optional

import foundrynet


def register(mcp) -> None:
    @mcp.tool
    async def mint_settle_cell(
        cell_id: str,
        participants: List[str],
        scores: Optional[List[int]] = None,
    ) -> dict:
        """Trigger **settlement** of a work cell.

        Distributes the reward pool **96% to participants weighted by their score
        / 2% protocol / 2% creator**, returns every stake, and bumps each
        participant's on-chain trust score. The configured signer must be the
        cell's creator.

        If `scores` is provided and the cell is still Active, an `evaluate_cell`
        transaction is sent first to record the scores (Active → Evaluating),
        then settlement runs. If the cell was already evaluated, omit `scores`.

        Args:
            cell_id: the cell to settle.
            participants: base58 pubkeys of every participant, in the order their
                scores apply (used to rebuild the participant/token/trust accounts).
            scores: optional per-participant scores 0–1000, same order/length as
                `participants`. Omit if the cell is already in the Evaluating state.

        Returns the settle tx signature (+ evaluate tx if run) and an explorer link.
        Inert (status="not_configured") until FOUNDRY_CELL_WALLET + FOUNDRY_STAKE_MINT
        are set.
        """
        return await foundrynet.settle_cell(cell_id, participants, scores)
