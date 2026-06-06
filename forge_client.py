"""Client for the Forge API (forge.foundrynet.io) — identity operations only.

The MINT Protocol server reuses Forge's existing on-chain identity
infrastructure rather than rebuilding it. mint_register maps an autonomous
actor onto the (oem, model, serial) triple Forge's /v1/identify already
understands, so every actor gets the same persistent mint_id + Solana wallet a
physical machine would. The agent never sees Forge — FORGE_API_KEY is an
internal service credential.
"""
from __future__ import annotations

from typing import Optional

import config
from http_util import request_json


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.FORGE_API_KEY}",
        "Content-Type":  "application/json",
        "User-Agent":    "MINT-Protocol-MCP/1.0",
    }


def configured() -> bool:
    return bool(config.FORGE_API_KEY)


async def identify(
    oem: str,
    model: str,
    serial: str,
    *,
    site: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """POST /v1/identify — provision or look up a mint_id for one actor.

    Idempotent on (oem, model, serial): calling again returns the same mint_id
    with `created: false`. Returns the full Forge identify payload (mint_id,
    internal_id, machine{wallet_address, …}, first_seen, created, …).
    """
    body: dict = {"oem": oem, "model": model, "serial": serial}
    if site is not None:
        body["site"] = site
    if metadata is not None:
        body["metadata"] = metadata
    return await request_json(
        "POST", f"{config.FORGE_API_URL}/v1/identify",
        headers=_headers(), body=body, timeout=config.REQUEST_TIMEOUT,
    )


async def settle(payload: dict) -> dict:
    """POST /v1/settle — fallback anchor path when MINT_RELAY_KEY is absent.

    Forge holds the relay operator credentials internally, so it can settle a
    mint_id it provisioned. Used by mint_attest only when the direct relay path
    isn't configured. Returns {tx_signature, verify_url, …}.
    """
    return await request_json(
        "POST", f"{config.FORGE_API_URL}/v1/settle",
        headers=_headers(), body=payload, timeout=config.REQUEST_TIMEOUT,
    )
