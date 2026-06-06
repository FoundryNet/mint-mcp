"""MINT Protocol — Universal Work Attestation for Autonomous Agents.

A lean, standalone MCP server (FastAPI + SSE) exposing exactly three tools to any
autonomous agent, machine, or service:

  mint_register  — give an actor a persistent cryptographic identity   (FREE)
  mint_attest    — anchor a tamper-evident record of completed work     (2¢)
  mint_verify    — query an actor's trust score + verified work history (FREE)

It rebuilds nothing: identity reuses the Forge API (/v1/identify), and
settlement/trust reuse the existing MINT relay. Agents are the users — there is
no web UI, no dashboard. Free to register, free to verify, 2¢ to attest; the
network grows on free identity + free reputation, revenue comes from attestation
volume.

Transport: SSE at /sse (for remote Railway hosting). Health: GET /health.
Discovery: /.well-known/agent-card.json, /.well-known/mcp[/server-card.json].
"""
from __future__ import annotations

import logging
import os

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import config
import forge_client
import mint_client
import tools
from x402_gate import X402Middleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mint.mcp")

if not forge_client.configured():
    logger.warning("FORGE_API_KEY not set — mint_register will return not_configured "
                   "until it's set in the Railway dashboard.")
if not mint_client.configured():
    logger.warning("MINT_RELAY_KEY not set — mint_attest will fall back to Forge "
                   "/v1/settle and mint_verify will return not_configured.")

mcp = FastMCP("mint-protocol")

# x402 pay-per-attest gate. Inert unless X402_ENABLED (see x402_gate.py).
mcp.add_middleware(X402Middleware())

# Attach the three tools (one module each under tools/).
tools.register_all(mcp)


# ── Health ──────────────────────────────────────────────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness for Railway + load balancers. Reports config presence only;
    never leaks key values."""
    return JSONResponse({
        "status":            "ok",
        "service":           "mint-protocol-mcp",
        "transport":         "sse",
        "tools":             ["mint_register", "mint_attest", "mint_verify"],
        "forge_api_url":     config.FORGE_API_URL,
        "mint_relay_url":    config.MINT_RELAY_URL,
        "forge_key_configured":  forge_client.configured(),
        "relay_key_configured":  mint_client.configured(),
        "x402_enabled":      config.X402_ENABLED,
    })


# ── Discovery: A2A agent card ────────────────────────────────────────────────

_AGENT_CARD = {
    "name": "MINT Protocol",
    "description": (
        "Universal work attestation for autonomous agents. Register identity, "
        "attest completed work, verify trust scores. The reputation layer for "
        "the agent economy."
    ),
    "url": "https://mint.foundrynet.io",
    "capabilities": [
        "agent_identity",
        "work_attestation",
        "trust_verification",
        "reputation_scoring",
    ],
    "tools": [
        {"name": "mint_register",
         "description": "Register any autonomous actor with persistent cryptographic identity",
         "pricing": "free"},
        {"name": "mint_attest",
         "description": "Attest completed work with tamper-evident on-chain record",
         "pricing": "0.02 USDC per attestation"},
        {"name": "mint_verify",
         "description": "Query any actor's trust score and verified work history",
         "pricing": "free"},
    ],
    "protocols": {
        "mcp": {
            "endpoint": config.PUBLIC_SSE_URL,
            "transport": "sse",
            "tools_count": 3,
        },
        "x402": {
            "supported": True,
            "currency": "USDC",
            "network": "solana",
        },
    },
    "contact": "hello@foundrynet.io",
}


@mcp.custom_route("/.well-known/agent-card.json", methods=["GET"])
async def agent_card(request: Request) -> JSONResponse:
    """A2A agent card — how other agents discover MINT's identity + tools."""
    return JSONResponse(_AGENT_CARD, headers={"Cache-Control": "public, max-age=300"})


# ── Discovery: well-known/mcp directory crawlers ─────────────────────────────

@mcp.custom_route("/.well-known/mcp", methods=["GET"])
async def mcp_endpoints(request: Request) -> JSONResponse:
    return JSONResponse(
        {"endpoints": [{
            "url":       config.PUBLIC_SSE_URL,
            "transport": "sse",
            "name":      "MINT Protocol MCP",
        }]},
        headers={"Cache-Control": "public, max-age=300"},
    )


@mcp.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
async def server_card(request: Request) -> JSONResponse:
    """Full MCP server card consumed by directories (glama, smithery, pulsemcp)."""
    return JSONResponse(
        {
            "version":   "1.0",
            "name":      "MINT Protocol — Universal Work Attestation",
            "tagline":   "The reputation layer for the agent economy.",
            "description": (
                "Register, attest, and verify work for any autonomous agent — "
                "AI agents, physical machines, IoT devices, services. Persistent "
                "cryptographic identity, tamper-evident on-chain (Solana) work "
                "records, and trust scores built from verified history. Free to "
                "register, free to verify, 2¢ to attest."
            ),
            "serverUrl": config.PUBLIC_SSE_URL,
            "transport": "sse",
            "auth": {
                "type":   "x402_or_api_key",
                "header": "Authorization",
                "prefix": "Bearer",
                "note":   "Free tools need no auth; mint_attest takes x402 USDC or a Forge billing key.",
            },
            "tools_count": 3,
            "tools": [
                {"name": "mint_register",
                 "description": "Register any autonomous actor (agent/machine/IoT/service) with a persistent mint_id + Solana wallet. Idempotent. FREE."},
                {"name": "mint_attest",
                 "description": "Anchor a tamper-evident record of completed work on Solana mainnet and update the actor's trust score. 0.02 USDC/attestation."},
                {"name": "mint_verify",
                 "description": "Query any actor's identity, trust score, and verified on-chain work history. FREE — reputation is never gated."},
            ],
            "categories": ["agents", "identity", "reputation", "attestation", "blockchain"],
            "pricing": {
                "model":     "metered",
                "free_tier": "Unlimited register + verify, no card",
                "paid_from": "0.02 USDC per attestation (x402)",
            },
            "docs_url": "https://foundrynet.io/docs",
        },
        headers={"Cache-Control": "public, max-age=300"},
    )


# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info(
        f"MINT Protocol MCP starting on 0.0.0.0:{config.PORT} "
        f"(forge={config.FORGE_API_URL}, relay={config.MINT_RELAY_URL}, "
        f"forge_key={forge_client.configured()}, relay_key={mint_client.configured()}, "
        f"x402={config.X402_ENABLED})"
    )
    mcp.run(transport="sse", host="0.0.0.0", port=config.PORT)
