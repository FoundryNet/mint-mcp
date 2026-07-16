"""Env-driven configuration for the MINT Protocol MCP server.

Single source of truth for every external dependency. Nothing here reads a
secret at import time beyond os.environ; values are plain module globals so
tools/clients can `from config import FORGE_API_URL` without a settings object.

Forge-only by design: mint-mcp is a thin presentation layer and Forge is the
single relay key-holder + settlement engine. mint-mcp never talks to the MINT
relay directly, so there is NO relay key here — only FORGE_API_KEY.
  mint_register → Forge POST /v1/identify
  mint_attest   → Forge POST /v1/attest   (Forge settles against the real mint_id)
  mint_verify   → identity now; Forge trust-read endpoint rolling out next.

Required:
  FORGE_API_KEY     fnet_… internal service key. The agent never sees Forge.

Optional:
  FORGE_API_URL     Default https://forge.foundrynet.io
  PORT              Default 8080 (Railway injects this)
  REQUEST_TIMEOUT   HTTP timeout seconds, default 30
  X402_ENABLED      "true" to arm the pay-per-attest gate (DEFAULT FALSE as of the
                    2026-06-30 pricing pivot — attestation is FREE, the distribution
                    channel; revenue moved to the paid trust-read gate, READ_GATE).
                    Set "true" only to revert to the legacy pay-per-attest model.
  ATTEST_PRICE_USDC Price per attest in USDC, default "0.02"
  PAYMENT_RECIPIENT base58 operations wallet that must receive the USDC (the gate
                    is inert until this is set; defaults to SOLANA_WALLET)
  PAYMENT_VERIFY_RPC Solana JSON-RPC used to confirm the payment on-chain
  PAYMENT_USDC_MINT  SPL mint accepted as payment (default = USDC mainnet)
  PAYMENT_EXPIRY_SECONDS  Payment freshness + quote window, default 300 (5 min)
  PAYMENT_CREDIT_TTL_SECONDS  Retry-credit lifetime after a paid-but-failed
                    attest, default 86400 (24h)
  MERKLE_ANCHOR_ENABLED  "true" (default) batches attestations and anchors one
                    merkle root per batch on Solana; "false" is the kill switch
                    back to per-attestation on-chain settlement via Forge.
  BATCH_SIZE        attestations per batch before an early anchor, default 50
  BATCH_INTERVAL_SECONDS  max seconds an attestation waits before anchoring,
                    default 300 (5 min)
  ANCHOR_WALLET_KEYPAIR  signer for anchor txs (base58 secret / JSON int array /
                    path to such a file). Only pays the per-tx fee.
  ANCHOR_RPC        Solana RPC for blockhash + sendTransaction (default = the
                    payment-verify RPC)

Legacy (the superseded x402-facilitator gate in x402_gate.py — no longer wired):
  X402_PRICE_USDC, CDP_API_KEY, SOLANA_WALLET
"""
from __future__ import annotations

import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


FORGE_API_URL  = _env("FORGE_API_URL", "https://forge.foundrynet.io").rstrip("/")
FORGE_API_KEY  = _env("FORGE_API_KEY")        # fnet_… internal identity key

# Supabase — the trust layer (Layers 6+7: ratings, recommendations, discovery,
# trust scores) lives in the Foundry Supabase project. mint-mcp reads/writes the
# mint_* tables directly via PostgREST with the service-role key. Identity +
# attestation still go through Forge; only the trust/discovery aggregates are
# stored here. Empty values ⇒ the trust features degrade gracefully to identity.
SUPABASE_URL          = _env("SUPABASE_URL", "https://hjiozatcmozqddhaklkh.supabase.co").rstrip("/")
SUPABASE_SERVICE_KEY  = _env("SUPABASE_SERVICE_KEY")   # service-role JWT (server-side only)

PORT            = int(_env("PORT", "8080"))
REQUEST_TIMEOUT = int(_env("REQUEST_TIMEOUT", "30"))

# ── Pay-per-attest gate (payment_gate.py) ────────────────────────────────────
# The agent pays 2¢ USDC on Solana (memo = the payment intent the 402 returns),
# then retries mint_attest with payment_tx=<sig>; the gate confirms the transfer
# on-chain before the attestation runs. Plain JSON-RPC over httpx — NO solders /
# x402[svm] extra, so it can't crash-loop at boot the way the facilitator gate did.
#
# DEFAULT OFF as of the 2026-06-30 pricing pivot: attestation is FREE (the
# distribution channel — every free attestation grows the trust graph), and
# revenue moves to the paid trust-READ gate below. The old pay-per-attest model
# is still here behind X402_ENABLED=true, fail-safe inert unless PAYMENT_RECIPIENT
# resolves to a wallet.
def _flag(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")

X402_ENABLED   = _flag("X402_ENABLED", False)

# base58 pay-to address for the legacy facilitator gate; also the default
# operations wallet the new gate expects to receive payment. Railway overrides it.
SOLANA_WALLET  = _env("SOLANA_WALLET", "nFvAMGrVaArW7aozYe2yNRCvC4AmCAwLkQ9pyCQna1s")

ATTEST_PRICE_USDC      = float(_env("ATTEST_PRICE_USDC", "0.02"))
PAYMENT_RECIPIENT      = _env("PAYMENT_RECIPIENT", SOLANA_WALLET).strip()
PAYMENT_VERIFY_RPC     = _env("PAYMENT_VERIFY_RPC", "https://api.mainnet-beta.solana.com").rstrip("/")
# USDC on Solana mainnet (6 decimals). Override only for a different stable/network.
PAYMENT_USDC_MINT      = _env("PAYMENT_USDC_MINT", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v").strip()
PAYMENT_EXPIRY_SECONDS = int(_env("PAYMENT_EXPIRY_SECONDS", "300"))
PAYMENT_CREDIT_TTL_SECONDS = int(_env("PAYMENT_CREDIT_TTL_SECONDS", "86400"))

# ── Paid trust-READ gate (read_gate.py) ──────────────────────────────────────
# The 2026-06-30 pivot: ATTEST FREE, VERIFY PAID. Writing to the trust graph is
# free (distribution); READING it is the product. Per-tool USDC micro-pricing,
# Stripe-first (subscription) with a keyless x402 USDC fallback. An fnet_ Bearer
# key bypasses the gate (billed downstream via Stripe). Same on-chain verification
# primitives as payment_gate (reused), same fail-safe: inert unless a recipient
# wallet is set AND READ_GATE_ENABLED, so a misconfigured deploy serves reads free
# rather than rejecting every call.
READ_GATE_ENABLED = _flag("READ_GATE_ENABLED", True)

# Per-tool price in USDC. Keys are the canonical tool names; the REST routes +
# MCP tools look their price up here so pricing lives in ONE place.
READ_PRICES = {
    "mint_verify":        float(_env("PRICE_MINT_VERIFY", "0.005")),
    "mint_trust_score":   float(_env("PRICE_MINT_TRUST_SCORE", "0.01")),
    "mint_trust_history": float(_env("PRICE_MINT_TRUST_HISTORY", "0.25")),
    "mint_trust_compare": float(_env("PRICE_MINT_TRUST_COMPARE", "0.05")),
}

# Stripe subscription links offered in every paid-read 402 (Stripe-first, same
# links the data-server fleet uses). Override per-deploy via env if they rotate.
STRIPE_LINK_PRO   = _env("STRIPE_LINK_PRO",   "https://buy.stripe.com/3cIdR278Cglq7bY5b67N604")  # $19/mo
STRIPE_LINK_INTEL = _env("STRIPE_LINK_INTEL", "https://buy.stripe.com/4gMaEQ78C8SYaoa32Y7N605")  # $49/mo


# ── Merkle batch anchoring (merkle_batch.py) ─────────────────────────────────
# Instead of one on-chain settlement per attestation (~0.002 SOL of rent each),
# attestations are recorded in Supabase (status "attested"), batched, and a single
# Solana transaction anchors the merkle ROOT of the whole batch (SPL-memo on a
# fee-only tx ⇒ ~0.000005 SOL regardless of batch size). Each attestation keeps
# its merkle proof, so inclusion under the on-chain root is independently
# verifiable without trusting FoundryNet.
#
# DEFAULT ON. The kill switch MERKLE_ANCHOR_ENABLED=false reverts mint_attest to
# the old per-attestation on-chain flow (Forge /v1/attest — expensive but exact).
# Like the payment gate, the anchorer is fail-safe: if no signer wallet is
# configured, attestations are still recorded + paid (status "attested") and drain
# into a batch as soon as a wallet IS configured — nothing is lost.
MERKLE_ANCHOR_ENABLED   = _flag("MERKLE_ANCHOR_ENABLED", True)
BATCH_SIZE              = int(_env("BATCH_SIZE", "50"))            # count trigger
BATCH_INTERVAL_SECONDS  = int(_env("BATCH_INTERVAL_SECONDS", "300"))  # time trigger (5 min)
# Upper bound on attestations folded into a single anchor tx (keeps proof depth +
# the one PATCH sweep bounded when draining a large backlog); the loop re-fires
# until the backlog is empty.
ANCHOR_MAX_BATCH        = int(_env("ANCHOR_MAX_BATCH", "1000"))
# The wallet that signs anchor txs. Accepts a base58 64-byte secret key, a JSON
# array of 64 ints (solana-keygen format), OR a filesystem path to such a JSON
# file. It only pays the per-tx fee (~5000 lamports) — keep it minimally funded.
ANCHOR_WALLET_KEYPAIR   = _env("ANCHOR_WALLET_KEYPAIR")
# RPC used to fetch a blockhash and submit the anchor tx. Defaults to the same
# endpoint the payment gate reads with.
ANCHOR_RPC              = _env("ANCHOR_RPC", PAYMENT_VERIFY_RPC).rstrip("/")
# SPL Memo program (v2) — the merkle root is written as a memo on the anchor tx.
MEMO_PROGRAM_ID         = _env("MEMO_PROGRAM_ID", "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")

# ── FoundryNet on-chain work cells + parametric insurance (foundrynet.py) ─────
# Devnet deployment of the foundry_net program (the work_cells + insurance
# modules). The five mint_*_cell / mint_*_policy tools build and submit REAL
# transactions against that program, signed by FOUNDRY_CELL_WALLET (which falls
# back to the merkle ANCHOR_WALLET_KEYPAIR). Like the anchorer + payment gate they
# are fail-safe: with no signer or no stake mint configured they return
# status="not_configured" instead of erroring, so a bare deploy never breaks.
#
# NOTE: this program id is the DEVNET deployment — it is deliberately NOT the
# mainnet foundry_net program (4ZvTZ3skfeMF3ZGyABoazPa9tiudw2QSwuVKn45t2AKL).
FOUNDRY_PROGRAM_ID  = _env("FOUNDRY_PROGRAM_ID", "GPAsjEHRKdoKeHsfgBTcJ6eoNLQ1BMpQ83eV3XHnMKKR")
FOUNDRY_RPC         = _env("FOUNDRY_RPC", "https://api.devnet.solana.com").rstrip("/")
FOUNDRY_CLUSTER     = _env("FOUNDRY_CLUSTER", "devnet").strip()
# Signer for cell/policy txs; defaults to the merkle anchor wallet so a single
# funded keypair can serve both. Same accepted formats (base58 / JSON array / path).
FOUNDRY_CELL_WALLET = _env("FOUNDRY_CELL_WALLET", ANCHOR_WALLET_KEYPAIR)
# SPL mint used for stakes + rewards (cells) and coverage + premium (insurance).
# On devnet this is a test mint the signer holds a funded associated account of.
FOUNDRY_STAKE_MINT  = _env("FOUNDRY_STAKE_MINT").strip()
# Where the 2% protocol fee on cell settlement lands (default = the signer's ATA).
FOUNDRY_PROTOCOL_TOKEN = _env("FOUNDRY_PROTOCOL_TOKEN").strip()

# Legacy facilitator gate (x402_gate.py) — kept for reference, no longer wired.
X402_PRICE_USDC = _env("X402_PRICE_USDC", "0.02")
CDP_API_KEY    = _env("CDP_API_KEY")

# Public SSE endpoint, used in discovery payloads. Railway maps the service
# domain here; mint.foundrynet.io is the eventual vanity host.
PUBLIC_MCP_URL = _env("PUBLIC_MCP_URL", "https://mint-mcp-production.up.railway.app/mcp")
SOLSCAN_TX_BASE = "https://solscan.io/tx"
