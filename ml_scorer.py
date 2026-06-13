"""ML confidence scoring — the oracle replacement, server-side.

On-chain, an off-chain oracle (foundrynet-ml-api/app.py) watched record_job events
and called update_trust(ml_confidence, trust_delta). We now compute those two
numbers ourselves, in-process, before an attestation is recorded — no webhook, no
on-chain call.

PRIMARY PATH: the production GBM (models/model.pkl — scikit-learn
GradientBoostingClassifier, 200 trees, depth 5, 37 features, v4_20251207, ROC-AUC
0.990) loaded ONCE at import (i.e. server startup), never per request. It outputs
P(anomalous); we map that to (ml_confidence, trust_delta).

FALLBACK PATH: if the model can't load (missing file / sklearn version mismatch)
or inference raises, we drop to a deterministic rule-based scorer and, as a final
guard, to (ml_confidence=500, trust_delta=0). The attestation is NEVER blocked on
a scorer failure (see core._attest_batched).

Public interface (stable so a future model swaps in without touching the flow):
    score_attestation(attestation, agent_state, network_state) -> (ml_confidence, trust_delta)
      ml_confidence : int 0–1000
      trust_delta   : int, clamped to [-5, +3]

DETERMINISM: scoring is a pure function of its inputs, with ONE intentional
exception — the two anti-gaming rate features (time_since_last_job,
jobs_last_hour_machine). time_since_last_job is a wall-clock delta against
agent_state["last_job_at"]; jobs_last_hour_machine is a live Supabase count injected
by core. Both are RELATIVE quantities (a delta and a rolling count), not absolute
timestamps, so a single scoring call is internally consistent and reproducible from
its inputs (the same last_job_at + the same injected count → the same score). There
is no RNG anywhere.

FEATURE FIDELITY: build_features mirrors foundrynet-ml-api/app.py:build_features
exactly, including its `features.get(name, 0)` behavior — features the model lists
but app.py never populated (complexity_per_minute, complexity_per_second) are fed
0.0 here too, matching what the deployed model actually received. Documented gaps
(defaulted, because the app-layer state doesn't carry them) are noted at each line.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("mint.ml_scorer")

_HERE = Path(__file__).resolve().parent
_MODEL_PATH = _HERE / "models" / "model.pkl"
_FEATURES_PATH = _HERE / "models" / "model_features.json"
_METADATA_PATH = _HERE / "models" / "model_metadata.json"

# Engine ↔ model scale: the trust engine uses integer complexity 500–2000; the GBM
# was trained on a complexity *multiplier* (~0.5–2.0). Divide by this to convert.
_COMPLEXITY_SCALE = 1000.0
_BASE_RATE = 0.005          # app.py BASE_RATE (== BASE_RATE_MICRO/1e6)
_MIN_MULT, _MAX_MULT = 0.5, 2.0
_WARMUP_JOBS = 30

# Fallback defaults (the hard guarantee on any failure).
_FALLBACK_CONFIDENCE = 500
_FALLBACK_DELTA = 0

# ml_confidence semantics. The spec is internally inconsistent: section 4 + the
# receipt example describe ml_confidence as "how confident the work happened"
# (high = good), while the inline snippet writes int(P(anomalous)*1000) (high =
# bad). We default to the human-facing semantic the SDK receipt shows
# (confidence = (1 - P(anomalous)) * 1000). Set ML_CONFIDENCE_IS_ANOMALY=true to
# report the literal P(anomalous)*1000 instead. trust_delta is unaffected.
_CONFIDENCE_IS_ANOMALY = os.environ.get(
    "ML_CONFIDENCE_IS_ANOMALY", "false").strip().lower() in ("1", "true", "yes", "on")

# Minimum plausible duration (seconds) per work type — used by the rule-based
# fallback's duration-sanity heuristic. A research task in 1s is suspicious.
_MIN_PLAUSIBLE = {
    "research": 30, "analysis": 20, "generation": 10, "code_review": 15,
    "manufacturing": 5, "normalization": 3, "delivery": 2, "custom": 3,
}


def _load_model():
    """Load the GBM + feature list once. Returns (model, feature_names, metadata) or
    (None, feature_names_or_[], {}) on failure — never raises."""
    feature_names: list = []
    metadata: dict = {}
    try:
        feature_names = json.loads(_FEATURES_PATH.read_text())
    except Exception as e:
        logger.warning(f"ml_scorer: could not read {_FEATURES_PATH.name}: {e}")
    try:
        metadata = json.loads(_METADATA_PATH.read_text())
    except Exception:
        metadata = {}
    try:
        import joblib  # imported lazily so a missing dep degrades, not crashes
        model = joblib.load(_MODEL_PATH)
        if not feature_names:
            feature_names = list(getattr(model, "feature_names_in_", []) or [])
        logger.info(f"ml_scorer: loaded GBM {metadata.get('version','?')} "
                    f"({len(feature_names)} features) from {_MODEL_PATH.name}")
        return model, feature_names, metadata
    except Exception as e:
        logger.warning(f"ml_scorer: model load failed ({e!r}) — using rule-based fallback")
        return None, feature_names, metadata


# Loaded ONCE at import (server startup). Inference is sub-ms on a 200-tree GBM.
_MODEL, _FEATURES, _METADATA = _load_model()


def model_loaded() -> bool:
    return _MODEL is not None


def model_info() -> dict:
    return {
        "loaded": _MODEL is not None,
        "version": _METADATA.get("version"),
        "features": len(_FEATURES),
        "scorer": "gbm" if _MODEL is not None else "rule_based",
        "confidence_semantic": "anomaly" if _CONFIDENCE_IS_ANOMALY else "work_happened",
    }


# ── feature construction (mirrors app.py:build_features) ───────────────────────

def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_iso(s) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# Default gap (seconds) when an agent has no prior job — a generous "not rushing"
# value so a first job isn't penalized as suspiciously fast.
_NO_PRIOR_JOB_GAP = 3600.0


def _seconds_since(last_job_at) -> float:
    """Wall-clock seconds since the agent's previous job (time_since_last_job).
    None / unparseable last_job_at ⇒ first job ⇒ _NO_PRIOR_JOB_GAP (3600). A
    relative delta, not an absolute timestamp — see the DETERMINISM note."""
    dt = _parse_iso(last_job_at)
    if dt is None:
        return _NO_PRIOR_JOB_GAP
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def build_features(attestation: dict, agent_state: dict, network_state: dict) -> dict:
    """Build the model's feature dict from the attestation + Supabase agent/network
    state. Mirrors foundrynet-ml-api/app.py:build_features key-for-key. Values the
    app layer can't supply use deterministic defaults (gaps flagged inline)."""
    # complexity arrives in engine scale (500–2000); the model expects a multiplier.
    c = _f(attestation.get("complexity_claimed", 1000)) / _COMPLEXITY_SCALE
    dur = _f(attestation.get("duration_seconds", 300))

    job_count = int(_f(agent_state.get("job_count", 0)))
    total_duration = _f(agent_state.get("total_duration", 0))
    complexity_sum = _f(agent_state.get("complexity_sum", 0))
    machine_total_earned = 0.0   # GAP: mint_agents doesn't track earnings (no token economy here)

    # Anti-gaming rate features (now wired, not defaulted):
    #  - time_since_last_job: wall-clock seconds since this agent's previous job.
    #  - jobs_last_hour_machine: rolling 1h attestation count, injected by core from
    #    a Supabase query (build_features stays sync). 0 when not supplied.
    time_since_last_job = _seconds_since(agent_state.get("last_job_at"))
    jobs_last_hour = _f(attestation.get("jobs_last_hour_machine", 0))

    # Network window aggregates (model scale).
    win_jobs = _f(network_state.get("window_jobs", 0))
    win_csum = _f(network_state.get("window_complexity_sum", 0))
    win_dur = _f(network_state.get("window_duration", 0))
    net_avg_c = (win_csum / win_jobs / _COMPLEXITY_SCALE) if win_jobs else 1.0
    net_avg_d = (win_dur / win_jobs) if win_jobs else 500.0
    net_std_c = 0.3    # GAP: per-job variance not stored (app.py default)
    net_std_d = 300.0  # GAP: per-job variance not stored (app.py default)

    # Machine stats: app.py uses network stats until the machine has ≥10 jobs.
    if job_count >= 10:
        mach_avg_c = (complexity_sum / job_count / _COMPLEXITY_SCALE) if job_count else net_avg_c
        mach_avg_d = (total_duration / job_count) if job_count else net_avg_d
        mach_std_c = net_std_c   # GAP: variance not stored
        mach_std_d = net_std_d   # GAP: variance not stored
    else:
        mach_avg_c, mach_avg_d, mach_std_c, mach_std_d = net_avg_c, net_avg_d, net_std_c, net_std_d

    warmup = 0.5 + 0.5 * min(1.0, job_count / _WARMUP_JOBS)
    normalized = max(_MIN_MULT, min(_MAX_MULT, (c / net_avg_c) if net_avg_c else 1.0))
    rew = dur * _BASE_RATE * normalized * warmup   # reward_gross (model scale)

    return {
        "complexity_claimed": c, "duration_seconds": dur, "duration_minutes": dur / 60,
        "reward_gross": rew, "reward_per_second": rew / (dur + 1), "reward_per_complexity": rew / (c + 0.1),
        "network_avg_complexity": net_avg_c, "network_avg_duration": net_avg_d,
        "network_complexity_std": net_std_c, "network_duration_std": net_std_d,
        "jobs_in_window": win_jobs if win_jobs else 100,   # GAP: app.py default 100
        "days_since_launch": 1.0,                            # GAP: deterministic default (no wall-clock)
        "activity_ratio": 1.0,                              # constant in app.py
        "decay_multiplier": 1.0,                            # age 0 at attest time
        "warmup_multiplier": warmup,
        "machine_job_count": job_count,
        "machine_total_earned": machine_total_earned,
        "machine_avg_complexity": mach_avg_c, "machine_avg_duration": mach_avg_d,
        "machine_complexity_std": mach_std_c, "machine_duration_std": mach_std_d,
        "complexity_vs_network": c - net_avg_c, "complexity_vs_machine": c - mach_avg_c,
        "complexity_zscore_network": (c - net_avg_c) / (net_std_c + 0.01),
        "complexity_zscore_machine": (c - mach_avg_c) / (mach_std_c + 0.01),
        "duration_vs_network": dur - net_avg_d, "duration_vs_machine": dur - mach_avg_d,
        "duration_zscore_network": (dur - net_avg_d) / (net_std_d + 1),
        "duration_zscore_machine": (dur - mach_avg_d) / (mach_std_d + 1),
        "time_since_last_job": time_since_last_job,   # wired: wall-clock delta vs last_job_at
        "jobs_last_hour_machine": jobs_last_hour,     # wired: rolling 1h count (injected by core)
        "is_new_machine": 1 if job_count < 10 else 0,
        "complexity_duration_ratio": c / (dur / 60 + 0.1),
        "reward_efficiency": rew / (dur + 1),
        "earning_rate": machine_total_earned / (job_count + 1),
    }


def _delta_from_anomaly(p_anom: float) -> int:
    """Map P(anomalous) → trust_delta (verbatim from the spec's model snippet)."""
    if p_anom > 0.70:
        return -5
    if p_anom > 0.50:
        return -2
    if p_anom < 0.25:
        return 1
    return 0


def _confidence_from_anomaly(p_anom: float) -> int:
    """ml_confidence in 0–1000. Default = work-happened confidence = (1-p)*1000."""
    val = p_anom if _CONFIDENCE_IS_ANOMALY else (1.0 - p_anom)
    return max(0, min(1000, int(round(val * 1000))))


def _rule_based_score(attestation: dict, agent_state: dict, network_state: dict) -> Tuple[int, int]:
    """Deterministic heuristic scorer — the fallback when the GBM is unavailable.
    Builds an anomaly estimate from duration sanity + hash signals, then reuses the
    same anomaly→(confidence, delta) mapping as the model path."""
    wtype = str(attestation.get("work_type") or "custom").strip().lower()
    dur = _f(attestation.get("duration_seconds", 0))
    in_h = (attestation.get("input_hash") or "").strip()
    out_h = (attestation.get("output_hash") or "").strip()

    suspicion = 0.0
    floor = _MIN_PLAUSIBLE.get(wtype, 3)
    if dur < floor:
        suspicion += 0.5            # implausibly fast for this work type
    elif dur < 2 * floor:
        suspicion += 0.15
    if in_h and out_h and in_h == out_h:
        suspicion += 0.4            # output identical to input → no real transformation
    if not in_h and not out_h:
        suspicion += 0.1            # nothing to verify against
    suspicion = max(0.0, min(1.0, suspicion))

    return _confidence_from_anomaly(suspicion), _delta_from_anomaly(suspicion)


def score_attestation(attestation: dict, agent_state: dict,
                      network_state: Optional[dict] = None) -> Tuple[int, int]:
    """Return (ml_confidence 0–1000, trust_delta clamped [-5, +3]).

    GBM if loaded, else the deterministic rule-based scorer, else the hard fallback
    (500, 0). Never raises — a scorer failure must not block an attestation."""
    network_state = network_state or {}
    try:
        if _MODEL is not None and _FEATURES:
            import warnings
            import numpy as np
            feats = build_features(attestation, agent_state, network_state)
            # _FEATURES order == the model's training order (verified), so positional
            # inference is correct; suppress sklearn's "no feature names" notice.
            vec = [_f(feats.get(name, 0.0)) for name in _FEATURES]   # matches app.py .get(f, 0)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                p_anom = float(_MODEL.predict_proba(np.array([vec], dtype=float))[0][1])
            delta = max(-5, min(3, _delta_from_anomaly(p_anom)))
            return _confidence_from_anomaly(p_anom), delta
        # No model → deterministic rules.
        conf, delta = _rule_based_score(attestation, agent_state, network_state)
        return conf, max(-5, min(3, delta))
    except Exception as e:
        logger.warning(f"ml_scorer.score_attestation failed ({e!r}) — fallback (500, 0)")
        return _FALLBACK_CONFIDENCE, _FALLBACK_DELTA
