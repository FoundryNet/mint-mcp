"""Paid trust-READ gate — the 2026-06-30 pricing pivot: ATTEST FREE, VERIFY PAID.

Writing to the trust graph (mint_register / mint_attest / mint_batch_attest) is
free — that's the distribution channel; every free attestation grows the graph.
READING the graph is the product:

    mint_verify         $0.005   check an attestation / actor against chain
    mint_trust_score    $0.01    agent reputation lookup
    mint_trust_history  $0.25    full attestation audit trail
    mint_trust_compare  $0.05    rank agents by trust
    mint_feed           FREE     network activity feed (discovery)

THE GATE (Stripe-first, same shape as the data-server fleet):
  1. fnet_ Bearer key  → bypass (billed downstream via Stripe subscription).
  2. keyless x402 USDC → the agent pays the per-tool price on Solana with the
     memo the 402 returns, then retries with payment_tx=<sig>. Verified on-chain
     by reusing payment_gate's verifier (one verification implementation, one
     mint_payments double-spend ledger).
  3. neither → HTTP 402 advertising BOTH the Stripe subscriptions and the
     pay-per-query x402 option.

Fail-safe: inert (reads stay free) unless READ_GATE_ENABLED *and* PAYMENT_RECIPIENT
resolves to a wallet — a misconfigured deploy serves reads free rather than
rejecting every call, exactly like payment_gate.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

import config
import payment_gate

logger = logging.getLogger("mint.read")


def is_active() -> bool:
    """Gating is ON only when armed AND a recipient wallet is configured."""
    return bool(config.READ_GATE_ENABLED and config.PAYMENT_RECIPIENT)


def price_for(tool: str) -> float:
    return config.READ_PRICES.get(tool, 0.0)


def read_intent(tool: str, args: dict) -> str:
    """Deterministic 32-hex memo for one read request. The agent gets it in the
    402, puts it on the USDC tx, and resends the identical request — so the
    server recomputes the same memo without storing a quote (mirrors
    payment_gate.intent_id, but keyed on the read tool + its arguments)."""
    canonical = json.dumps({"tool": tool, "args": args or {}},
                           sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def payment_required_body(tool: str, intent: str,
                          reason: Optional[str] = None) -> dict:
    """The HTTP-402 body for a paid read. Stripe-first (subscription upgrade) with
    a keyless pay-per-query x402 USDC fallback the agent can fulfil right now."""
    price = price_for(tool)
    base_units = round(price * (10 ** 6))   # USDC has 6 decimals
    body = {
        "status": 402,
        "error": "payment_required",     # _resp maps this to HTTP 402
        "tool": tool,
        "price": f"{price:.6f}".rstrip("0").rstrip(".") + " USDC",
        "message": ("Attest free. Verify paid. The trust graph grows with every "
                    "free attestation — reading it is where the value lives."),
        # Stripe-first: the durable, no-per-call-friction path.
        "upgrade": {
            "pro":          {"price": "$19/month",  "checkout": config.STRIPE_LINK_PRO,
                             "note": "Unlimited trust reads + verification."},
            "intelligence": {"price": "$49/month",  "checkout": config.STRIPE_LINK_INTEL,
                             "note": "Pro + history/compare + composite intelligence."},
        },
        # Keyless pay-per-query: one USDC micro-payment on Solana for this call.
        "pay_per_query": {
            "x402_usdc": {
                "amount":            f"{price:.6f}".rstrip("0").rstrip("."),
                "network":           "solana",
                "currency":          "USDC",
                "recipient":         config.PAYMENT_RECIPIENT,
                "memo":              intent,
                "usdc_mint":         config.PAYMENT_USDC_MINT,
                "amount_base_units": base_units,
                "decimals":          6,
                "expires_in":        config.PAYMENT_EXPIRY_SECONDS,
            },
        },
        "instructions": (
            f"Subscribe (Bearer fnet_ key, no per-call payment) OR send {price:.6f} "
            f"USDC ({config.PAYMENT_USDC_MINT}) to {config.PAYMENT_RECIPIENT} on "
            f"Solana with the SPL-memo '{intent}', then retry {tool} with the SAME "
            f"arguments plus payment_tx=<transaction signature>."),
    }
    if reason:
        body["reason"] = reason
    return body


def _has_api_key(api_key: Optional[str]) -> bool:
    return bool(api_key and api_key.strip())


async def precheck(tool: str, args: dict, payment_tx: Optional[str],
                   api_key: Optional[str]) -> dict:
    """Decide whether a paid read may run. Returns a dict with `gate`:
      "open"    — gating inert (free for everyone)
      "api_key" — a Bearer fnet_ key is present (billed downstream via Stripe)
      "paid"    — payment verified on-chain and the tx claimed (ledger reserved)
      "blocked" — needs payment; carries {"status":402, "body": <402 payload>}
    """
    if not is_active() or price_for(tool) <= 0:
        return {"gate": "open"}
    if _has_api_key(api_key):
        return {"gate": "api_key"}

    intent = read_intent(tool, args)
    payment_tx = (payment_tx or "").strip()
    if not payment_tx:
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(tool, intent)}

    if await payment_gate._tx_used(payment_tx):
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(
                    tool, intent, reason="This payment_tx was already used for a "
                                         "read. Make a new payment.")}

    v = await payment_gate.verify_payment(payment_tx, intent,
                                          expected_usdc=price_for(tool))
    if not v["ok"]:
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(tool, intent, reason=v["detail"])}

    row = {
        "tx_signature": payment_tx, "intent": intent, "mint_id": f"read:{tool}",
        "amount_usdc": v["amount_usdc"], "payer_wallet": v.get("payer"),
        "recipient": config.PAYMENT_RECIPIENT, "status": "verified",
        "block_time": v.get("block_time"),
    }
    if not await payment_gate._reserve_payment(row):
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(
                    tool, intent, reason="This payment_tx was already used "
                                         "(claimed concurrently). Make a new payment.")}
    logger.info(f"x402 read payment verified: {payment_tx} {v['amount_usdc']:.6f} "
                f"USDC from {v.get('payer')} for {tool}")
    return {"gate": "paid", "payment_tx": payment_tx,
            "amount_usdc": v["amount_usdc"], "payer": v.get("payer")}


def billing_note(decision: dict) -> Optional[dict]:
    """Optional `billing` block to fold into a successful paid read's response."""
    gate = decision.get("gate")
    if gate == "paid":
        return {"method": "x402", "paid_usdc": decision.get("amount_usdc"),
                "payment_tx": decision.get("payment_tx"), "payer": decision.get("payer")}
    if gate == "api_key":
        return {"method": "subscription", "note": "billed to your Forge/Stripe account"}
    return None
