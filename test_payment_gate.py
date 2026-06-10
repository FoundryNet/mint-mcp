"""End-to-end test of the x402 pay-per-attest gate (offline, no real chain/Forge).

Covers the five required flows:
  1. mint_attest with NO payment        → 402 with payment instructions
  2. mint_attest with a VALID payment_tx → attestation succeeds + ledger row
  3. REPLAY the same payment_tx          → rejected (double-spend guard)
  4. Kill switch X402_ENABLED=false      → attestation runs free
  5. Paid-but-attest-fails               → 24h retry credit, then a free retry works
Plus the verifier's own checks (underpaid / wrong recipient / wrong memo / stale).

Forge's /v1/attest and the Solana RPC are monkeypatched; the Supabase-backed
stores fall back to in-memory (SUPABASE unset), so this runs with no network.
Run: python3 test_payment_gate.py
"""
from __future__ import annotations

import asyncio
import time

import config
import core
import forge_client
import payment_gate
from http_util import request_json as _real_request_json  # noqa: F401 (kept for ref)

# ── force a known config + in-memory stores ──────────────────────────────────
config.SUPABASE_SERVICE_KEY = ""           # → in-memory ledger/credits
config.X402_ENABLED = True
config.PAYMENT_RECIPIENT = "OPSWa11etRecipient1111111111111111111111111"
config.PAYMENT_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
config.ATTEST_PRICE_USDC = 0.02
config.PAYMENT_EXPIRY_SECONDS = 300
config.FORGE_API_KEY = "fnet_service_test"

PASS, FAIL = "✅ PASS", "❌ FAIL"
_results = []


def check(name: str, cond: bool, extra: str = ""):
    _results.append(cond)
    print(f"  {PASS if cond else FAIL}  {name}{(' — ' + extra) if extra else ''}")


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeForge:
    """Stand-in for Forge /v1/attest: succeeds, or fails when armed to."""
    def __init__(self):
        self.fail = False
        self.calls = 0

    async def attest(self, mint_id, duration_seconds, **kw):
        self.calls += 1
        if self.fail:
            return {"error": "http_502", "detail": "relay congestion"}
        return {"attestation_id": f"att_{self.calls}", "data_hash": "deadbeef",
                "tx_signature": f"chainSig_{self.calls}", "verify_url": None,
                "trust_score": 55, "reward": 10, "settled": True}


def make_tx(memo: str, *, amount_base=20000, recipient=None, mint=None,
            err=None, age_seconds=10, found=True):
    """Build a fake getTransaction result for a USDC transfer + memo."""
    recipient = recipient or config.PAYMENT_RECIPIENT
    mint = mint or config.PAYMENT_USDC_MINT
    if not found:
        return {"result": None}
    return {"result": {
        "blockTime": int(time.time()) - age_seconds,
        "meta": {
            "err": err,
            "preTokenBalances": [
                {"accountIndex": 3, "mint": mint, "owner": recipient,
                 "uiTokenAmount": {"amount": "0"}}],
            "postTokenBalances": [
                {"accountIndex": 3, "mint": mint, "owner": recipient,
                 "uiTokenAmount": {"amount": str(amount_base)}}],
            "logMessages": [f'Program log: Memo (len {len(memo)}): "{memo}"'],
        },
        "transaction": {"message": {
            "accountKeys": [{"pubkey": "PayerWa11et1111111111111111111111111111111",
                             "signer": True}],
            "instructions": [
                {"program": "spl-memo",
                 "programId": "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
                 "parsed": memo}],
        }},
    }}


# wire a programmable RPC: maps tx_signature → canned getTransaction result
_RPC_TABLE: dict = {}


async def fake_rpc(method, url, *, body=None, headers=None, params=None, timeout=30):
    if body and body.get("method") == "getTransaction":
        sig = body["params"][0]
        return _RPC_TABLE.get(sig, {"result": None})
    return {"error": "unexpected_call", "detail": body}


# patch both the gate's RPC and Forge
payment_gate.request_json = fake_rpc
_forge = FakeForge()
forge_client.attest = _forge.attest


# ── helpers ──────────────────────────────────────────────────────────────────

ATTEST_ARGS = dict(mint_id="MINT-tester", work_type="research",
                   duration_seconds=60, summary="did research")


def intent_for(**over):
    a = {**ATTEST_ARGS, **over}
    return payment_gate.intent_id(a["mint_id"], a["work_type"], a["duration_seconds"],
                                  a["summary"], a.get("input_hash"), a.get("output_hash"),
                                  a.get("metadata"))


async def attest(**over):
    a = {**ATTEST_ARGS, **over}
    return await core.do_attest(**a)


# ── tests ────────────────────────────────────────────────────────────────────

async def main():
    print("\n[1] No payment → 402 with instructions")
    r = await attest()
    pr = r.get("payment_required") or {}
    check("status 402", r.get("status") == 402, str(r.get("status")))
    check("error=payment_required", r.get("error") == "payment_required")
    check("amount 0.02 USDC", pr.get("amount") == "0.02" and pr.get("currency") == "USDC")
    check("recipient is ops wallet", pr.get("recipient") == config.PAYMENT_RECIPIENT)
    check("memo == derived intent", pr.get("memo") == intent_for(), pr.get("memo"))
    check("network solana", pr.get("network") == "solana")
    check("expires_in 300", pr.get("expires_in") == 300)
    check("Forge NOT called (no money, no attest)", _forge.calls == 0)

    print("\n[2] Valid payment_tx → attestation succeeds")
    memo = intent_for()
    _RPC_TABLE["goodSig"] = make_tx(memo)
    r = await attest(payment_tx="goodSig")
    check("attestation_id returned", bool(r.get("attestation_id")), str(r.get("attestation_id")))
    check("no error", "error" not in r, str(r.get("error")))
    check("payment method x402", (r.get("payment") or {}).get("method") == "x402")
    check("paid_usdc 0.02", (r.get("payment") or {}).get("paid_usdc") == 0.02)
    check("ledger row recorded", "goodSig" in payment_gate._mem_used_tx)
    check("ledger status settled",
          payment_gate._mem_used_tx.get("goodSig", {}).get("status") == "settled")
    check("ledger has attestation_id",
          payment_gate._mem_used_tx.get("goodSig", {}).get("attestation_id") == r.get("attestation_id"))

    print("\n[3] Replay same payment_tx → rejected")
    r = await attest(payment_tx="goodSig")
    check("status 402 on replay", r.get("status") == 402)
    check("reason mentions already used", "already used" in (r.get("reason") or "").lower(),
          r.get("reason"))

    print("\n[4] Verifier sub-checks (underpaid / wrong recipient / wrong memo / stale)")
    _RPC_TABLE["under"] = make_tx(intent_for(mint_id="MINT-u"), amount_base=10000)
    v = await payment_gate.verify_payment("under", intent_for(mint_id="MINT-u"))
    check("underpaid rejected", not v["ok"] and v["reason"] == "underpaid", v.get("reason"))

    _RPC_TABLE["wrongrec"] = make_tx(intent_for(mint_id="MINT-w"), recipient="SomeoneElse")
    v = await payment_gate.verify_payment("wrongrec", intent_for(mint_id="MINT-w"))
    check("wrong recipient rejected", not v["ok"] and v["reason"] == "no_transfer", v.get("reason"))

    _RPC_TABLE["wrongmemo"] = make_tx("not-the-intent")
    v = await payment_gate.verify_payment("wrongmemo", intent_for())
    check("wrong memo rejected", not v["ok"] and v["reason"] == "memo_mismatch", v.get("reason"))

    _RPC_TABLE["stale"] = make_tx(intent_for(mint_id="MINT-s"), age_seconds=999)
    v = await payment_gate.verify_payment("stale", intent_for(mint_id="MINT-s"))
    check("stale tx rejected (replay window)", not v["ok"] and v["reason"] == "expired", v.get("reason"))

    _RPC_TABLE["failed"] = make_tx(intent_for(mint_id="MINT-f"), err={"InstructionError": 1})
    v = await payment_gate.verify_payment("failed", intent_for(mint_id="MINT-f"))
    check("on-chain-failed tx rejected", not v["ok"] and v["reason"] == "tx_failed", v.get("reason"))

    v = await payment_gate.verify_payment("missing", intent_for())
    check("unconfirmed/not-found rejected", not v["ok"] and v["reason"] == "not_confirmed", v.get("reason"))

    print("\n[5] Kill switch → free")
    config.X402_ENABLED = False
    _forge.calls = 0
    r = await attest(mint_id="MINT-free", summary="free one")
    check("attest succeeds with no payment", bool(r.get("attestation_id")))
    check("no payment block in result", "payment" not in r)
    check("Forge called (work actually ran)", _forge.calls == 1)
    config.X402_ENABLED = True

    print("\n[6] Paid but attestation FAILS → 24h credit, then free retry")
    fmemo = intent_for(mint_id="MINT-credit", summary="will fail then succeed")
    _RPC_TABLE["paySig"] = make_tx(fmemo)
    _forge.fail = True
    r = await attest(mint_id="MINT-credit", summary="will fail then succeed", payment_tx="paySig")
    check("attest_failed surfaced", r.get("error") == "attest_failed", str(r.get("error")))
    check("payment preserved as credit", r.get("payment_status") == "credited")
    check("credit exists for mint_id", "MINT-credit" in payment_gate._mem_credits)
    check("ledger marked paid_attest_failed",
          payment_gate._mem_used_tx.get("paySig", {}).get("status") == "paid_attest_failed")

    # retry with NO new payment — the credit should let it through
    _forge.fail = False
    r = await attest(mint_id="MINT-credit", summary="will fail then succeed")
    check("free retry succeeds via credit", bool(r.get("attestation_id")), str(r.get("error")))
    check("retry used credit method", (r.get("payment") or {}).get("method") == "retry_credit")
    check("credit consumed", payment_gate._mem_credits["MINT-credit"]["consumed"] is True)

    # a SECOND free attempt must NOT be free (credit was one-shot)
    r = await attest(mint_id="MINT-credit", summary="will fail then succeed")
    check("credit is one-shot (next call 402s)", r.get("status") == 402, str(r.get("status")))

    # ── summary ──
    print(f"\n{'='*52}")
    total, passed = len(_results), sum(_results)
    print(f"  {passed}/{total} checks passed")
    print('='*52)
    if passed != total:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
