"""Shared /v1/reliability payload — each server self-proves by running the 12
portable conformance vectors through the vendored validator and returns honest
reference examples + the MCP `_meta` carrier shape. modelcontextprotocol#2964."""
from __future__ import annotations

import json
import datetime
from pathlib import Path

import okf_reliability as okf

_VECTORS = Path(__file__).resolve().parent / "okf_vectors.json"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _conformance() -> dict:
    try:
        import okf_validator as V
        vectors = json.loads(_VECTORS.read_text())["vectors"]
        passed = sum(
            ("valid" if all(p for _, p, _ in V.check(v["reliability"])) else "invalid") == v["expect"]
            for v in vectors)
        return {"passed": passed, "total": len(vectors), "green": passed == len(vectors)}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}


def reliability_payload(server_id: str, *, forecast: bool = False, version: str = "1.0.0") -> dict:
    now = _now()
    example = (okf.for_forecast(attestation_hash="…", as_of=now, score=0.3) if forecast
               else okf.for_attested_analysis(attestation_hash="…", as_of=now, score=0.7))
    return {
        "spec": "okf-reliability-v1",
        "schema": okf.SCHEMA_URL,
        "server": server_id,
        "cardinal_rule": "signed != verified — a MINT attestation proves integrity, not truth; "
                         "verified needs >=2 independent corroborators.",
        "emits_meta_on": "every MCP tool result (_meta.reliability + _meta['io.modelcontextprotocol/integrity'])",
        "reference_example": {"reliability": example},
        "mcp_meta_shape": okf.mcp_meta(example, server_id=server_id, server_version=version,
                                       produced_at=now, output_text="<tool output>"),
        "conformance": _conformance(),
        "specification": "modelcontextprotocol#2964",
        "reference_packet": "https://github.com/dynamicfeed/df-verify",
    }
