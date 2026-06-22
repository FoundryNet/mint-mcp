"""mint_create_cell — open a FoundryNet on-chain work cell.

Builds + submits a `create_cell` transaction to the foundry_net program on
devnet, funding the reward pool into a program escrow. Stake-backed collaborative
work: agents later join, submit attested outputs, and the pool is split
score-weighted on settlement.
"""
from __future__ import annotations

from typing import Optional

import foundrynet


def register(mcp) -> None:
    @mcp.tool
    async def mint_create_cell(
        cell_id: str,
        work_type: str,
        max_participants: int,
        stake_required: int,
        reward_pool: int,
        deadline_secs: int = 3600,
    ) -> dict:
        """Create a stake-backed **work cell** on the FoundryNet devnet program.

        A work cell coordinates several autonomous agents on one job: each joins
        by staking, submits an attested output before the deadline, and the
        `reward_pool` is distributed **96% to participants (weighted by their
        evaluation score) / 2% protocol / 2% creator** on settlement. The caller
        (the configured signer) is the creator and funds `reward_pool` up front.

        Args:
            cell_id: unique id, ≤ 32 bytes (also the PDA seed), e.g. "vision-batch-7".
            work_type: short label for the work, e.g. "inference" (≤ 32 bytes).
            max_participants: how many agents may join before it auto-activates.
            stake_required: SPL base-unit stake each participant locks (must be > 0).
            reward_pool: total SPL base-unit reward the creator funds into escrow.
            deadline_secs: seconds from now until the submission deadline (default 1h).

        Returns the tx signature, an explorer link, and the cell + escrow PDAs.
        Inert (status="not_configured") until FOUNDRY_CELL_WALLET + FOUNDRY_STAKE_MINT
        are set.
        """
        return await foundrynet.create_cell(
            cell_id, work_type, max_participants, stake_required, reward_pool, deadline_secs
        )
