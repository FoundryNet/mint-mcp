"""Client for the MINT relay (mint-relay-production.up.railway.app).

The relay owns settlement and trust: it records a job on Solana, lets the oracle
score it, settles the reward, and tracks per-machine trust + job history. We talk
to it with a `mint_` operator key. That key MUST be the same operator Forge
provisions machines under (MINT_RELAY_KEY == Forge's internal relay operator),
otherwise the relay won't recognize the mint_ids that mint_register minted.

Endpoints used (full surface is intentionally small — no /record, /settle bundles
record+trust+settle):
  POST /register            register internal ids → mint_ids (idempotent)
  POST /settle              record+trust+settle one job, anchor on Solana
  GET  /history/{mint_id}   per-machine job history + trust + earnings
  GET  /fleet               operator-wide rollup
  GET  /health              liveness
"""
from __future__ import annotations

from typing import Optional

import config
from http_util import request_json


def configured() -> bool:
    return bool(config.MINT_RELAY_KEY)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.MINT_RELAY_KEY}",
        "Content-Type":  "application/json",
        "User-Agent":    "MINT-Protocol-MCP/1.0",
    }


async def register(internal_ids: list[str]) -> dict:
    """POST /register — register one or more internal identifiers. Idempotent;
    already-registered ids come back with status 'already_registered'."""
    return await request_json(
        "POST", f"{config.MINT_RELAY_URL}/register",
        headers=_headers(), body={"machines": internal_ids},
        timeout=config.REQUEST_TIMEOUT,
    )


async def settle(mint_id: str, duration_seconds: int, complexity: int = 1000) -> dict:
    """POST /settle — record_job → update_trust → settle_job in one call.

    Anchors on Solana mainnet and returns {status, job_id, tx_signature,
    verify_url, trust_score, final_reward, …}. complexity is clamped 500–2000
    by the relay (1000 = network baseline).
    """
    return await request_json(
        "POST", f"{config.MINT_RELAY_URL}/settle",
        headers=_headers(),
        body={
            "machine_id":       mint_id,
            "duration_seconds": duration_seconds,
            "complexity":       complexity,
        },
        timeout=config.REQUEST_TIMEOUT,
    )


async def history(mint_id: str, *, limit: int = 50) -> dict:
    """GET /history/{mint_id} — job history + summary (total_jobs, total_earned,
    average_trust) for one machine."""
    return await request_json(
        "GET", f"{config.MINT_RELAY_URL}/history/{mint_id}",
        headers=_headers(), params={"limit": limit},
        timeout=config.REQUEST_TIMEOUT,
    )


async def fleet() -> dict:
    """GET /fleet — operator-wide summary across all machines."""
    return await request_json(
        "GET", f"{config.MINT_RELAY_URL}/fleet",
        headers=_headers(), timeout=config.REQUEST_TIMEOUT,
    )


async def health() -> dict:
    """GET /health — no auth required."""
    return await request_json(
        "GET", f"{config.MINT_RELAY_URL}/health",
        timeout=config.REQUEST_TIMEOUT,
    )
