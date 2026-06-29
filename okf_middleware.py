"""FastMCP middleware that attaches an honest okf-reliability-v1 object to every
MCP tool result's `_meta` — the integrity and reliability axes as siblings
(modelcontextprotocol#2964).

FAIL-SAFE BY DESIGN: any error in computing the metadata leaves the tool result
completely untouched. This wraps EVERY tool on the server, so it must never break
a tool response. The cardinal rule it carries: signed != verified — the object is
always `verified: false, vantage: producer-reported` for self-attested output.
"""
from __future__ import annotations

import json
import datetime

from fastmcp.server.middleware import Middleware

import okf_reliability as okf


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class ReliabilityMiddleware(Middleware):
    def __init__(self, server_id: str, version: str = "1.0.0", forecast: bool = False):
        self.server_id = server_id
        self.version = version
        self.forecast = forecast  # set True for prediction servers (UNVERIFIED basis)

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        try:
            sc = getattr(result, "structured_content", None)
            sc = sc if isinstance(sc, dict) else {}
            ah = sc.get("attestation_hash") or (sc.get("attestation") or {}).get("attestation_hash")
            as_of = sc.get("created_at") or _now()
            if self.forecast:
                rel = okf.for_forecast(attestation_hash=ah, as_of=as_of, score=0.3)
            else:
                rel = okf.for_attested_analysis(attestation_hash=ah, as_of=as_of, score=0.7)
            output_text = json.dumps(sc, sort_keys=True, default=str) if sc else None
            meta = dict(getattr(result, "meta", None) or {})
            meta["reliability"] = rel
            meta["io.modelcontextprotocol/integrity"] = okf.integrity(
                self.server_id, self.version, as_of, output_text)
            result.meta = meta
        except Exception:  # noqa: BLE001 — never break a tool result
            pass
        return result
