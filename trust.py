"""Trust score computation for the MINT trust layer (Layer 7).

Trust is a 0–100 weighted blend of an actor's verified history:

    volume (30%)         more attested work  → more trusted   (saturates at 100)
    avg rating (30%)     quality of that work as rated by others
    consistency (10%)    low rating variance → reliable
    recommendations (15%) peer endorsements                    (saturates at 10)
    recency (15%)        recent activity matters more          (decays over 30d)

A brand-new actor with no attestations, ratings, or recommendations scores a
neutral 50 (matching the seeded default) — not 0. Once it has ANY signal the
blend takes over. Unknown sub-signals (e.g. no ratings yet) contribute a neutral
50 for their component rather than a misleading 0 or 100.

Recompute is cheap and runs on every new rating/recommendation; the result is
cached in mint_trust_scores and served by mint_verify / mint_discover.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from typing import Optional

import supa

logger = logging.getLogger("mint.trust")

NEUTRAL = 50.0
RECENCY_WINDOW_DAYS = 30.0
VOLUME_FULL = 100        # attestations for a full volume score
REC_FULL = 10            # recommendations for a full recommendation score


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _max_iso(a: Optional[str], b: Optional[str]) -> Optional[str]:
    da, db = _parse_iso(a), _parse_iso(b)
    if da is None:
        return b
    if db is None:
        return a
    return a if da >= db else b


def _recency_score(last_active: Optional[str]) -> float:
    dt = _parse_iso(last_active)
    if dt is None:
        return NEUTRAL          # no activity timestamp ⇒ unknown, neutral
    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    if age_days <= 0:
        return 100.0
    if age_days >= RECENCY_WINDOW_DAYS:
        return 0.0
    return round((1.0 - age_days / RECENCY_WINDOW_DAYS) * 100.0, 1)


def compute(n_attest: int, scores: list, n_recs: int,
            last_active: Optional[str]) -> float:
    """Pure scoring function — given the gathered signals, return 0–100."""
    total_ratings = len(scores)

    # No signal at all ⇒ neutral starting point (don't punish new actors).
    if n_attest == 0 and total_ratings == 0 and n_recs == 0:
        return NEUTRAL

    volume = min(100.0, float(n_attest) / VOLUME_FULL * 100.0)

    rating = (sum(scores) / total_ratings / 5.0) * 100.0 if total_ratings else NEUTRAL

    if total_ratings >= 2:
        std = statistics.pstdev(scores)
        consistency = max(0.0, min(100.0, 100.0 - std * 20.0))
    else:
        consistency = NEUTRAL   # <2 ratings ⇒ variance unknown

    recommendation = min(100.0, float(n_recs) * (100.0 / REC_FULL))
    recency = _recency_score(last_active)

    trust = (volume * 0.30 + rating * 0.30 + consistency * 0.10
             + recommendation * 0.15 + recency * 0.15)
    return round(trust, 1)


async def recompute(mint_id: str) -> dict:
    """Gather every signal for `mint_id`, compute trust, persist to
    mint_trust_scores, and return the cached row. Best-effort: a Supabase blip
    logs and returns a neutral stub rather than raising."""
    try:
        n_attest = await supa.attestation_count(mint_id)
        ratings = await supa.ratings_for(mint_id)
        scores = [int(r["score"]) for r in ratings
                  if isinstance(r.get("score"), (int, float))]
        total_ratings = len(scores)
        avg_rating = round(sum(scores) / total_ratings, 2) if total_ratings else 0.0

        recs = await supa.recommendations_for(mint_id)
        n_recs = len(recs)
        n_recs_given = await supa.count_recommendations_given(mint_id)

        last_active = await supa.attestation_last_active(mint_id)
        if ratings:
            last_active = _max_iso(last_active, ratings[0].get("created_at"))

        score = compute(n_attest, scores, n_recs, last_active)
        work_types = await supa.attestation_work_types(mint_id)

        fields = {
            "trust_score": score,
            "total_attestations": n_attest,
            "total_ratings": total_ratings,
            "avg_rating": avg_rating,
            "total_recommendations_received": n_recs,
            "total_recommendations_given": n_recs_given,
            "work_types": work_types,
            "last_active": last_active,
            "computed_at": _now_iso(),
        }
        res = await supa.upsert_trust(mint_id, fields)
        if "error" in res:
            logger.warning(f"trust upsert failed for {mint_id}: {res}")
        return {"mint_id": mint_id, **fields}
    except Exception as e:
        logger.warning(f"trust.recompute failed for {mint_id}: {e}")
        return {"mint_id": mint_id, "trust_score": NEUTRAL, "error": str(e)}


async def profile(mint_id: str, local: Optional[dict] = None) -> dict:
    """Full trust profile for mint_verify. Reads the cached trust row + actor
    directory + recent ratings/recommendations. If no cached row exists yet
    (e.g. first verify after registration), computes one on the fly."""
    trust = await supa.get_trust(mint_id)
    actor = await supa.get_actor(mint_id)
    if trust is None:
        trust = await recompute(mint_id)

    ratings = await supa.ratings_for(mint_id, limit=10)
    recs = await supa.recommendations_for(mint_id, limit=10)
    given = await supa.count_recommendations_given(mint_id)

    name = (actor or {}).get("name") or (local or {}).get("name")
    actor_type = (actor or {}).get("actor_type") or (local or {}).get("actor_type")
    capabilities = (actor or {}).get("capabilities") or (local or {}).get("capabilities") or []

    return {
        "mint_id": mint_id,
        "registered": actor is not None or local is not None,
        "name": name,
        "actor_type": actor_type,
        "capabilities": capabilities,
        "operator": (actor or {}).get("operator") or (local or {}).get("operator"),
        "mcp_endpoint": (actor or {}).get("mcp_endpoint"),
        "description": (actor or {}).get("description"),
        "trust_score": trust.get("trust_score", NEUTRAL),
        "total_attestations": trust.get("total_attestations", 0),
        "avg_rating": trust.get("avg_rating", 0),
        "total_ratings": trust.get("total_ratings", 0),
        "recommendations_received": trust.get("total_recommendations_received", 0),
        "recommendations_given": given,
        "work_types": trust.get("work_types", {}),
        "first_seen": (actor or {}).get("registered_at"),
        "last_active": trust.get("last_active"),
        "recent_ratings": [
            {"score": r.get("score"), "tags": r.get("tags", []),
             "comment": r.get("comment"), "from": r.get("rater_mint_id"),
             "at": r.get("created_at")}
            for r in ratings
        ],
        "recent_recommendations": [
            {"from": r.get("recommender_mint_id"), "context": r.get("context"),
             "score": r.get("score"), "note": r.get("note"), "at": r.get("created_at")}
            for r in recs
        ],
        "verification": "on-chain",
        "verifiable": True,
    }
