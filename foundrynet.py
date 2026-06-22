"""FoundryNet work-cells + parametric-insurance on-chain client.

Thin, dependency-light bridge from the MINT MCP tools to the `foundry_net`
Anchor program deployed on Solana **devnet** (work_cells + insurance modules).

Mirrors merkle_batch.py exactly: solders is imported lazily, transactions are
built by hand and submitted over plain JSON-RPC (httpx via http_util), and the
whole module is fail-safe — with no signer or no stake mint configured the tool
functions return ``{"status": "not_configured", ...}`` instead of raising, so a
bare deploy never breaks.

Anchor wire format reproduced here (no anchorpy needed):
  * instruction data = sha256("global:<snake_name>")[:8] ++ borsh(args…)
  * account / PDA layouts match programs/foundry_net/src/{work_cells,insurance}.rs
The discriminators + PDA seeds are cross-checked offline against the same IDL the
devnet test-suite exercised (scripts/gen-idl.js in ~/foundrynet-contracts).
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import time
import logging

import config
from http_util import request_json

logger = logging.getLogger("mint.foundrynet")

# Well-known program ids (same on every cluster).
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ATA_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
RENT_SYSVAR_ID = "SysvarRent111111111111111111111111111111111"


# ── borsh encoders ───────────────────────────────────────────────────────────
def _u8(v: int) -> bytes:
    return struct.pack("<B", v)


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _u64(v: int) -> bytes:
    return struct.pack("<Q", v)


def _i64(v: int) -> bytes:
    return struct.pack("<q", v)


def _string(s: str) -> bytes:
    b = s.encode("utf-8")
    return _u32(len(b)) + b


def _vec_u32(xs) -> bytes:
    return _u32(len(xs)) + b"".join(_u32(int(x)) for x in xs)


def _vec_string(xs) -> bytes:
    return _u32(len(xs)) + b"".join(_string(str(x)) for x in xs)


def _disc(name: str) -> bytes:
    """Anchor global instruction discriminator."""
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


# ── signer / solders loading (lazy) ───────────────────────────────────────────
def signer_configured() -> bool:
    return bool((config.FOUNDRY_CELL_WALLET or "").strip()) and bool(config.FOUNDRY_STAKE_MINT)


def _load_signer():
    """Parse FOUNDRY_CELL_WALLET (path to JSON int-array, raw JSON array, or base58
    secret). Returns a solders Keypair or None."""
    raw = (config.FOUNDRY_CELL_WALLET or "").strip()
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
    except Exception as e:  # pragma: no cover - config error path
        logger.error(f"FOUNDRY_CELL_WALLET could not be parsed: {type(e).__name__}: {e}")
        return None


def _pubkey(s):
    from solders.pubkey import Pubkey

    return Pubkey.from_string(s) if isinstance(s, str) else s


def program_id():
    return _pubkey(config.FOUNDRY_PROGRAM_ID)


# ── PDA + ATA derivation ──────────────────────────────────────────────────────
def _pda(seeds: list[bytes]):
    from solders.pubkey import Pubkey

    pk, _bump = Pubkey.find_program_address(seeds, program_id())
    return pk


def cell_pda(cell_id: str):
    return _pda([b"cell", cell_id.encode()])


def escrow_pda(cell_id: str):
    return _pda([b"escrow", cell_id.encode()])


def participant_pda(cell_id: str, who):
    return _pda([b"participant", cell_id.encode(), bytes(_pubkey(who))])


def trust_pda(who):
    return _pda([b"trust", bytes(_pubkey(who))])


def policy_pda(policy_id: str):
    return _pda([b"policy", policy_id.encode()])


def policy_escrow_pda(policy_id: str):
    return _pda([b"pescrow", policy_id.encode()])


def ata(owner, mint=None):
    """Associated token account address for (owner, mint)."""
    from solders.pubkey import Pubkey

    mint = mint or config.FOUNDRY_STAKE_MINT
    pk, _b = Pubkey.find_program_address(
        [bytes(_pubkey(owner)), bytes(_pubkey(TOKEN_PROGRAM_ID)), bytes(_pubkey(mint))],
        _pubkey(ATA_PROGRAM_ID),
    )
    return pk


def explorer(sig: str) -> str:
    return f"https://explorer.solana.com/tx/{sig}?cluster={config.FOUNDRY_CLUSTER}"


# ── tx assembly + submit ──────────────────────────────────────────────────────
def _meta(pubkey, *, signer: bool = False, writable: bool = False):
    from solders.instruction import AccountMeta

    return AccountMeta(_pubkey(pubkey), signer, writable)


def _ix(data: bytes, metas):
    from solders.instruction import Instruction

    return Instruction(program_id(), data, metas)


async def _send(instructions, signer) -> dict:
    """Sign + submit a single transaction holding `instructions`. Returns
    {"ok": True, "tx", "signer"} or {"ok": False, "reason", "detail"}."""
    try:
        from solders.message import Message
        from solders.transaction import Transaction
        from solders.hash import Hash
        import base64
    except Exception as e:  # pragma: no cover
        return {"ok": False, "reason": "solders_missing", "detail": f"{type(e).__name__}: {e}"}

    bh_resp = await request_json(
        "POST",
        config.FOUNDRY_RPC,
        body={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getLatestBlockhash",
            "params": [{"commitment": "finalized"}],
        },
        timeout=config.REQUEST_TIMEOUT,
    )
    try:
        blockhash = bh_resp["result"]["value"]["blockhash"]
    except Exception:
        return {"ok": False, "reason": "rpc_error", "detail": f"getLatestBlockhash failed: {bh_resp}"}

    try:
        bh = Hash.from_string(blockhash)
        msg = Message.new_with_blockhash(list(instructions), signer.pubkey(), bh)
        tx = Transaction([signer], msg, bh)
        raw_b64 = base64.b64encode(bytes(tx)).decode()
    except Exception as e:
        return {"ok": False, "reason": "build_failed", "detail": f"{type(e).__name__}: {e}"}

    send = await request_json(
        "POST",
        config.FOUNDRY_RPC,
        body={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [raw_b64, {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}],
        },
        timeout=config.REQUEST_TIMEOUT,
    )
    sig = send.get("result") if isinstance(send, dict) else None
    if not sig:
        return {"ok": False, "reason": "send_failed", "detail": send}
    return {"ok": True, "tx": sig, "signer": str(signer.pubkey())}


def _not_configured() -> dict:
    return {
        "status": "not_configured",
        "detail": (
            "FoundryNet on-chain tools are inert until FOUNDRY_CELL_WALLET (a funded "
            "devnet signer) and FOUNDRY_STAKE_MINT (the SPL stake/coverage mint) are "
            "set. No transaction was sent."
        ),
        "program_id": config.FOUNDRY_PROGRAM_ID,
        "cluster": config.FOUNDRY_CLUSTER,
    }


async def _fetch_account_b64(pubkey) -> bytes | None:
    resp = await request_json(
        "POST",
        config.FOUNDRY_RPC,
        body={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [str(_pubkey(pubkey)), {"encoding": "base64"}],
        },
        timeout=config.REQUEST_TIMEOUT,
    )
    try:
        import base64

        val = resp["result"]["value"]
        if not val:
            return None
        return base64.b64decode(val["data"][0])
    except Exception:
        return None


# ── instruction builders + tool entrypoints ───────────────────────────────────
async def create_cell(cell_id, work_type, max_participants, stake_required,
                      reward_pool, deadline_secs) -> dict:
    if not signer_configured():
        return _not_configured()
    if len(cell_id.encode()) > 32:
        return {"status": "error", "detail": "cell_id must be <= 32 bytes"}
    signer = _load_signer()
    if signer is None:
        return {"status": "error", "detail": "signer keypair unparseable"}

    deadline = int(time.time()) + int(deadline_secs)
    data = (
        _disc("create_cell")
        + _string(cell_id)
        + _string(work_type)
        + _u8(int(max_participants))
        + _u64(int(stake_required))
        + _u64(int(reward_pool))
        + _i64(deadline)
    )
    creator_token = ata(signer.pubkey())
    metas = [
        _meta(cell_pda(cell_id), writable=True),
        _meta(escrow_pda(cell_id), writable=True),
        _meta(config.FOUNDRY_STAKE_MINT),
        _meta(creator_token, writable=True),
        _meta(signer.pubkey(), signer=True, writable=True),
        _meta(TOKEN_PROGRAM_ID),
        _meta(SYSTEM_PROGRAM_ID),
        _meta(RENT_SYSVAR_ID),
    ]
    res = await _send([_ix(data, metas)], signer)
    if not res.get("ok"):
        return {"status": "failed", **res}
    return {
        "status": "ok",
        "tx": res["tx"],
        "explorer": explorer(res["tx"]),
        "cell_id": cell_id,
        "work_cell": str(cell_pda(cell_id)),
        "escrow": str(escrow_pda(cell_id)),
        "creator": res["signer"],
        "deadline": deadline,
    }


async def join_cell(cell_id) -> dict:
    if not signer_configured():
        return _not_configured()
    signer = _load_signer()
    if signer is None:
        return {"status": "error", "detail": "signer keypair unparseable"}

    data = _disc("join_cell") + _string(cell_id)
    part_token = ata(signer.pubkey())
    metas = [
        _meta(cell_pda(cell_id), writable=True),
        _meta(participant_pda(cell_id, signer.pubkey()), writable=True),
        _meta(trust_pda(signer.pubkey()), writable=True),
        _meta(escrow_pda(cell_id), writable=True),
        _meta(part_token, writable=True),
        _meta(signer.pubkey(), signer=True, writable=True),
        _meta(TOKEN_PROGRAM_ID),
        _meta(SYSTEM_PROGRAM_ID),
    ]
    res = await _send([_ix(data, metas)], signer)
    if not res.get("ok"):
        return {"status": "failed", **res}
    return {
        "status": "ok",
        "tx": res["tx"],
        "explorer": explorer(res["tx"]),
        "cell_id": cell_id,
        "participant": res["signer"],
        "participant_record": str(participant_pda(cell_id, signer.pubkey())),
        "trust": str(trust_pda(signer.pubkey())),
    }


async def settle_cell(cell_id, participants, scores=None) -> dict:
    """Settle a cell. `participants` is a list of base58 participant pubkeys (the
    same order their scores apply). If `scores` is given and the cell is still
    Active, an evaluate_cell tx is sent first; then settle_cell distributes the
    pool 96/2/2 and returns stakes. The signer must be the cell creator."""
    if not signer_configured():
        return _not_configured()
    signer = _load_signer()
    if signer is None:
        return {"status": "error", "detail": "signer keypair unparseable"}
    if not participants:
        return {"status": "error", "detail": "participants list is required to settle"}
    if scores is not None and len(scores) != len(participants):
        return {"status": "error", "detail": "scores length must match participants"}

    txs = []
    # Optional evaluate first (Active -> Evaluating).
    if scores is not None:
        eval_data = _disc("evaluate_cell") + _string(cell_id) + _vec_u32(scores)
        eval_metas = [
            _meta(cell_pda(cell_id), writable=True),
            _meta(signer.pubkey(), signer=True),
        ]
        for p in participants:
            eval_metas.append(_meta(participant_pda(cell_id, p), writable=True))
        eres = await _send([_ix(eval_data, eval_metas)], signer)
        if not eres.get("ok"):
            return {"status": "failed", "stage": "evaluate", **eres}
        txs.append({"evaluate": eres["tx"]})

    protocol_token = config.FOUNDRY_PROTOCOL_TOKEN or str(ata(signer.pubkey()))
    creator_token = ata(signer.pubkey())
    settle_data = _disc("settle_cell") + _string(cell_id)
    settle_metas = [
        _meta(cell_pda(cell_id), writable=True),
        _meta(escrow_pda(cell_id), writable=True),
        _meta(protocol_token, writable=True),
        _meta(creator_token, writable=True),
        _meta(signer.pubkey()),  # creator (unchecked, has_one)
        _meta(TOKEN_PROGRAM_ID),
    ]
    for p in participants:
        settle_metas.append(_meta(participant_pda(cell_id, p), writable=True))
        settle_metas.append(_meta(ata(p), writable=True))
        settle_metas.append(_meta(trust_pda(p), writable=True))
    sres = await _send([_ix(settle_data, settle_metas)], signer)
    if not sres.get("ok"):
        return {"status": "failed", "stage": "settle", **sres}
    txs.append({"settle": sres["tx"]})
    return {
        "status": "ok",
        "cell_id": cell_id,
        "txs": txs,
        "settle_tx": sres["tx"],
        "explorer": explorer(sres["tx"]),
        "split": "96% participants (score-weighted) / 2% protocol / 2% creator",
    }


async def create_policy(policy_id, trigger_field, trigger_threshold, trigger_direction,
                        trigger_duration_secs, coverage_amount, premium_amount,
                        policy_duration_secs, beneficiary, machine=None) -> dict:
    if not signer_configured():
        return _not_configured()
    if len(policy_id.encode()) > 32:
        return {"status": "error", "detail": "policy_id must be <= 32 bytes"}
    signer = _load_signer()
    if signer is None:
        return {"status": "error", "detail": "signer keypair unparseable"}

    machine = machine or str(signer.pubkey())
    data = (
        _disc("create_policy")
        + _string(policy_id)
        + _string(trigger_field)
        + _u32(int(trigger_threshold))
        + _u8(int(trigger_direction))
        + _u64(int(trigger_duration_secs))
        + _u64(int(coverage_amount))
        + _u64(int(premium_amount))
        + _i64(int(policy_duration_secs))
    )
    insurer_token = ata(signer.pubkey())
    metas = [
        _meta(policy_pda(policy_id), writable=True),
        _meta(policy_escrow_pda(policy_id), writable=True),
        _meta(config.FOUNDRY_STAKE_MINT),
        _meta(insurer_token, writable=True),
        _meta(machine),
        _meta(beneficiary),
        _meta(signer.pubkey(), signer=True, writable=True),
        _meta(TOKEN_PROGRAM_ID),
        _meta(SYSTEM_PROGRAM_ID),
        _meta(RENT_SYSVAR_ID),
    ]
    res = await _send([_ix(data, metas)], signer)
    if not res.get("ok"):
        return {"status": "failed", **res}
    return {
        "status": "ok",
        "tx": res["tx"],
        "explorer": explorer(res["tx"]),
        "policy_id": policy_id,
        "policy": str(policy_pda(policy_id)),
        "escrow": str(policy_escrow_pda(policy_id)),
        "insurer": res["signer"],
        "beneficiary": str(beneficiary),
    }


async def settle_policy(policy_id, beneficiary) -> dict:
    """Settle a policy: if triggered → pays the beneficiary; if expired untriggered
    → returns escrow to the insurer (the signer)."""
    if not signer_configured():
        return _not_configured()
    signer = _load_signer()
    if signer is None:
        return {"status": "error", "detail": "signer keypair unparseable"}

    data = _disc("settle_policy") + _string(policy_id)
    metas = [
        _meta(policy_pda(policy_id), writable=True),
        _meta(policy_escrow_pda(policy_id), writable=True),
        _meta(ata(beneficiary), writable=True),
        _meta(ata(signer.pubkey()), writable=True),
        _meta(TOKEN_PROGRAM_ID),
    ]
    res = await _send([_ix(data, metas)], signer)
    if not res.get("ok"):
        return {"status": "failed", **res}
    return {
        "status": "ok",
        "tx": res["tx"],
        "explorer": explorer(res["tx"]),
        "policy_id": policy_id,
        "policy": str(policy_pda(policy_id)),
    }
