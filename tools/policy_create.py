"""mint_create_policy — open a FoundryNet parametric insurance policy.

Builds + submits `create_policy`, funding the coverage escrow. The on-chain
settlement primitive behind the TimesFM predict_breach layer: a payout fires when
an oracle attests the trigger field crossed its threshold for the required
duration.
"""
from __future__ import annotations

from typing import Optional

import foundrynet


def register(mcp) -> None:
    @mcp.tool
    async def mint_create_policy(
        policy_id: str,
        trigger_field: str,
        trigger_threshold: int,
        coverage_amount: int,
        premium_amount: int,
        policy_duration_secs: int,
        beneficiary: str,
        trigger_direction: int = 0,
        trigger_duration_secs: int = 60,
        machine: Optional[str] = None,
    ) -> dict:
        """Create a **parametric insurance policy** on the FoundryNet devnet program.

        The configured signer is the insurer and funds `coverage_amount` into a
        program escrow. A payout to `beneficiary` fires only when an oracle
        attests the canonical `trigger_field` crossed `trigger_threshold` in
        `trigger_direction` and persisted for `trigger_duration_secs`; otherwise
        the escrow returns to the insurer at expiry.

        Args:
            policy_id: unique id, ≤ 32 bytes (PDA seed), e.g. "spindle-cnc-12".
            trigger_field: canonical field name, e.g. "spindle_load_pct".
            trigger_threshold: scaled threshold, e.g. 9500 for 95.00%.
            coverage_amount: SPL base-unit payout the insurer escrows.
            premium_amount: SPL base-unit monthly premium the operator pays.
            policy_duration_secs: policy length in seconds.
            beneficiary: base58 pubkey that receives the payout (the machine owner).
            trigger_direction: 0 = above the threshold, 1 = below (default 0).
            trigger_duration_secs: how long the condition must persist (default 60).
            machine: optional registered machine pubkey (defaults to the signer).

        Returns the tx signature, an explorer link, and the policy + escrow PDAs.
        Inert (status="not_configured") until FOUNDRY_CELL_WALLET + FOUNDRY_STAKE_MINT
        are set.
        """
        return await foundrynet.create_policy(
            policy_id,
            trigger_field,
            trigger_threshold,
            trigger_direction,
            trigger_duration_secs,
            coverage_amount,
            premium_amount,
            policy_duration_secs,
            beneficiary,
            machine,
        )
