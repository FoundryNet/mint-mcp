"""mint_verify — query any actor's identity, trust score, and verified work.

FREE. Reputation lookups are never gated — open verification is what makes the
trust score worth anything. This is the call an agent makes before trusting an
unfamiliar agent.

Source of truth is the relay's /history/{mint_id}: total jobs, average trust,
earnings, and the recent settled jobs (each with a Solscan verify_url). The
actor's semantic label (type/name/work-type breakdown) comes from the in-process
actor registry when available; a miss is reported honestly as unknown — the
on-chain history is still fully verifiable.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

import actor_registry
import mint_client


def _relative(ts: Optional[str]) -> Optional[str]:
    """Render an ISO timestamp as a coarse 'N ago' string."""
    if not ts:
        return None
    try:
        t = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=_dt.timezone.utc)
        delta = _dt.datetime.now(_dt.timezone.utc) - t
        secs = int(delta.total_seconds())
        if secs < 0:
            return "just now"
        if secs < 60:
            return f"{secs} seconds ago"
        if secs < 3600:
            return f"{secs // 60} minutes ago"
        if secs < 86400:
            return f"{secs // 3600} hours ago"
        return f"{secs // 86400} days ago"
    except Exception:
        return ts


def register(mcp) -> None:
    @mcp.tool
    async def mint_verify(
        mint_id: Optional[str] = None,
        actor_name: Optional[str] = None,
        actor_type: Optional[str] = None,
    ) -> dict:
        """Look up an actor's trust profile: identity, current trust score,
        total verified attestations, work-type breakdown, and the most recent
        on-chain attestations (each with a Solscan verify_url any party can
        independently check).

        FREE — reputation queries are never gated.

        Provide EITHER `mint_id` directly, OR `actor_name` (optionally with
        `actor_type`) to resolve a known actor by name. Name resolution is
        best-effort within this server instance; when in doubt pass the mint_id.

        Args:
            mint_id: the actor's MINT id ("MINT-xxxxxx").
            actor_name: the actor's registered name, e.g. "ResearchBot-7".
            actor_type: optional disambiguator when resolving by name.
        """
        local: Optional[dict] = None

        if not mint_id:
            if not actor_name:
                return {"error": "bad_request",
                        "detail": "Provide either mint_id or actor_name."}
            found = actor_registry.find_by_name(actor_name, actor_type)
            if not found:
                return {"error": "not_found",
                        "detail": f"No mint_id known on this instance for actor_name={actor_name!r}"
                                  f"{f' actor_type={actor_type!r}' if actor_type else ''}. "
                                  "Pass the mint_id directly to verify off-instance actors.",
                        "verifiable": True}
            mint_id, local = found
        else:
            local = actor_registry.lookup(mint_id)

        if not (mint_id or "").startswith("MINT-"):
            return {"error": "bad_request",
                    "detail": f"mint_id must look like 'MINT-xxxxxx', got {mint_id!r}"}

        if not mint_client.configured():
            # Without the relay key we can't read trust/jobs. Be explicit rather
            # than fabricate a reputation.
            return {"error": "not_configured",
                    "detail": "MINT_RELAY_KEY is not set; cannot read on-chain trust/history.",
                    "mint_id": mint_id,
                    "registered": local is not None,
                    "actor_type": (local or {}).get("actor_type"),
                    "name": (local or {}).get("name"),
                    "verifiable": True}

        hist = await mint_client.history(mint_id, limit=10)
        if "error" in hist:
            detail = hist.get("detail")
            registered = not (str(hist.get("error")).endswith("404"))
            return {"error": "lookup_failed", "detail": detail,
                    "mint_id": mint_id, "registered": registered,
                    "verifiable": registered}

        summary = hist.get("summary") or {}
        jobs = hist.get("jobs") or []

        avg_trust = summary.get("average_trust")
        trust_score = round(avg_trust) if isinstance(avg_trust, (int, float)) else None

        recent = [{
            "attestation_id": j.get("job_id"),
            "timestamp":      j.get("settled_at"),
            "duration_seconds": j.get("duration_seconds"),
            "reward":         j.get("final_reward"),
            "verify_url":     j.get("verify_url"),
        } for j in jobs]

        last_active = _relative(jobs[0].get("settled_at")) if jobs else None

        return {
            "mint_id":            mint_id,
            "registered":         True,
            "actor_type":         (local or {}).get("actor_type"),
            "name":               (local or {}).get("name"),
            "capabilities":       (local or {}).get("capabilities", []),
            "operator":           (local or {}).get("operator"),
            "trust_score":        trust_score,
            "total_attestations": summary.get("total_jobs"),
            "total_earned":       summary.get("total_earned"),
            "last_active":        last_active,
            "work_types":         (local or {}).get("work_types", {}),
            "recent_attestations": recent,
            "verification":       "on-chain",
            "verifiable":         True,
            "note": (
                "Trust + attestation totals are read live from the MINT relay; "
                "every recent_attestations.verify_url is an independent Solscan "
                "anchor. actor_type/name/work_types are labels from this server "
                "instance and may be null for actors first seen elsewhere — the "
                "on-chain record is still fully verifiable."
            ),
        }
