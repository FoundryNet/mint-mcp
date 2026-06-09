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


def _headers(api_key: Optional[str] = None) -> dict:
    # Per-request key (e.g. an SDK developer's fnet_ key passed through from the
    # REST surface) overrides the server's service key. register + attest for one
    # actor MUST use the SAME key so Forge's ownership check holds.
    return {
        "Authorization": f"Bearer {api_key or config.FORGE_API_KEY}",
        "Content-Type":  "application/json",
        "User-Agent":    "MINT-Protocol-MCP/1.0",
    }


def configured(api_key: Optional[str] = None) -> bool:
    return bool(api_key or config.FORGE_API_KEY)


async def identify(
    oem: str,
    model: str,
    serial: str,
    *,
    site: Optional[str] = None,
    metadata: Optional[dict] = None,
    api_key: Optional[str] = None,
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
        headers=_headers(api_key), body=body, timeout=config.REQUEST_TIMEOUT,
    )


async def autonomous_register(
    actor_type: str,
    name: str,
    *,
    capabilities: Optional[list] = None,
    operator: Optional[str] = None,
    metadata: Optional[dict] = None,
    api_key: Optional[str] = None,
) -> dict:
    """POST /v1/autonomous-register — anonymous identity + scoped-key provisioning.

    Forge generates a fresh synthetic owner for this actor, registers it on the
    relay, mints a scoped+capped+revocable fnet_ key, and returns
    {mint_id, api_key, ...}. No human, no signup. The (optional) key here is the
    MCP server's own service key and is ignored by the anonymous endpoint.
    """
    body: dict = {"name": name, "actor_type": actor_type}
    if capabilities is not None:
        body["capabilities"] = capabilities
    if operator is not None:
        body["operator"] = operator
    if metadata is not None:
        body["metadata"] = metadata
    return await request_json(
        "POST", f"{config.FORGE_API_URL}/v1/autonomous-register",
        headers=_headers(api_key), body=body, timeout=config.REQUEST_TIMEOUT,
    )


async def whoami(api_key: str) -> dict:
    """GET /v1/whoami — resolve an fnet_ key to its Forge account.

    Returns {user_id, is_demo, has_subscription, …} on a valid key, or
    {"error": "http_401", …} when the key is missing/invalid. Used by the trust
    layer to bind a rater/recommender to the identities their key actually owns
    (anti-spam: only a real account can rate, and only as an actor it controls).
    """
    if not api_key:
        return {"error": "bad_request", "detail": "api_key required for whoami"}
    return await request_json(
        "GET", f"{config.FORGE_API_URL}/v1/whoami",
        headers=_headers(api_key), timeout=config.REQUEST_TIMEOUT,
    )


async def attest(
    mint_id: str,
    duration_seconds: int,
    *,
    complexity: int = 1000,
    work_type: Optional[str] = None,
    input_hash: Optional[str] = None,
    output_hash: Optional[str] = None,
    summary: Optional[str] = None,
    metadata: Optional[dict] = None,
    api_key: Optional[str] = None,
) -> dict:
    """POST /v1/attest — settle work against the actor's OWN mint_id.

    Forge is the single relay key-holder: it settles on the relay against the
    real mint_id (accruing trust + earnings + history), computes the canonical
    data_hash, and returns {attestation_id, data_hash, tx_signature, verify_url,
    trust_score, reward, settled, …}. mint-mcp never touches the relay.
    """
    body: dict = {"mint_id": mint_id, "duration_seconds": duration_seconds,
                  "complexity": complexity}
    if work_type is not None:   body["work_type"] = work_type
    if input_hash is not None:  body["input_hash"] = input_hash
    if output_hash is not None: body["output_hash"] = output_hash
    if summary is not None:     body["summary"] = summary
    if metadata is not None:    body["metadata"] = metadata
    return await request_json(
        "POST", f"{config.FORGE_API_URL}/v1/attest",
        headers=_headers(api_key), body=body, timeout=config.REQUEST_TIMEOUT,
    )
