"""Trust score computation for the MINT trust layer (Layer 7).

Trust is a 0–100 weighted blend of an actor's verified history:

    volume (30%)         more attested work  → more trusted   (saturates at 100)
    avg rating (30%)     quality of that work as rated by others
    consistency (10%)    low rating variance → reliable
    recommendations (15%) peer endorsements                    (saturates at 10)
    recency (15%)        recent activity matters more          (decays over 30d)

Core principle — ABSENCE OF DATA IS NOT NEGATIVE EVIDENCE. An actor with zero
attestations has *unknown* volume, not zero volume; unknown scores NEUTRAL (50),
not 0. Every axis sits at 50 when there's no data, so a brand-new actor scores
50. Positive evidence (attestations, good ratings, recommendations, recent
activity) pushes axes above 50; only genuinely negative evidence (low ratings,
high rating variance) pushes below. Consequence: a 5★ recommendation on a fresh
actor nudges trust ABOVE neutral — it can never drop an actor below 50.

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
    """Recency is a positive-only signal: unknown/stale ⇒ NEUTRAL (50), recent
    activity climbs toward 100. Dormancy is *absence* of recent positive signal,
    not negative evidence — so it floors at NEUTRAL, never 0."""
    dt = _parse_iso(last_active)
    if dt is None:
        return NEUTRAL          # no activity timestamp ⇒ unknown
    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    if age_days <= 0:
        return 100.0
    if age_days >= RECENCY_WINDOW_DAYS:
        return NEUTRAL          # stale ⇒ unknown current status, not negative
    return round(NEUTRAL + (1.0 - age_days / RECENCY_WINDOW_DAYS) * 50.0, 1)


def compute(n_attest: int, scores: list, n_recs: int,
            last_active: Optional[str], rec_scores: Optional[list] = None) -> float:
    """Pure scoring function — return 0–100.

    Principle (absence ≠ negative): every axis sits at NEUTRAL (50) when there's
    no data, so an actor with no history scores 50 — *unknown*, not bad. Positive
    evidence pushes an axis above 50; only genuinely negative evidence (low
    ratings, high rating variance) pushes below. Thus a 5★ recommendation on a
    brand-new actor nudges trust ABOVE neutral, and trust only drops below 50
    when real negative signal arrives.

      volume (30%)         50 (unknown) → 100 as attestations accumulate
      rating (30%)         50 (none); 5★→100, 3★→60, 1★→20  (can go below 50)
      consistency (10%)    50 (<2 ratings); low variance high, high variance dips
      recommendation (15%) 50 (none) → 100, scaled by endorsement quality
      recency (15%)        50 (unknown/stale) → 100 (active now)
    """
    total_ratings = len(scores)

    # Volume is positive-only: no attestations = UNKNOWN (50), not zero.
    volume = NEUTRAL + 50.0 * min(1.0, float(n_attest) / VOLUME_FULL)

    # Ratings are the one axis that carries negative evidence.
    rating = (sum(scores) / total_ratings / 5.0) * 100.0 if total_ratings else NEUTRAL

    if total_ratings >= 2:
        std = statistics.pstdev(scores)
        consistency = max(0.0, min(100.0, 100.0 - std * 20.0))
    else:
        consistency = NEUTRAL   # <2 ratings ⇒ variance unknown

    # Recommendations are positive-only endorsements: none = UNKNOWN (50), climbing
    # toward 100 with count, weighted by average endorsement score (5★ counts full).
    if n_recs:
        avg_quality = (sum(rec_scores) / len(rec_scores) / 5.0) if rec_scores else 1.0
        recommendation = NEUTRAL + 50.0 * min(1.0, float(n_recs) / REC_FULL) * avg_quality
    else:
        recommendation = NEUTRAL

    recency = _recency_score(last_active)

    trust = (volume * 0.30 + rating * 0.30 + consistency * 0.10
             + recommendation * 0.15 + recency * 0.15)
    return round(trust, 1)


async def recompute(mint_id: str) -> dict:
    """Gather every signal for `mint_id`, compute trust, persist to
    mint_trust_scores, and return the cached row. Best-effort: a Supabase blip
    logs and returns a neutral stub rather than raising."""
    try:
        # Attestations live in two disjoint stores: Forge's forge_trigger_executions
        # (the per-attestation on-chain path / kill switch) and mint_attestations
        # (the merkle-batch path, now the primary store). An attestation is in
        # exactly one, so summing the counts is exact and never double-counts.
        n_attest = await supa.attestation_count(mint_id) + await supa.mint_attestation_count(mint_id)
        ratings = await supa.ratings_for(mint_id)
        scores = [int(r["score"]) for r in ratings
                  if isinstance(r.get("score"), (int, float))]
        total_ratings = len(scores)
        avg_rating = round(sum(scores) / total_ratings, 2) if total_ratings else 0.0

        recs = await supa.recommendations_for(mint_id)
        n_recs = len(recs)
        rec_scores = [int(r["score"]) for r in recs
                      if isinstance(r.get("score"), (int, float))]
        n_recs_given = await supa.count_recommendations_given(mint_id)

        last_active = _max_iso(await supa.attestation_last_active(mint_id),
                               await supa.mint_attestation_last_active(mint_id))
        if ratings:
            last_active = _max_iso(last_active, ratings[0].get("created_at"))

        score = compute(n_attest, scores, n_recs, last_active, rec_scores)
        work_types = await supa.attestation_work_types(mint_id)
        for wt, c in (await supa.mint_attestation_work_types(mint_id)).items():
            work_types[wt] = work_types.get(wt, 0) + c

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
