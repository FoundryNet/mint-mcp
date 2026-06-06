"""x402 pay-per-attest gate for the MINT Protocol MCP server.

Only mint_attest is priced (2¢); mint_register and mint_verify are free and pass
straight through. This is a FastMCP middleware (same boundary as forge-mcp's
GatingMiddleware) rather than an HTTP middleware, because the tool name lives in
the MCP tools/call frame, not the URL — but the x402 `x-payment` header rides the
same per-message HTTP request, so get_http_headers() can read it.

GATED + INERT by default — identical posture to Forge's x402 gate, which
crash-looped once and was fully reverted. DO NOT flip X402_ENABLED casually:
  1. Install the SVM extra: add `x402[fastapi,svm]>=2.10.0` to requirements.txt.
     WITHOUT [svm] (which pulls solders) the import crash-loops at BOOT → 502.
  2. Set env: X402_ENABLED=1, CDP_API_KEY=<portal.cdp.coinbase.com>,
     SOLANA_WALLET=<base58 pay-to>, X402_PRICE_USDC=0.02 (optional).
  3. Deploy STAGING first, tail logs (failure is boot-time), verify a paid and
     an unpaid mint_attest, validate facilitator method names against the
     installed x402 version, THEN prod.
CDP facilitator = Solana MAINNET (x402.org's public facilitator is devnet-only).

When enabled but init fails, the gate disables itself (fail-safe) rather than
bricking the server — free tools keep working and attest just isn't priced.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.dependencies import get_http_headers

import config

logger = logging.getLogger("mint.x402")

PAID_TOOLS = frozenset({"mint_attest"})

# Solana mainnet CAIP-2 network id (matches Forge's gate).
_SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

_X402: Optional[dict] = None
_ENABLED = config.X402_ENABLED


def _init() -> None:
    """Initialize the CDP facilitator client iff X402_ENABLED. Fail-safe: any
    error disables the gate instead of raising (which would crash boot)."""
    global _X402, _ENABLED
    if not _ENABLED:
        logger.info("x402 gate INERT (X402_ENABLED=0) — mint_attest is unpriced")
        return
    try:
        from x402.facilitator import FacilitatorClient, FacilitatorConfig  # type: ignore
        if not (config.CDP_API_KEY and config.SOLANA_WALLET):
            raise RuntimeError("CDP_API_KEY / SOLANA_WALLET not set")
        _X402 = {
            "client": FacilitatorClient(FacilitatorConfig(
                url="https://api.cdp.coinbase.com/platform/x402/facilitate",
                api_key=config.CDP_API_KEY)),
            "pay_to":  config.SOLANA_WALLET,
            "network": _SOLANA_MAINNET,
            "asset":   "USDC",
            "price":   config.X402_PRICE_USDC,
        }
        logger.info("x402 pay-per-attest ENABLED (CDP facilitator, Solana mainnet, "
                    f"{config.X402_PRICE_USDC} USDC/attest)")
    except Exception as e:
        logger.error(f"x402 init failed — staying DISABLED "
                     f"(need x402[fastapi,svm] + CDP_API_KEY + SOLANA_WALLET): {e}")
        _ENABLED = False
        _X402 = None


def _payment_required_error() -> ToolError:
    """HTTP-402-equivalent structured payload the agent/LLM can act on."""
    return ToolError(json.dumps({
        "error":   "payment_required",
        "message": (f"mint_attest costs {config.X402_PRICE_USDC} USDC. Resend with an "
                    "`X-PAYMENT` header (x402, Solana mainnet, USDC), or call via a "
                    "Forge billing API key."),
        "x402": {
            "scheme":   "exact",
            "network":  _SOLANA_MAINNET,
            "asset":    "USDC",
            "amount":   config.X402_PRICE_USDC,
            "pay_to":   config.SOLANA_WALLET,
        },
    }))


class X402Middleware(Middleware):
    """Gates mint_attest behind a verified x402 USDC payment. Inert unless armed."""

    def __init__(self) -> None:
        _init()

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        if not (_ENABLED and _X402 is not None):
            return await call_next(context)          # inert: free for everyone

        tool_name = getattr(context.message, "name", None) or ""
        if tool_name not in PAID_TOOLS:
            return await call_next(context)          # register/verify always free

        headers = get_http_headers(include={"x-payment"}) or {}
        payment = (headers.get("x-payment") or "").strip()
        if not payment:
            raise _payment_required_error()

        # Verify the payment with the facilitator BEFORE running the tool.
        try:
            v = await _X402["client"].verify(
                payment, pay_to=_X402["pay_to"], network=_X402["network"],
                asset=_X402["asset"], max_amount_required=_X402["price"])
            ok = getattr(v, "is_valid", None)
            ok = ok if ok is not None else (isinstance(v, dict) and v.get("isValid"))
        except Exception as e:
            logger.warning(f"x402 verify error: {type(e).__name__}: {e}")
            raise _payment_required_error()
        if not ok:
            raise _payment_required_error()

        result = await call_next(context)

        # Capture the funds after the attestation succeeded. Best-effort: the
        # work is already anchored; a settle hiccup shouldn't fail the call.
        try:
            await _X402["client"].settle(
                payment, pay_to=_X402["pay_to"], network=_X402["network"])
        except Exception as e:
            logger.warning(f"x402 settle failed (attestation already served): {e}")

        return result
