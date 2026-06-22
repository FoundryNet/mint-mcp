"""mint_settle_policy — settle a FoundryNet parametric insurance policy.

Submits `settle_policy`: pays the beneficiary if the policy was triggered, or
returns the coverage escrow to the insurer if it expired untriggered. A
settlement event is emitted for MINT attestation.
"""
from __future__ import annotations

import foundrynet


def register(mcp) -> None:
    @mcp.tool
    async def mint_settle_policy(policy_id: str, beneficiary: str) -> dict:
        """Trigger **settlement** of a parametric insurance policy.

        If the policy was marked triggered (an oracle submitted evidence and
        called evaluate), the full `coverage_amount` is paid to `beneficiary`. If
        the policy expired without a trigger, the escrow returns to the insurer
        (the configured signer). Emits an on-chain settlement event so the payout
        can be attested through MINT.

        Args:
            policy_id: the policy to settle.
            beneficiary: base58 pubkey of the payout beneficiary (its token account
                receives the coverage on a triggered settlement).

        Returns the tx signature and an explorer link. Inert
        (status="not_configured") until FOUNDRY_CELL_WALLET + FOUNDRY_STAKE_MINT
        are set.
        """
        return await foundrynet.settle_policy(policy_id, beneficiary)
