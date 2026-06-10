"""Merkle batch anchoring — one cheap on-chain tx for a whole batch of attestations.

WHY: settling every attestation on-chain creates a fresh rent-exempt account
(~0.002 SOL each), which destroys the margin on a 2¢ product. Instead we record
each attestation off-chain (Supabase, status 'attested'), accumulate them, and
anchor the MERKLE ROOT of the batch in a SINGLE Solana transaction — an SPL-memo
on a fee-only tx (no account creation), so the on-chain cost is ~0.000005 SOL
*per batch*, independent of batch size. Each attestation keeps its merkle proof,
so inclusion under the on-chain root is independently verifiable by anyone holding
(attestation_hash, merkle_proof, on-chain root) — no trust in FoundryNet required.

TRIGGERS (whichever first): BATCH_SIZE attestations queued, or BATCH_INTERVAL_SECONDS
elapsed. A lone attestation still anchors when the timer fires — nothing sits
unanchored indefinitely.

CRASH-SAFETY: the source of truth is the Supabase row (status 'attested'). The
in-memory queue is only a trigger signal; an anchor pass RE-READS the unanchored
rows from the store, so a crash before anchoring loses nothing — the next pass (or
the next boot, via start()'s recovery) picks them up. SIGTERM → the lifespan
shutdown calls flush(), anchoring whatever is pending before exit.

MERKLE CONSTRUCTION (independently reproducible in any language):
  leaf(i)   = sha256( 0x00 || bytes.fromhex(attestation_hash_i) )
  node(a,b) = sha256( 0x01 || a || b )
  odd node  → duplicated (paired with itself) when promoting a level
  root      = top of the tree; a single-leaf batch has root == leaf(0)
  proof     = [{"sibling": <hex>, "position": "left"|"right"}, …] bottom→top
  verify    : fold the proof into leaf(target); the result must equal the root,
              and the root is the value written in the anchor tx's memo.

SAFETY: solders is imported LAZILY and the anchorer is fail-safe — if no signer
wallet is configured it simply doesn't anchor (attestations stay 'attested',
recorded + paid, and drain once a wallet is set). It can't crash-loop at boot.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Optional

import config
import supa
from http_util import request_json   # module-level so tests can monkeypatch it

logger = logging.getLogger("mint.merkle")

MEMO_PREFIX = "MINT-MERKLE:v1"

# In-memory stores — used ONLY when Supabase is unconfigured (single instance).
# Production runs with Supabase, where the row is the durable source of truth.
_mem_attestations: dict = {}   # attestation_hash -> row
_mem_anchor_batches: list = []


# ── canonical hashing ─────────────────────────────────────────────────────────

def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


def compute_data_hash(mint_id: str, work_type: str, duration_seconds: int,
                      summary: str, input_hash: Optional[str],
                      output_hash: Optional[str], metadata: Optional[dict]) -> str:
    """sha256 over the canonical WORK payload — the reproducible content commitment
    (same canonicalization Forge /v1/attest used)."""
    return hashlib.sha256(_canonical({
        "mint_id": mint_id, "work_type": work_type,
        "duration_seconds": duration_seconds, "summary": summary or "",
        "input_hash": input_hash or "", "output_hash": output_hash or "",
        "metadata": metadata or {},
    }).encode("utf-8")).hexdigest()


def compute_attestation_hash(record: dict) -> str:
    """sha256 over the canonical full attestation RECORD (id + created_at included,
    so it's unique per row). This is the merkle leaf and the public verify handle."""
    return hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()


# ── merkle tree (pure, dependency-free, independently reproducible) ────────────

def _h(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def leaf_hash(attestation_hash_hex: str) -> bytes:
    """Domain-separated leaf: 0x00 prefix distinguishes leaves from internal nodes
    (guards against the duplicate-node second-preimage class of attacks)."""
    return _h(b"\x00" + bytes.fromhex(attestation_hash_hex))


def node_hash(left: bytes, right: bytes) -> bytes:
    return _h(b"\x01" + left + right)


def build_levels(leaves: list) -> list:
    """Bottom-up list of levels; level[0] = leaves, level[-1] = [root]. Odd nodes
    are paired with themselves (duplicated) when promoting."""
    if not leaves:
        return [[]]
    levels = [leaves]
    cur = leaves
    while len(cur) > 1:
        nxt = []
        for i in range(0, len(cur), 2):
            left = cur[i]
            right = cur[i + 1] if i + 1 < len(cur) else cur[i]   # duplicate last if odd
            nxt.append(node_hash(left, right))
        levels.append(nxt)
        cur = nxt
    return levels


def root_of(levels: list) -> bytes:
    return levels[-1][0]


def proof_for(levels: list, index: int) -> list:
    """Inclusion path for leaf `index`, bottom→top: each step names the sibling and
    whether it sits to the left or right (so the verifier folds in the right order)."""
    proof = []
    idx = index
    for level in levels[:-1]:
        if idx % 2 == 0:                                   # left child
            sib = idx + 1 if idx + 1 < len(level) else idx  # self when odd/duplicated
            position = "right"
        else:                                               # right child
            sib = idx - 1
            position = "left"
        proof.append({"sibling": level[sib].hex(), "position": position})
        idx //= 2
    return proof


def verify_proof(attestation_hash_hex: str, proof: list, root_hex: str) -> bool:
    """Reproduce the root from a leaf + its proof. Anyone can run this with only the
    attestation_hash, the merkle_proof, and the on-chain root — no server needed."""
    try:
        h = leaf_hash(attestation_hash_hex)
        for step in proof or []:
            sib = bytes.fromhex(step["sibling"])
            if step.get("position") == "left":
                h = node_hash(sib, h)
            else:
                h = node_hash(h, sib)
        return h.hex() == root_hex
    except Exception:
        return False


# ── attestation store (Supabase, with in-memory fallback) ─────────────────────

async def record_attestation(mint_id: str, work_type: str, duration_seconds: int,
                             summary: str = "", input_hash: Optional[str] = None,
                             output_hash: Optional[str] = None,
                             metadata: Optional[dict] = None,
                             payment_tx: Optional[str] = None) -> dict:
    """Persist a new attestation as status 'attested' and queue it for the next
    anchor batch. Returns the identifiers (or {"error": …} if the store write
    fails — the caller treats that like a failed attestation and refunds)."""
    att_id = str(uuid.uuid4())
    created_at = _iso(time.time())
    data_hash = compute_data_hash(mint_id, work_type, duration_seconds, summary,
                                  input_hash, output_hash, metadata)
    record = {
        "id": att_id, "mint_id": mint_id, "work_type": work_type,
        "data_hash": data_hash, "duration_seconds": duration_seconds,
        "summary": summary or "", "payment_tx": payment_tx, "created_at": created_at,
    }
    attestation_hash = compute_attestation_hash(record)
    row = {**record, "attestation_hash": attestation_hash, "status": "attested"}

    if supa.configured():
        res = await supa.insert_attestation(row)
        if "error" in res:
            return {"error": "store_failed", "detail": res}
    else:
        _mem_attestations[attestation_hash] = dict(row)

    _engine.enqueue(attestation_hash)
    return {"attestation_id": att_id, "attestation_hash": attestation_hash,
            "data_hash": data_hash, "status": "attested", "created_at": created_at}


async def get_attestation(attestation_hash: str) -> Optional[dict]:
    if supa.configured():
        return await supa.get_attestation_by_hash(attestation_hash)
    return _mem_attestations.get(attestation_hash)


async def attestations_for_mint(mint_id: str, limit: int = 10) -> list:
    if supa.configured():
        return await supa.attestations_for_mint(mint_id, limit=limit)
    rows = [r for r in _mem_attestations.values() if r.get("mint_id") == mint_id]
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows[:limit]


async def _list_unanchored(limit: int) -> list:
    if supa.configured():
        return await supa.list_attested(limit=limit)
    rows = [r for r in _mem_attestations.values() if r.get("status") == "attested"]
    rows.sort(key=lambda r: r.get("created_at") or "")
    return rows[:limit]


async def _count_unanchored() -> int:
    if supa.configured():
        return await supa.attested_count()
    return sum(1 for r in _mem_attestations.values() if r.get("status") == "attested")


async def _mark_anchored(row: dict, fields: dict) -> bool:
    if supa.configured():
        res = await supa.mark_attestation_anchored(row["id"], fields)
        return bool(res.get("data"))
    r = _mem_attestations.get(row["attestation_hash"])
    if r and r.get("status") == "attested":
        r.update({"status": "anchored", **fields})
        return True
    return False


async def _record_anchor_batch(batch_row: dict) -> None:
    if supa.configured():
        await supa.insert_anchor_batch(batch_row)
    else:
        _mem_anchor_batches.append(batch_row)


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts))


# ── on-chain anchor (lazy solders; fee-only memo tx) ──────────────────────────

def _load_keypair():
    """Parse ANCHOR_WALLET_KEYPAIR: a filesystem path to a JSON int array, a raw
    JSON int array, or a base58 64-byte secret. Returns a solders Keypair or None
    (None ⇒ inert anchorer)."""
    raw = (config.ANCHOR_WALLET_KEYPAIR or "").strip()
    if not raw:
        return None
    from solders.keypair import Keypair
    try:
        if os.path.exists(raw):
            with open(raw) as f:
                raw = f.read().strip()
        if raw.startswith("["):
            return Keypair.from_bytes(bytes(json.loads(raw)))
        return Keypair.from_base58_string(raw)
    except Exception as e:
        logger.error(f"ANCHOR_WALLET_KEYPAIR could not be parsed: {type(e).__name__}: {e}")
        return None


async def _anchor_onchain(memo: str) -> dict:
    """Sign + submit ONE Solana tx whose only instruction is an SPL-memo carrying
    the merkle root. Fee-only (no account creation) ⇒ ~5000 lamports total.
    Returns {"ok": True, "tx", "signer"} or {"ok": False, "reason", "detail"}.
    Monkeypatched in tests so no real chain is touched."""
    kp = _load_keypair()
    if kp is None:
        return {"ok": False, "reason": "no_signer",
                "detail": "ANCHOR_WALLET_KEYPAIR unset/invalid — cannot anchor yet."}
    try:
        from solders.pubkey import Pubkey
        from solders.instruction import Instruction, AccountMeta
        from solders.message import Message
        from solders.transaction import Transaction
        from solders.hash import Hash
        import base64
    except Exception as e:
        return {"ok": False, "reason": "solders_missing", "detail": f"{type(e).__name__}: {e}"}

    bh_resp = await request_json("POST", config.ANCHOR_RPC, body={
        "jsonrpc": "2.0", "id": 1, "method": "getLatestBlockhash",
        "params": [{"commitment": "finalized"}]}, timeout=config.REQUEST_TIMEOUT)
    try:
        blockhash = bh_resp["result"]["value"]["blockhash"]
    except Exception:
        return {"ok": False, "reason": "rpc_error",
                "detail": f"getLatestBlockhash failed: {bh_resp}"}

    try:
        bh = Hash.from_string(blockhash)
        memo_ix = Instruction(
            Pubkey.from_string(config.MEMO_PROGRAM_ID), memo.encode("utf-8"),
            [AccountMeta(kp.pubkey(), True, False)])
        msg = Message.new_with_blockhash([memo_ix], kp.pubkey(), bh)
        tx = Transaction([kp], msg, bh)
        raw_b64 = base64.b64encode(bytes(tx)).decode()
    except Exception as e:
        return {"ok": False, "reason": "build_failed", "detail": f"{type(e).__name__}: {e}"}

    send = await request_json("POST", config.ANCHOR_RPC, body={
        "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
        "params": [raw_b64, {"encoding": "base64", "skipPreflight": False,
                             "maxRetries": 3}]}, timeout=config.REQUEST_TIMEOUT)
    sig = send.get("result") if isinstance(send, dict) else None
    if not sig:
        return {"ok": False, "reason": "send_failed", "detail": send}
    return {"ok": True, "tx": sig, "signer": str(kp.pubkey())}


# ── the batch anchorer ────────────────────────────────────────────────────────

class BatchAnchorer:
    def __init__(self):
        self._wake = asyncio.Event()
        self._stop = False
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._pending = 0                 # best-effort queue signal (not the truth)
        self._next_anchor_ts: Optional[float] = None
        self._stats = {"last_anchor_tx": None, "last_anchor_at": None,
                       "last_anchor_count": 0, "total_anchored": 0,
                       "total_batches": 0, "last_error": None}

    # --- queue signal (called from the request path; never blocks) ---
    def enqueue(self, attestation_hash: str) -> None:
        self._pending += 1
        if self._pending >= config.BATCH_SIZE:
            self._wake.set()

    def next_anchor_eta(self) -> str:
        # The next anchor is at most one interval away even if the loop isn't yet
        # tracking a concrete deadline (e.g. a request that lands before start()).
        ts = self._next_anchor_ts or (time.time() + config.BATCH_INTERVAL_SECONDS)
        return _iso(ts)

    # --- lifecycle ---
    async def start(self) -> None:
        if not config.MERKLE_ANCHOR_ENABLED:
            logger.info("merkle anchoring DISABLED (MERKLE_ANCHOR_ENABLED=false) — "
                        "mint_attest uses per-attestation on-chain settlement")
            return
        if self._task and not self._task.done():
            return
        self._stop = False
        self._wake.clear()
        backlog = await _count_unanchored()
        self._pending = backlog
        self._next_anchor_ts = time.time() + config.BATCH_INTERVAL_SECONDS
        self._task = asyncio.create_task(self._run())
        armed = "armed" if config.ANCHOR_WALLET_KEYPAIR else "INERT (no ANCHOR_WALLET_KEYPAIR)"
        logger.info(f"merkle anchorer started: {armed}; batch_size={config.BATCH_SIZE}, "
                    f"interval={config.BATCH_INTERVAL_SECONDS}s, backlog={backlog}")
        if backlog > 0:
            self._wake.set()

    async def _run(self) -> None:
        while not self._stop:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=config.BATCH_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
            if self._stop:
                break
            self._wake.clear()
            try:
                await self.anchor_now(reason="threshold/timer")
            except Exception as e:
                logger.warning(f"anchor cycle errored: {type(e).__name__}: {e}")
                self._stats["last_error"] = f"{type(e).__name__}: {e}"
            self._next_anchor_ts = time.time() + config.BATCH_INTERVAL_SECONDS

    async def stop(self) -> None:
        self._stop = True
        self._wake.set()
        if self._task:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=12)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        # Final flush: anchor anything still pending before the process exits.
        try:
            await self.flush()
        except Exception as e:
            logger.warning(f"shutdown flush errored: {type(e).__name__}: {e}")

    async def flush(self) -> dict:
        return await self.anchor_now(reason="flush")

    # --- the anchor pass ---
    async def anchor_now(self, reason: str = "manual") -> dict:
        async with self._lock:
            rows = await _list_unanchored(config.ANCHOR_MAX_BATCH)
            if not rows:
                self._pending = 0
                return {"anchored": 0, "reason": reason, "note": "nothing pending"}

            hashes = [r["attestation_hash"] for r in rows]
            levels = build_levels([leaf_hash(h) for h in hashes])
            root = root_of(levels).hex()
            ts = int(time.time())
            memo = f"{MEMO_PREFIX}:{root}:{len(rows)}:{ts}"

            anchor = await _anchor_onchain(memo)
            if not anchor.get("ok"):
                self._stats["last_error"] = f"anchor failed ({anchor.get('reason')}): {anchor.get('detail')}"
                logger.warning(f"batch anchor skipped — {self._stats['last_error']}; "
                               f"{len(rows)} attestation(s) remain 'attested', will retry")
                return {"anchored": 0, "reason": reason, "error": anchor}

            tx = anchor["tx"]
            batch_id = str(uuid.uuid4())
            anchored_at = _iso(ts)
            n_ok = 0
            for i, r in enumerate(rows):
                fields = {"batch_id": batch_id, "merkle_root": root,
                          "merkle_proof": proof_for(levels, i),
                          "anchor_tx": tx, "anchored_at": anchored_at}
                if await _mark_anchored(r, fields):
                    n_ok += 1
            await _record_anchor_batch({
                "id": batch_id, "merkle_root": root, "batch_size": len(rows),
                "anchor_tx": tx, "memo": memo, "anchored_at": anchored_at})

            self._stats.update(last_anchor_tx=tx, last_anchor_at=anchored_at,
                               last_anchor_count=len(rows), last_error=None)
            self._stats["total_anchored"] += n_ok
            self._stats["total_batches"] += 1
            logger.info(f"anchored batch {batch_id}: {n_ok}/{len(rows)} attestations "
                        f"under root {root[:16]}… in 1 tx {tx} (reason={reason})")

            more = len(rows) >= config.ANCHOR_MAX_BATCH
            self._pending = 0
            if more:
                self._wake.set()   # backlog exceeded one tx — drain again immediately
            return {"anchored": n_ok, "batch_size": len(rows), "batch_id": batch_id,
                    "merkle_root": root, "anchor_tx": tx, "signer": anchor.get("signer"),
                    "more_pending": more}

    async def status(self) -> dict:
        pending = await _count_unanchored()
        sec_until = None
        if self._next_anchor_ts is not None:
            sec_until = max(0, round(self._next_anchor_ts - time.time()))
        return {
            "merkle_anchor_enabled": config.MERKLE_ANCHOR_ENABLED,
            "anchor_armed": bool(config.ANCHOR_WALLET_KEYPAIR),
            "current_batch_size": pending,
            "batch_size_threshold": config.BATCH_SIZE,
            "batch_interval_seconds": config.BATCH_INTERVAL_SECONDS,
            "seconds_until_next_anchor": sec_until,
            "last_anchor_tx": self._stats["last_anchor_tx"],
            "last_anchor_at": self._stats["last_anchor_at"],
            "last_anchor_count": self._stats["last_anchor_count"],
            "total_attestations_anchored": self._stats["total_anchored"],
            "total_batches": self._stats["total_batches"],
            "last_error": self._stats["last_error"],
            "solscan": (f"{config.SOLSCAN_TX_BASE}/{self._stats['last_anchor_tx']}"
                        if self._stats["last_anchor_tx"] else None),
        }


# ── module singleton + public surface ─────────────────────────────────────────

_engine = BatchAnchorer()


async def start() -> None:
    await _engine.start()


async def stop() -> None:
    await _engine.stop()


async def flush() -> dict:
    return await _engine.flush()


async def status() -> dict:
    return await _engine.status()


def next_anchor_eta() -> str:
    return _engine.next_anchor_eta()
