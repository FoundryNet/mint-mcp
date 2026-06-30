"""Pay-per-attest gate for mint_attest — on-chain USDC micropayments on Solana.

THE FLOW (HTTP 402, memo-bound, verified on-chain — no facilitator):
  1. Agent calls mint_attest with NO payment_tx → the gate returns a 402 body
     telling it to send `ATTEST_PRICE_USDC` USDC to `PAYMENT_RECIPIENT` with a
     specific `memo` (a payment *intent*, derived deterministically from the
     request — see `intent_id`).
  2. Agent makes the USDC transfer on Solana, putting that memo on the tx.
  3. Agent retries mint_attest with the SAME payload + `payment_tx=<signature>`.
  4. The gate confirms the transfer on-chain via plain JSON-RPC: the tx is
     confirmed (meta.err == null), it moved ≥ price USDC to the operations
     wallet, the memo matches the intent, it's within PAYMENT_EXPIRY_SECONDS
     (replay window), and the signature hasn't paid for a prior attestation.
  5. Only then does the attestation run.

Why memo == a derived *intent*, not the attestation_id the spec sketches: the
attestation_id only exists AFTER Forge settles, but the agent must pay BEFORE.
`intent_id(...)` is a stable hash of the request, so the agent can compute the
same memo on both calls and the server never has to remember a quote.

WHAT STAYS FREE: mint_register and mint_verify never call this gate. A caller
that presents a real `Authorization: Bearer fnet_…` key also bypasses it — those
are billed downstream via Forge/Stripe; the on-chain path is the keyless option.

PERSISTENCE: every verified payment is recorded in Supabase `mint_payments`
(UNIQUE tx_signature = the double-spend guard, and the revenue ledger). A
paid-but-failed attestation grants a one-shot `mint_attest_credits` row so the
agent can retry once for free within 24h — we never eat their money. When
Supabase is unconfigured the same stores live in-process (single-instance only).

SAFETY: unlike the old facilitator gate (x402_gate.py), this needs no solders /
x402[svm] extra — it's httpx JSON-RPC — so it can't crash-loop at boot. It is
DEFAULT ON but fail-safe inert unless PAYMENT_RECIPIENT is set; X402_ENABLED=false
is the kill switch.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Optional

import config
import supa
from http_util import request_json

logger = logging.getLogger("mint.pay")

_USDC_DECIMALS = 6  # USDC on Solana

# spl-memo program ids (v2 current, v1 legacy) — used to read a tx's memo.
_MEMO_PROGRAM_IDS = frozenset({
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo",
})

# In-memory fallback stores (used only when Supabase isn't configured). Keyed for
# a single instance; production runs with Supabase so the guards are durable.
_mem_used_tx: dict = {}        # tx_signature -> ledger row
_mem_credits: dict = {}        # mint_id -> {"expires_ts", "consumed", "source_tx"}


# ── activation ────────────────────────────────────────────────────────────────

def is_active() -> bool:
    """Gating is ON only when armed AND a recipient wallet is configured. A missing
    recipient ⇒ inert (attest stays free) rather than rejecting every call."""
    return bool(config.X402_ENABLED and config.PAYMENT_RECIPIENT)


def _expected_base_units(price_usdc: Optional[float] = None) -> int:
    price = config.ATTEST_PRICE_USDC if price_usdc is None else price_usdc
    return round(price * (10 ** _USDC_DECIMALS))


# ── payment intent (the memo) ─────────────────────────────────────────────────

def intent_id(mint_id: str, work_type: str, duration_seconds, summary: str,
              input_hash: Optional[str], output_hash: Optional[str],
              metadata: Optional[dict]) -> str:
    """Deterministic 32-hex memo for one attestation request. The agent gets it in
    the 402, puts it on the USDC tx, and resends the identical payload — so the
    server recomputes the same memo without storing a quote."""
    canonical = json.dumps({
        "mint_id": mint_id, "work_type": (work_type or "").strip().lower(),
        "duration_seconds": int(duration_seconds) if str(duration_seconds).lstrip("-").isdigit() else duration_seconds,
        "summary": summary or "", "input_hash": input_hash or "",
        "output_hash": output_hash or "", "metadata": metadata or {},
    }, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def payment_required_body(intent: str, reason: Optional[str] = None) -> dict:
    """The HTTP-402 body. Standard JSON any MCP/HTTP client can parse; the spec's
    keys (amount/currency/network/recipient/memo/expires_in) plus enough extra
    detail (mint, base units, instructions) for an agent to build the tx."""
    body = {
        "status": 402,
        "error": "payment_required",      # lets the REST layer map this to HTTP 402
        "payment_required": {
            "amount": f"{config.ATTEST_PRICE_USDC:.2f}",
            "currency": "USDC",
            "network": "solana",
            "recipient": config.PAYMENT_RECIPIENT,
            "memo": intent,
            "expires_in": config.PAYMENT_EXPIRY_SECONDS,
            "usdc_mint": config.PAYMENT_USDC_MINT,
            "amount_base_units": _expected_base_units(),
            "decimals": _USDC_DECIMALS,
        },
        "instructions": (
            f"Send {config.ATTEST_PRICE_USDC:.2f} USDC ({config.PAYMENT_USDC_MINT}) "
            f"to {config.PAYMENT_RECIPIENT} on Solana with the SPL-memo set to "
            f"'{intent}', then call mint_attest again with the SAME arguments plus "
            f"payment_tx=<transaction signature>."),
    }
    if reason:
        body["reason"] = reason
    return body


# ── on-chain verification (plain Solana JSON-RPC) ─────────────────────────────

def _fail(code: str, detail: str) -> dict:
    return {"ok": False, "reason": code, "detail": detail}


async def verify_payment(tx_signature: str, expected_memo: str,
                         expected_usdc: Optional[float] = None) -> dict:
    """Confirm a USDC payment on-chain. Returns {"ok": True, "amount_base",
    "amount_usdc", "payer", "block_time"} or {"ok": False, "reason", "detail"}.

    Checks: tx found + confirmed (meta.err is null), ≥ price USDC moved to the
    operations wallet (via token-balance delta — robust to transfer vs.
    transferChecked and to ATA creation), memo matches the intent, and the tx is
    within PAYMENT_EXPIRY_SECONDS (freshness = replay guard).

    `expected_usdc` overrides the required amount (defaults to ATTEST_PRICE_USDC)
    so the paid-read gate (read_gate.py) can reuse this same verifier at its own
    per-tool prices."""
    rpc = {
        "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
        "params": [tx_signature, {
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 0,
            "commitment": "confirmed",
        }],
    }
    resp = await request_json("POST", config.PAYMENT_VERIFY_RPC, body=rpc,
                              timeout=config.REQUEST_TIMEOUT)
    if not isinstance(resp, dict) or "error" in resp:
        return _fail("rpc_error", f"Solana RPC call failed: {resp.get('detail') if isinstance(resp, dict) else resp}")
    if resp.get("error"):  # JSON-RPC error object
        return _fail("rpc_error", f"Solana RPC error: {resp['error']}")
    result = resp.get("result")
    if result is None:
        return _fail("not_confirmed",
                     "Transaction not found or not yet confirmed. Wait for "
                     "confirmation, then retry with the same payment_tx.")
    meta = result.get("meta") or {}
    if meta.get("err") is not None:
        return _fail("tx_failed", f"Transaction failed on-chain: {meta.get('err')}")

    block_time = result.get("blockTime")
    if block_time is None:
        return _fail("not_confirmed", "Transaction has no blockTime yet (still processing).")
    age = time.time() - block_time
    if age > config.PAYMENT_EXPIRY_SECONDS:
        return _fail("expired",
                     f"Payment is {int(age)}s old; it must be within "
                     f"{config.PAYMENT_EXPIRY_SECONDS}s. Make a fresh payment.")
    if age < -120:
        return _fail("clock_skew", "Transaction blockTime is in the future (clock skew).")

    delta = _usdc_delta_to_recipient(meta)
    if delta is None:
        return _fail("no_transfer",
                     f"No USDC ({config.PAYMENT_USDC_MINT}) transfer to the "
                     f"operations wallet {config.PAYMENT_RECIPIENT} found in this tx.")
    need = _expected_base_units(expected_usdc)
    if delta < need:
        price = config.ATTEST_PRICE_USDC if expected_usdc is None else expected_usdc
        return _fail("underpaid",
                     f"Transferred {delta / 10**_USDC_DECIMALS:.6f} USDC; need at "
                     f"least {price:.6f} USDC.")

    memo = _extract_memo(result, meta)
    if not memo or expected_memo not in memo:
        return _fail("memo_mismatch",
                     f"Payment memo {memo!r} does not contain the required intent "
                     f"'{expected_memo}'. Pay with that exact memo.")

    return {"ok": True, "amount_base": delta, "amount_usdc": delta / 10**_USDC_DECIMALS,
            "payer": _payer(result), "block_time": block_time}


def _usdc_delta_to_recipient(meta: dict) -> Optional[int]:
    """Net USDC (base units) received by PAYMENT_RECIPIENT in this tx, by diffing
    pre/post token balances. None if the wallet's USDC balance didn't grow."""
    mint, recip = config.PAYMENT_USDC_MINT, config.PAYMENT_RECIPIENT
    pre = {b.get("accountIndex"): b for b in (meta.get("preTokenBalances") or [])}
    post = {b.get("accountIndex"): b for b in (meta.get("postTokenBalances") or [])}
    best: Optional[int] = None
    for idx, pb in post.items():
        if pb.get("mint") != mint or pb.get("owner") != recip:
            continue
        post_amt = int(pb.get("uiTokenAmount", {}).get("amount", 0))
        pre_amt = int((pre.get(idx) or {}).get("uiTokenAmount", {}).get("amount", 0))
        d = post_amt - pre_amt
        best = d if best is None or d > best else best
    return best


def _extract_memo(result: dict, meta: dict) -> Optional[str]:
    """Read the SPL-memo from a jsonParsed tx: scan top-level + inner instructions
    for the memo program, then fall back to the program log."""
    msg = (result.get("transaction") or {}).get("message") or {}
    instrs = list(msg.get("instructions") or [])
    for inner in (meta.get("innerInstructions") or []):
        instrs.extend(inner.get("instructions") or [])
    for ins in instrs:
        if ins.get("program") == "spl-memo" or ins.get("programId") in _MEMO_PROGRAM_IDS:
            p = ins.get("parsed")
            if isinstance(p, str):
                return p
            if isinstance(p, dict):
                return p.get("memo") or p.get("info")
    for line in (meta.get("logMessages") or []):
        m = re.search(r'Memo \(len \d+\): "(.*)"', line)
        if m:
            return m.group(1)
    return None


def _payer(result: dict) -> Optional[str]:
    keys = ((result.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    if keys:
        first = keys[0]
        return first.get("pubkey") if isinstance(first, dict) else first
    return None


# ── stores (Supabase, with in-memory fallback) ────────────────────────────────

async def _tx_used(tx_signature: str) -> bool:
    if supa.configured():
        return await supa.payment_tx_used(tx_signature)
    return tx_signature in _mem_used_tx


async def _reserve_payment(row: dict) -> bool:
    """Record a verified payment, claiming its signature. Returns False if the
    signature was already consumed (double-spend) — the UNIQUE constraint is the
    real guard, so a 409 here means another attest already used this tx."""
    tx = row["tx_signature"]
    if supa.configured():
        res = await supa.insert_payment(row)
        if "error" in res:
            blob = json.dumps(res).lower()
            if "409" in blob or "duplicate" in blob or "unique" in blob:
                return False
            # Unknown write error: log, but don't hand out a free attest — treat as
            # not-reserved so the caller is told to retry rather than served unpaid.
            logger.error(f"payment ledger insert failed (treating as unreserved): {res}")
            return False
        return True
    if tx in _mem_used_tx:
        return False
    _mem_used_tx[tx] = row
    return True


async def _finalize_payment(tx_signature: str, fields: dict) -> None:
    if supa.configured():
        await supa.finalize_payment(tx_signature, fields)
    elif tx_signature in _mem_used_tx:
        _mem_used_tx[tx_signature].update(fields)


async def _active_credit(mint_id: str) -> Optional[dict]:
    if supa.configured():
        return await supa.active_credit(mint_id, _iso(time.time()))
    c = _mem_credits.get(mint_id)
    if c and not c["consumed"] and c["expires_ts"] > time.time():
        return {"id": mint_id, "source_tx": c.get("source_tx")}
    return None


async def _consume_credit(credit: dict) -> bool:
    if supa.configured():
        res = await supa.consume_credit(credit["id"])
        return bool(res.get("data"))   # empty data ⇒ someone else already claimed it
    c = _mem_credits.get(credit["id"])
    if c and not c["consumed"]:
        c["consumed"] = True
        return True
    return False


async def _grant_credit(mint_id: str, source_tx: Optional[str]) -> None:
    """One-shot retry credit so a paid-but-failed attest isn't lost money."""
    expires = time.time() + config.PAYMENT_CREDIT_TTL_SECONDS
    if supa.configured():
        await supa.insert_credit({
            "mint_id": mint_id, "source_tx": source_tx, "consumed": False,
            "expires_at": _iso(expires)})
    else:
        _mem_credits[mint_id] = {"expires_ts": expires, "consumed": False, "source_tx": source_tx}
    logger.info(f"granted 24h retry credit to {mint_id} (paid tx {source_tx} but attest failed)")


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts))


# ── the gate (called by core.do_attest, behind BOTH MCP and REST) ─────────────

def _has_api_key(api_key: Optional[str]) -> bool:
    return bool(api_key and api_key.strip())


async def precheck(mint_id: str, intent: str, payment_tx: Optional[str],
                   api_key: Optional[str]) -> dict:
    """Decide whether the attestation may run. Returns a dict with `gate`:
      "open"    — gating inert (free for everyone)
      "api_key" — a Bearer fnet_ key is present (billed downstream)
      "credit"  — an unexpired retry credit was claimed (free retry)
      "paid"    — payment verified on-chain and the tx claimed (ledger reserved)
      "blocked" — needs payment; carries {"status":402, "body": <402 payload>}
    """
    if not is_active():
        return {"gate": "open"}
    if _has_api_key(api_key):
        return {"gate": "api_key"}

    # A live retry credit takes precedence over payment_tx (the prior tx is now
    # 'used', so we must not ask them to verify it again).
    credit = await _active_credit(mint_id)
    if credit and await _consume_credit(credit):
        logger.info(f"{mint_id} attesting on a retry credit (source tx {credit.get('source_tx')})")
        return {"gate": "credit", "source_tx": credit.get("source_tx")}

    payment_tx = (payment_tx or "").strip()
    if not payment_tx:
        return {"gate": "blocked", "status": 402, "body": payment_required_body(intent)}

    if await _tx_used(payment_tx):
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(
                    intent, reason="This payment_tx was already used for an "
                                   "attestation. Make a new payment.")}

    v = await verify_payment(payment_tx, intent)
    if not v["ok"]:
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(intent, reason=v["detail"])}

    row = {
        "tx_signature": payment_tx, "intent": intent, "mint_id": mint_id,
        "amount_usdc": v["amount_usdc"], "payer_wallet": v.get("payer"),
        "recipient": config.PAYMENT_RECIPIENT, "status": "verified",
        "block_time": v.get("block_time"),
    }
    if not await _reserve_payment(row):
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(
                    intent, reason="This payment_tx was already used (claimed "
                                   "concurrently). Make a new payment.")}
    logger.info(f"x402 payment verified: {payment_tx} {v['amount_usdc']:.6f} USDC "
                f"from {v.get('payer')} for {mint_id}")
    return {"gate": "paid", "payment_tx": payment_tx,
            "amount_usdc": v["amount_usdc"], "payer": v.get("payer")}


async def settle(decision: dict, mint_id: str, attestation_id: Optional[str],
                 ok: bool) -> None:
    """Finalize the revenue ledger after the attestation resolves, and make the
    agent whole on failure. Called only when precheck claimed payment/credit."""
    gate = decision.get("gate")
    if gate == "paid":
        tx = decision.get("payment_tx")
        if ok:
            await _finalize_payment(tx, {"status": "settled", "attestation_id": attestation_id})
        else:
            await _finalize_payment(tx, {"status": "paid_attest_failed"})
            await _grant_credit(mint_id, source_tx=tx)   # don't eat their money
    elif gate == "credit" and not ok:
        # The free retry also failed — refresh the credit so they can try again.
        await _grant_credit(mint_id, source_tx=decision.get("source_tx"))
