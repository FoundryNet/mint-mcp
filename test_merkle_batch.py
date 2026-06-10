"""End-to-end test of merkle batch anchoring (offline — no real chain / Forge / Supabase).

Covers the spec's acceptance checklist:
  1. Submit 3 attestations via the x402 gate → each recorded 'attested', anchored=false.
  2. Trigger the batch → all 3 flip to 'anchored' with merkle proofs.
  3. A SINGLE anchor tx covers the batch (one sendTransaction), regardless of size.
  4. The merkle ROOT is really written in that tx's on-chain memo.
  5. mint_verify(attestation_hash=…) returns a proof that independently validates.
  6. The background timer anchors a LONE attestation (nothing sits unanchored).
  7. Graceful shutdown (stop) flushes the pending batch.
  8. Crash recovery: start() picks up unanchored rows left in the store.
  9. Kill switch MERKLE_ANCHOR_ENABLED=false reverts to the per-attestation flow.

The Solana RPC (blockhash + sendTransaction) and the payment-gate RPC are
monkeypatched; Supabase is unconfigured so the stores run in-memory. The anchor tx
is REALLY built + signed with solders (a generated keypair) and we decode the
submitted tx to confirm the memo — only the network is faked.
Run: python3 test_merkle_batch.py
"""
from __future__ import annotations

import asyncio
import base64
import time

import base58
from solders.hash import Hash
from solders.keypair import Keypair
from solders.transaction import Transaction

import config

# ── force a known config + in-memory stores BEFORE importing the app modules ──
config.SUPABASE_SERVICE_KEY = ""           # → in-memory attestation/payment stores
config.X402_ENABLED = True
config.PAYMENT_RECIPIENT = "OPSWa11etRecipient1111111111111111111111111"
config.PAYMENT_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
config.ATTEST_PRICE_USDC = 0.02
config.PAYMENT_EXPIRY_SECONDS = 300
config.FORGE_API_KEY = "fnet_service_test"
config.MERKLE_ANCHOR_ENABLED = True
config.BATCH_SIZE = 50                      # high → count trigger won't fire; we drive it explicitly
config.BATCH_INTERVAL_SECONDS = 1           # short → timer-fire test is quick
config.ANCHOR_MAX_BATCH = 1000
_ANCHOR_KP = Keypair()
config.ANCHOR_WALLET_KEYPAIR = base58.b58encode(bytes(_ANCHOR_KP)).decode()

import core            # noqa: E402
import forge_client    # noqa: E402
import merkle_batch    # noqa: E402
import payment_gate    # noqa: E402

PASS, FAIL = "✅ PASS", "❌ FAIL"
_results = []


def check(name, cond, extra=""):
    _results.append(bool(cond))
    print(f"  {PASS if cond else FAIL}  {name}{(' — ' + str(extra)) if extra else ''}")


# ── fake Solana RPCs ──────────────────────────────────────────────────────────

_SENT = []          # base64 anchor txs submitted via sendTransaction
_BH = str(Hash.default())


async def fake_anchor_rpc(method, url, *, body=None, headers=None, params=None, timeout=30):
    m = (body or {}).get("method")
    if m == "getLatestBlockhash":
        return {"result": {"value": {"blockhash": _BH}}}
    if m == "sendTransaction":
        b64 = body["params"][0]
        _SENT.append(b64)
        tx = Transaction.from_bytes(base64.b64decode(b64))   # real sig of the submitted tx
        return {"result": str(tx.signatures[0])}
    return {"error": "unexpected_anchor_call", "detail": body}


_GATE_RPC = {}      # tx_signature -> getTransaction result


def make_tx(memo, *, amount_base=20000, recipient=None, mint=None, age_seconds=10):
    recipient = recipient or config.PAYMENT_RECIPIENT
    mint = mint or config.PAYMENT_USDC_MINT
    return {"result": {
        "blockTime": int(time.time()) - age_seconds,
        "meta": {"err": None,
                 "preTokenBalances": [{"accountIndex": 3, "mint": mint, "owner": recipient,
                                       "uiTokenAmount": {"amount": "0"}}],
                 "postTokenBalances": [{"accountIndex": 3, "mint": mint, "owner": recipient,
                                        "uiTokenAmount": {"amount": str(amount_base)}}],
                 "logMessages": [f'Program log: Memo (len {len(memo)}): "{memo}"']},
        "transaction": {"message": {
            "accountKeys": [{"pubkey": "PayerWa11et1111111111111111111111111111111", "signer": True}],
            "instructions": [{"program": "spl-memo",
                              "programId": "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
                              "parsed": memo}]}}}}


async def fake_gate_rpc(method, url, *, body=None, headers=None, params=None, timeout=30):
    if body and body.get("method") == "getTransaction":
        return _GATE_RPC.get(body["params"][0], {"result": None})
    return {"error": "unexpected_gate_call", "detail": body}


payment_gate.request_json = fake_gate_rpc
merkle_batch.request_json = fake_anchor_rpc


# ── helpers ────────────────────────────────────────────────────────────────────

BASE = dict(mint_id="MINT-batchtester", work_type="research", duration_seconds=60)


async def paid_attest(summary):
    a = {**BASE, "summary": summary}
    intent = payment_gate.intent_id(a["mint_id"], a["work_type"], a["duration_seconds"],
                                    a["summary"], None, None, None)
    sig = f"paySig_{intent[:10]}"
    _GATE_RPC[sig] = make_tx(intent)
    return await core.do_attest(**a, payment_tx=sig)


# ── tests ────────────────────────────────────────────────────────────────────

async def main():
    print("\n[1] Submit 3 attestations via the x402 gate → recorded, NOT yet anchored")
    submitted = []
    for i in range(3):
        r = await paid_attest(f"unit of work #{i}")
        submitted.append(r)
        check(f"#{i} recorded", bool(r.get("attestation_hash")) and r.get("status") == "attested",
              r.get("error") or r.get("attestation_hash"))
        check(f"#{i} anchored=false", r.get("anchored") is False and r.get("pending_anchor") is True)
        check(f"#{i} has anchor_eta", bool(r.get("anchor_eta")), r.get("anchor_eta"))
        check(f"#{i} payment x402", (r.get("payment") or {}).get("method") == "x402")
    hashes = [r["attestation_hash"] for r in submitted]
    check("3 distinct attestation hashes", len(set(hashes)) == 3)

    st = await merkle_batch.status()
    check("batch status shows 3 pending", st["current_batch_size"] == 3, st["current_batch_size"])
    pre = await merkle_batch.get_attestation(hashes[0])
    check("pre-anchor status is 'attested'", pre and pre["status"] == "attested")

    print("\n[2] Anchor the batch → all 3 flip to 'anchored' in ONE tx")
    res = await merkle_batch.flush()
    check("anchored 3", res.get("anchored") == 3, res)
    check("batch_size 3", res.get("batch_size") == 3)
    check("returned a merkle_root", bool(res.get("merkle_root")))
    check("returned an anchor_tx", bool(res.get("anchor_tx")))
    check("EXACTLY ONE on-chain tx for the batch", len(_SENT) == 1, f"{len(_SENT)} txs")
    root, anchor_tx = res["merkle_root"], res["anchor_tx"]

    print("\n[3] The merkle ROOT is in the anchor tx's on-chain memo")
    decoded = bytes(Transaction.from_bytes(base64.b64decode(_SENT[0])).message.instructions[0].data).decode()
    check("memo carries the root", root in decoded, decoded)
    check("memo is MINT-MERKLE:v1:<root>:3:<ts>",
          decoded.startswith(f"{merkle_batch.MEMO_PREFIX}:{root}:3:"), decoded)

    print("\n[4] Every attestation now anchored with an independently-valid proof")
    for i, h in enumerate(hashes):
        v = await core.do_verify(attestation_hash=h)
        check(f"#{i} anchored=true", v.get("anchored") is True, v.get("status"))
        check(f"#{i} same root", v.get("merkle_root") == root)
        check(f"#{i} same anchor_tx", v.get("anchor_tx") == anchor_tx)
        check(f"#{i} proof_valid (server)", v.get("proof_valid") is True)
        # independent re-verification (what a third party would do)
        indep = merkle_batch.verify_proof(h, v.get("merkle_proof"), root)
        check(f"#{i} proof validates independently", indep)
        # a proof must NOT validate against a different attestation's hash
        other = hashes[(i + 1) % 3]
        check(f"#{i} proof rejects wrong leaf", not merkle_batch.verify_proof(other, v.get("merkle_proof"), root))

    print("\n[5] Cost property: one tx anchored 3 attestations")
    check("anchor cost is per-BATCH not per-attestation", len(_SENT) == 1,
          "→ ~0.000005 SOL for the batch regardless of size")
    st = await merkle_batch.status()
    check("status: nothing left pending", st["current_batch_size"] == 0)
    check("status: total_attestations_anchored == 3", st["total_attestations_anchored"] == 3, st)

    print("\n[6] Background timer anchors a LONE attestation (never sits indefinitely)")
    before = len(_SENT)
    r = await paid_attest("a single straggler")
    lone_hash = r["attestation_hash"]
    await merkle_batch.start()                 # starts the loop (interval=1s)
    for _ in range(40):                        # poll up to ~4s for the timer to fire
        await asyncio.sleep(0.1)
        if len(_SENT) > before:
            break
    await merkle_batch.stop()
    check("timer fired a 2nd anchor tx", len(_SENT) == before + 1, f"{len(_SENT)} total")
    v = await core.do_verify(attestation_hash=lone_hash)
    check("lone attestation anchored alone", v.get("anchored") is True and v.get("batch_id"), v.get("status"))

    print("\n[7] Crash recovery + graceful-shutdown flush via stop()")
    r = await paid_attest("recorded then process dies")
    surv = r["attestation_hash"]
    # simulate a crash: the row exists as 'attested' but was never anchored
    check("survivor still 'attested' (would survive a crash)",
          (await merkle_batch.get_attestation(surv))["status"] == "attested")
    before = len(_SENT)
    await merkle_batch.start()                 # start() recovers the backlog…
    await merkle_batch.stop()                  # …and stop() flushes it (SIGTERM path)
    check("shutdown flush anchored the survivor", len(_SENT) == before + 1)
    check("survivor now anchored", (await merkle_batch.get_attestation(surv))["status"] == "anchored")

    print("\n[8] Kill switch: MERKLE_ANCHOR_ENABLED=false → per-attestation Forge flow")
    class FakeForge:
        calls = 0
        async def attest(self, mint_id, duration_seconds, **kw):
            FakeForge.calls += 1
            return {"attestation_id": "att_forge_1", "data_hash": "abc",
                    "tx_signature": "forgeChainSig", "verify_url": None,
                    "trust_score": 55, "reward": 10, "settled": True}
    forge_client.attest = FakeForge().attest
    config.MERKLE_ANCHOR_ENABLED = False
    sent_before = len(_SENT)
    r = await paid_attest("kill-switch path")
    check("old path returns tx_signature", r.get("tx_signature") == "forgeChainSig", r.get("tx_signature"))
    check("old path is NOT batched (no anchored flag)", "anchored" not in r)
    check("Forge.attest was called", FakeForge.calls == 1)
    check("no new merkle anchor tx in kill-switch mode", len(_SENT) == sent_before)
    config.MERKLE_ANCHOR_ENABLED = True

    # ── summary ──
    print(f"\n{'='*54}")
    total, passed = len(_results), sum(_results)
    print(f"  {passed}/{total} checks passed")
    print('='*54)
    if passed != total:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
