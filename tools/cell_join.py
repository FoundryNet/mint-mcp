"""mint_join_cell — join a FoundryNet work cell by staking.

Builds + submits a `join_cell` transaction: transfers the cell's required stake
from the signer's token account into escrow, opens the signer's participant
record + trust account, and activates the cell once it is full.
"""
from __future__ import annotations

import foundrynet


def register(mcp) -> None:
    @mcp.tool
    async def mint_join_cell(cell_id: str) -> dict:
        """Join an open **work cell** by locking its required stake.

        Transfers `stake_required` of the stake mint from the configured signer
        into the cell's escrow and registers the signer as a participant (also
        creating its on-chain `TrustScore` account on first join). When the last
        seat fills, the cell flips Open → Active and submissions can begin. Fails
        if the cell is full or already active.

        Args:
            cell_id: the id of the cell to join (the same id used at creation).

        Returns the tx signature, an explorer link, and the participant + trust
        PDAs. Inert (status="not_configured") until FOUNDRY_CELL_WALLET +
        FOUNDRY_STAKE_MINT are set.
        """
        return await foundrynet.join_cell(cell_id)
