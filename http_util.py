"""Shared HTTP helper for the Forge + MINT relay clients.

Mirrors forge-mcp's `_call_forge` contract: ALWAYS returns a dict, never raises.
A failed call surfaces a structured `{"error": …, "detail": …}` payload the LLM
can read in the tool result rather than blowing up the MCP protocol frame. One
retry on transient transport errors (deploys restart upstream mid-request).
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import Optional

import httpx

logger = logging.getLogger("mint.http")

RETRY_DELAY_SECONDS = 2
RETRYABLE = (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError)


def _shape_error(status: int, body_text: str) -> dict:
    """Upstreams return either structured JSON ({detail: …}) or HTML on edge
    errors. Normalize both into one shape."""
    try:
        return {"error": f"http_{status}", "detail": _json.loads(body_text)}
    except Exception:
        return {"error": f"http_{status}", "detail": body_text[:500]}


async def request_json(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    body: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout: int = 30,
) -> dict:
    """One HTTP call with a single 2s retry on transient transport failures.

    Returns a dict on every path:
      - the decoded JSON body on 2xx,
      - {"error": "http_<status>", "detail": …} on 4xx/5xx,
      - {"error": "network", …} when the transport never completes,
      - {"error": "non_json_response", …} when 2xx body isn't JSON.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.request(
                    method, url,
                    headers=headers,
                    json=body if body is not None else None,
                    params=params,
                )
        except RETRYABLE as e:
            last_exc = e
            if attempt == 0:
                logger.info(f"transient {type(e).__name__} on {method} {url}; retrying in {RETRY_DELAY_SECONDS}s")
                await asyncio.sleep(RETRY_DELAY_SECONDS)
                continue
            return {"error": "network", "detail": f"{type(e).__name__}: {e}", "attempts": 2}
        except httpx.HTTPError as e:
            return {"error": "network", "detail": f"{type(e).__name__}: {e}"}

        if r.status_code >= 400:
            return _shape_error(r.status_code, r.text)
        try:
            return r.json()
        except Exception as e:
            return {"error": "non_json_response", "detail": f"{type(e).__name__}: {e}", "raw": r.text[:500]}

    return {"error": "unreachable", "detail": str(last_exc)}
