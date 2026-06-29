"""okf-reliability-v1 emitter — the reliability axis, honest by construction.

The cardinal rule this enforces at the SOURCE: signed != verified. A MINT
attestation proves integrity (the bytes are unaltered, by whom, when) — it does
NOT make a claim `verified`. `verified` is earned only by >=2 INDEPENDENT
corroborators. Every object this module emits passes the df-verify conformance
validator (okf-reliability-v1); see okf_validator.py + okf_vectors.json.

Spec/schema: https://dynamicfeed.ai/schemas/okf-reliability-v1.json
Reference packet: df-verify/reliability/mcp-extension-reference.md (MCP #2964).
"""
from __future__ import annotations

import hashlib

SCHEMA_URL = "https://dynamicfeed.ai/schemas/okf-reliability-v1.json"
OBJ_TYPE = "okf-reliability-v1"

BANDS = {"HIGH", "MEDIUM", "LOW", "UNVERIFIED"}
BASES = {"live-source", "partner-attested", "vendor-doc", "forecast", "computed", "inferred"}


def build(*, basis: str, attested: bool = False, sources: int = 1,
          corroborated: bool = False, independent: bool = False, fresh: bool = True,
          forecast: bool = False, as_of: str | None = None,
          score: float | None = None, conflict: dict | None = None) -> dict:
    """Construct a reliability object that ALWAYS satisfies the honesty invariants.

    - `attested` (a MINT anchor) → signals.signed=true, never `verified`.
    - `verified` is granted ONLY with corroborated + independent + sources>=2 + fresh.
    - `forecast` (a prediction about the future) → UNVERIFIED until an outcome lands.
    - band is capped: disputed→MEDIUM, single-source-but-signed→MEDIUM, else LOW.
    """
    if basis not in BASES:
        basis = "computed"
    sources = max(int(sources or 0), 0)
    signed = bool(attested)
    disputed = bool(conflict and conflict.get("disputed"))
    verified = bool(corroborated and independent and sources >= 2 and fresh
                    and not forecast and not disputed)
    vantage = "independent" if independent else "producer-reported"

    if forecast:
        confidence = "UNVERIFIED"
    elif disputed:
        confidence = "MEDIUM"            # a dispute caps the band at MEDIUM
    elif verified:
        confidence = "HIGH"
    elif signed or fresh:
        confidence = "MEDIUM"            # signed/fresh but uncorroborated → MEDIUM ceiling
    else:
        confidence = "LOW"

    obj: dict = {
        "type": OBJ_TYPE,
        "confidence": confidence,
        "basis": basis,
        "sources": sources,
        "verified": verified,
        "vantage": vantage,
        "signals": {"signed": signed, "corroborated": bool(corroborated), "fresh": bool(fresh)},
    }
    if score is not None:
        try:
            s = max(0.0, min(1.0, float(score)))
        except (TypeError, ValueError):
            s = None
        if s is not None:
            if confidence == "UNVERIFIED":
                s = min(s, 0.49)          # UNVERIFIED cannot carry a high score
            elif confidence == "HIGH":
                s = max(s, 0.5)           # HIGH must be coherent
            obj["score"] = round(s, 3)
    if as_of:
        obj["freshness"] = {"as_of": as_of, "state": "fresh" if fresh else "stale"}
    if conflict:
        obj["conflict"] = conflict
        obj["signals"]["conflict"] = disputed
    return obj


# ── FoundryNet-specific honest mappings ───────────────────────────────────────
def for_attested_analysis(*, attestation_hash: str | None, as_of: str | None = None,
                          score: float | None = None, basis: str = "computed") -> dict:
    """A single-agent MINT-attested analysis (risk/threat composite). Signed and
    fresh, but single producer-side source → MEDIUM, verified:false, producer-reported."""
    return build(basis=basis, attested=bool(attestation_hash), sources=1,
                 corroborated=False, independent=False, fresh=True,
                 as_of=as_of, score=score)


def for_forecast(*, attestation_hash: str | None, as_of: str | None = None,
                 score: float | None = None) -> dict:
    """A probability about a future event (OddsCheck thesis / pre-resolution). A
    signed forecast is still UNVERIFIED — it cannot be verified until an outcome lands."""
    return build(basis="forecast", attested=bool(attestation_hash), sources=1,
                 corroborated=False, independent=False, fresh=True,
                 forecast=True, as_of=as_of, score=score)


def for_resolved_prediction(*, attestation_hash: str | None, as_of: str | None = None,
                            score: float = 0.9) -> dict:
    """An OddsCheck call corroborated by the actual market resolution — a genuinely
    INDEPENDENT observer. This is the one case FoundryNet can honestly call verified."""
    return build(basis="live-source", attested=bool(attestation_hash), sources=2,
                 corroborated=True, independent=True, fresh=True, as_of=as_of, score=score)


# ── MCP _meta carrier (modelcontextprotocol#2964) ─────────────────────────────
def integrity(server_id: str, server_version: str, produced_at: str,
              output_text: str | None = None) -> dict:
    o = {"serverId": server_id, "serverVersion": server_version, "producedAt": produced_at}
    if output_text is not None:
        o["outputSha256"] = hashlib.sha256(output_text.encode("utf-8")).hexdigest()
    return o


def mcp_meta(reliability_obj: dict, *, server_id: str, server_version: str,
             produced_at: str, output_text: str | None = None) -> dict:
    """The `_meta` block for an MCP tool result: integrity + reliability as
    separable siblings (the three orthogonal axes)."""
    return {
        "io.modelcontextprotocol/integrity": integrity(server_id, server_version, produced_at, output_text),
        "reliability": reliability_obj,
    }


def mcp_tool_result(text: str, reliability_obj: dict, *, server_id: str,
                    server_version: str, produced_at: str) -> dict:
    """A full MCP tool-result envelope carrying the reliability metadata in `_meta`."""
    return {
        "content": [{"type": "text", "text": text}],
        "_meta": mcp_meta(reliability_obj, server_id=server_id, server_version=server_version,
                          produced_at=produced_at, output_text=text),
    }
