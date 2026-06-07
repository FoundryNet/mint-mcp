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
  X402_ENABLED      "1" to arm the x402 pay-per-attest gate (default "0" = inert)
  X402_PRICE_USDC   Price per attest under x402, default "0.02"
  CDP_API_KEY       Coinbase CDP facilitator key (required iff X402_ENABLED)
  SOLANA_WALLET     base58 pay-to address for x402 settlement
"""
from __future__ import annotations

import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


FORGE_API_URL  = _env("FORGE_API_URL", "https://forge.foundrynet.io").rstrip("/")
FORGE_API_KEY  = _env("FORGE_API_KEY")        # fnet_… internal identity key

PORT            = int(_env("PORT", "8080"))
REQUEST_TIMEOUT = int(_env("REQUEST_TIMEOUT", "30"))

# x402 pay-per-attest gate — INERT by default. See x402_gate.py for activation
# steps; arming it WITHOUT the [svm] extra installed crash-loops at boot.
X402_ENABLED   = _env("X402_ENABLED", "0") == "1"
X402_PRICE_USDC = _env("X402_PRICE_USDC", "0.02")
CDP_API_KEY    = _env("CDP_API_KEY")
SOLANA_WALLET  = _env("SOLANA_WALLET", "nFvAMGrVaArW7aozYe2yNRCvC4AmCAwLkQ9pyCQna1s")

# Public SSE endpoint, used in discovery payloads. Railway maps the service
# domain here; mint.foundrynet.io is the eventual vanity host.
PUBLIC_MCP_URL = _env("PUBLIC_MCP_URL", "https://mint-mcp-production.up.railway.app/mcp")
SOLSCAN_TX_BASE = "https://solscan.io/tx"
