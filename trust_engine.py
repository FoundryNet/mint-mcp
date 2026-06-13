"""Trust scoring engine — a faithful server-side port of the on-chain Solana
program (4ZvTZ3…AKL: record_job / update_trust / settle_job).

This is the EXACT integer math the on-chain program runs, lifted into the app
layer so every merkle-batch attestation can be quality-scored before it's
recorded + anchored — with NO on-chain calls. All functions are pure and
deterministic (integer floor-division, mirroring Rust u64/u128 ops), so the
same inputs always produce the same score.

Mapping to the on-chain source (mint4.rs):
  normalize_complexity      ← fn normalize_complexity
  network_avg_complexity    ← fn calculate_network_avg_complexity
  warmup_multiplier         ← fn calculate_warmup
  compute_base_score        ← record_job base_reward_micro math
  apply_trust_delta         ← update_trust probation/ban state machine
  trust_weighted_score      ← settle_job final_reward_micro math
  should_rotate_window      ← fn maybe_rotate_window (7-day window)

Note: the on-chain MachineState.trust_score is u8 and NetworkState counters are
u64; Python ints are unbounded so there is no overflow to mirror — the floor
division semantics match exactly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# ── Constants (verbatim from mint4.rs) ────────────────────────────────────────
BASE_RATE_MICRO = 5000
TRUST_START = 100
WARMUP_JOBS = 30
MIN_COMPLEXITY = 500
MAX_COMPLEXITY = 2000
COMPLEXITY_SCALE = 1000
WINDOW_SECONDS = 7 * 24 * 60 * 60   # rolling weekly window (maybe_rotate_window)


def normalize_complexity(claimed: int, network_avg: int) -> int:
    """Normalize a claimed complexity against the network average, clamped to
    [500, 2000]. Port of fn normalize_complexity. network_avg==0 ⇒ COMPLEXITY_SCALE
    (the on-chain calculate_network_avg_complexity returns SCALE for an empty window)."""
    if network_avg == 0:
        network_avg = COMPLEXITY_SCALE
    norm = (int(claimed) * COMPLEXITY_SCALE) // int(network_avg)
    return max(MIN_COMPLEXITY, min(MAX_COMPLEXITY, norm))


def network_avg_complexity(window_complexity_sum: int, window_jobs: int) -> int:
    """Average claimed complexity over the current window, clamped [500, 2000].
    Port of fn calculate_network_avg_complexity — SCALE (1000) when the window is
    empty, otherwise window_complexity_sum / window_jobs clamped to the valid range."""
    if not window_jobs:
        return COMPLEXITY_SCALE
    avg = int(window_complexity_sum) // int(window_jobs)
    return max(MIN_COMPLEXITY, min(MAX_COMPLEXITY, avg))


def warmup_multiplier(job_count: int) -> int:
    """50% at job 0, scaling linearly to 100% at job 30 (returned ×1000, so
    500..1000). Port of fn calculate_warmup."""
    progress = min(int(job_count) * COMPLEXITY_SCALE // WARMUP_JOBS, COMPLEXITY_SCALE)
    return 500 + (500 * progress // COMPLEXITY_SCALE)


def compute_base_score(duration_seconds: int, normalized_complexity: int, warmup: int) -> int:
    """Base quality score (in micro-MINT units, exactly the on-chain base_reward_micro):
        duration * BASE_RATE_MICRO * normalized_complexity * warmup / (SCALE * SCALE)
    Port of the record_job base-reward computation."""
    return (int(duration_seconds) * BASE_RATE_MICRO * int(normalized_complexity) * int(warmup)) \
        // (COMPLEXITY_SCALE * COMPLEXITY_SCALE)


def apply_trust_delta(current_trust: int, delta: int, *,
                      was_on_probation: bool = False,
                      probation_count: int = 0,
                      now_iso: Optional[str] = None) -> dict:
    """Apply a trust delta and run the on-chain probation/ban state machine
    (update_trust). Returns the full set of fields to persist.

    The spec's 3-value signature is extended here because the probation/ban
    transitions genuinely need the PRIOR on_probation flag and probation_count
    (exactly as the on-chain MachineState carries them):

      new_trust = clamp(current + delta, 0, 100)
      new_trust == 0:
        - not previously on probation → enter probation (count += 1, stamp time)
        - already on probation        → BAN (repeat zero-trust)
      new_trust > 0 and was on probation → RECOVER (clear probation)

    Returns: {new_trust, on_probation, is_banned, probation_count, probation_started_at}
    """
    new_trust = max(0, min(100, int(current_trust) + int(delta)))
    on_probation = bool(was_on_probation)
    is_banned = False
    new_probation_count = int(probation_count)
    probation_started_at: Optional[str] = None  # None ⇒ leave/clear; set ⇒ stamp

    if new_trust == 0:
        if not was_on_probation:
            on_probation = True
            new_probation_count += 1
            probation_started_at = now_iso or _now_iso()
        else:
            # second zero-trust while already on probation → ban (on_probation
            # stays True, probation_started_at/count unchanged — mirrors on-chain)
            is_banned = True
            probation_started_at = "__keep__"
    elif was_on_probation:
        # recovered: trust climbed back above zero
        on_probation = False
        probation_started_at = None
    else:
        probation_started_at = "__keep__"  # no probation change

    return {
        "new_trust": new_trust,
        "on_probation": on_probation,
        "is_banned": is_banned,
        "probation_count": new_probation_count,
        "probation_started_at": probation_started_at,  # "__keep__" ⇒ caller leaves as-is
    }


def trust_weighted_score(base_score: int, trust_score: int, *, on_probation: bool = False) -> int:
    """Final, trust-adjusted score (settle_job final_reward_micro):
        base_score * (trust_score * 10) / 1000
    A probationary agent settles to 0 (on-chain settle_job returns reward 0 while
    on_probation), so we mirror that here."""
    if on_probation:
        return 0
    trust_multiplier = int(trust_score) * 10
    return (int(base_score) * trust_multiplier) // COMPLEXITY_SCALE


def clamp_complexity(claimed: int) -> int:
    """Clamp a claimed complexity to the protocol range [500, 2000] (the on-chain
    record_job require!()). Work-type → complexity comes from core._WORK_COMPLEXITY."""
    return max(MIN_COMPLEXITY, min(MAX_COMPLEXITY, int(claimed)))


# ── network window rotation (maybe_rotate_window) ─────────────────────────────

def should_rotate_window(window_start_iso: Optional[str], now: Optional[datetime] = None) -> bool:
    """True when the rolling window is older than 7 days and should reset.
    Port of fn maybe_rotate_window's age check."""
    if not window_start_iso:
        return False
    start = _parse_iso(window_start_iso)
    if start is None:
        return False
    ref = now or datetime.now(timezone.utc)
    return (ref - start).total_seconds() > WINDOW_SECONDS


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None
