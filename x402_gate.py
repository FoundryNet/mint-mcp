"""x402 pay-per-attest gate for the MINT Protocol MCP server — the Tron model.

Only mint_attest is priced; mint_register and mint_verify are ALWAYS free and
unauthenticated (they're never in PAID_TOOLS, and the REST routes for them never
call this gate). Free to join the network, free to check trust, pay per
attestation — volume is the revenue.

When X402_ENABLED=1, mint_attest requires EITHER of:
  • Authorization: Bearer fnet_…  — an API key (billed via Forge/Stripe), OR
  • X-PAYMENT: <x402>             — a 2¢ USDC micropayment (no key needed).
Either one passes the gate; neither → HTTP 402 / ToolError with x402 requirements.

The same authorize()/capture() helpers back BOTH surfaces: the FastMCP middleware
(MCP/SSE tool calls) and the REST /v1/attest route (the mint-attest SDK + any HTTP
client), so the policy can't drift between them. The x402 `x-payment` and
`authorization` headers ride the per-request HTTP frame, readable via
get_http_headers() over MCP and request.headers over REST.

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


def is_active() -> bool:
    """True only when x402 is enabled AND the facilitator initialized cleanly.
    When False the gate is inert — attest is unpriced (key billing only)."""
    return bool(_ENABLED and _X402 is not None)


def payment_required_payload() -> dict:
    """HTTP-402 body: tells the caller how to pay (x402) or that a key also works."""
    return {
        "error":   "payment_required",
        "message": (f"mint_attest requires payment: send `Authorization: Bearer fnet_…` "
                    f"(API key, billed via Stripe) OR an `X-PAYMENT` header "
                    f"({config.X402_PRICE_USDC} USDC, x402 on Solana mainnet)."),
        "accepted": ["api_key", "x402"],
        "x402": {
            "scheme":  "exact",
            "network": _SOLANA_MAINNET,
            "asset":   "USDC",
            "amount":  config.X402_PRICE_USDC,
            "pay_to":  config.SOLANA_WALLET,
        },
    }


def _has_api_key(authorization: Optional[str]) -> bool:
    a = (authorization or "").strip()
    return a.lower().startswith("bearer ") and bool(a[7:].strip())


async def authorize(authorization: Optional[str], payment: Optional[str]) -> dict:
    """Decide whether a mint_attest call may proceed. Returns:
      {"ok": True,  "method": "api_key"}                — a Bearer key is present
      {"ok": True,  "method": "x402", "payment": <hdr>} — a payment verified
      {"ok": False, "error": <402 payload>}             — neither

    The API key isn't validated here — Forge validates it downstream (a bad key
    fails the attest, so it's never a free ride). x402 is verified with the
    facilitator BEFORE the work runs; capture() settles it AFTER success.
    """
    if not is_active():
        return {"ok": True, "method": "open"}        # inert → unpriced
    if _has_api_key(authorization):
        return {"ok": True, "method": "api_key"}     # billed via Forge/Stripe
    payment = (payment or "").strip()
    if not payment:
        return {"ok": False, "error": payment_required_payload()}
    try:
        v = await _X402["client"].verify(
            payment, pay_to=_X402["pay_to"], network=_X402["network"],
            asset=_X402["asset"], max_amount_required=_X402["price"])
        ok = getattr(v, "is_valid", None)
        ok = ok if ok is not None else (isinstance(v, dict) and v.get("isValid"))
    except Exception as e:
        logger.warning(f"x402 verify error: {type(e).__name__}: {e}")
        return {"ok": False, "error": payment_required_payload()}
    if not ok:
        return {"ok": False, "error": payment_required_payload()}
    return {"ok": True, "method": "x402", "payment": payment}


async def capture(payment: Optional[str]) -> None:
    """Settle a verified x402 payment AFTER the attestation succeeded. Best-effort:
    the work is already anchored, so a settle hiccup must not fail the call."""
    if not (is_active() and payment):
        return
    try:
        await _X402["client"].settle(
            payment, pay_to=_X402["pay_to"], network=_X402["network"])
    except Exception as e:
        logger.warning(f"x402 settle failed (attestation already served): {e}")


class X402Middleware(Middleware):
    """Gates ONLY mint_attest, accepting an API key OR an x402 payment. Inert
    unless armed (X402_ENABLED + facilitator initialized)."""

    def __init__(self) -> None:
        _init()

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        if not is_active():
            return await call_next(context)          # inert: free for everyone

        tool_name = getattr(context.message, "name", None) or ""
        if tool_name not in PAID_TOOLS:
            return await call_next(context)          # register/verify always free

        headers = get_http_headers(include={"authorization", "x-payment"}) or {}
        decision = await authorize(headers.get("authorization"), headers.get("x-payment"))
        if not decision["ok"]:
            raise ToolError(json.dumps(decision["error"]))

        result = await call_next(context)
        if decision.get("method") == "x402":
            await capture(decision.get("payment"))
        return result
