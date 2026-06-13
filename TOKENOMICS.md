# MINT Protocol — Economic Model

MINT runs on a **two-layer economic model**, deliberately sequenced. Layer 1 is the
live business: agents pay real money (USDC) for attestations. Layer 2 is a token
utility layer that exists on-chain but is **intentionally dormant** until the network
has the attestation volume to justify activating it.

This document is explicit about what is live and what is not. The token is not
hidden — it is **sequenced**. Revenue comes first; token utility activates only when
there is a real network for it to coordinate.

---

## Layer 1 — Attestation Revenue (Live)

This is the core business model, **live on Solana mainnet today**.

- **Agents pay `0.02 USDC` per attestation**, enforced by the x402 payment gate
  (`mint_attest`). Identity, verification, rating, recommendation, and discovery
  are free; revenue comes from attestation volume.
- **Revenue is collected in USDC** — a stablecoin. There is **no token dependency**:
  the product earns and settles in USDC, not in MINT.
- **Attestations are anchored via merkle batching.** Each attestation is recorded
  off-chain, batched, and a single Solana transaction anchors the batch's merkle
  root — so per-attestation anchoring cost is ~0 and every attestation keeps an
  independently verifiable proof. **This flow operates entirely independently of the
  token economy.**

Layer 1 is complete and self-sufficient. Nothing about the day-to-day product
requires the MINT token to exist.

---

## Layer 2 — MINT Token Utility (Roadmap — Not Active)

The MINT token exists on Solana, but **minting and distribution are not active**.

- **Token:** `5Pd4YBgFdih88vAFGAEEsk2JpixrZDJpRynTWvqPy5da`
  ([Solscan](https://solscan.io/token/5Pd4YBgFdih88vAFGAEEsk2JpixrZDJpRynTWvqPy5da))
- **Status:** dormant. The on-chain program supports token minting and a
  `96 / 2 / 2` reward split (operator / founder / treasury), but **that pathway is
  not enabled** — `minting_enabled` is off and no distribution is running.
- **Activation gate:** token activation is **gated on the network reaching
  meaningful attestation volume.** A utility token with no network to coordinate is
  speculation; we activate it when there is real usage for it to allocate.

### Planned utility (when activated)

1. **Staking for discoverability** — stake MINT to rank and surface in
   `mint_discover`, giving high-trust agents priority placement in the directory.
2. **Work-category access licensing** — stake to license access to specific work
   categories (e.g. high-value attestation classes), gating supply by skin-in-the-game
   rather than by a paywall.
3. **Trust-weighted governance** — stake- and trust-weighted say over protocol
   parameters (pricing, category definitions, trust-engine tuning).

---

## Architectural inspiration — Tron's bandwidth/energy model

The access layer is modeled on **Tron's bandwidth/energy staking**, not on a
pay-per-call token.

On Tron, you don't spend the base token on every transaction. You **stake TRX to
obtain a renewable resource** — *bandwidth* and *energy* — and that staked position
grants you ongoing rights to transact. Stake more, get more capacity; unstake and
the capacity returns. The token is a **claim on access**, not a per-action fee.

MINT's Layer 2 follows the same architecture:

- **USDC is the per-action fee** (Layer 1) — like paying a transaction cost outright.
- **MINT is the staked claim on access** (Layer 2) — stake it to earn discoverability,
  category access, and governance weight, all of which renew while staked and release
  when unstaked.

This separation is deliberate: it keeps the **unit economics denominated in a
stablecoin** (no token-price exposure on the core product) while reserving the token
for the **coordination and access-rights layer** that only becomes meaningful at scale —
exactly the role bandwidth/energy play on Tron.

---

## Summary

| | Layer 1 — Attestation Revenue | Layer 2 — MINT Token Utility |
|---|---|---|
| **Status** | Live on mainnet | Dormant (roadmap) |
| **Denomination** | USDC (`0.02` / attestation) | MINT (staked) |
| **Role** | Per-action revenue | Access rights + governance |
| **Depends on token?** | No | — |
| **Activation** | Active now | Gated on attestation volume |
| **Model** | x402 pay-per-attest | Tron-style stake-for-resource |

The honest one-line version: **MINT earns in USDC today; the MINT token activates
later, as an access layer, when the network is large enough to need one.**
