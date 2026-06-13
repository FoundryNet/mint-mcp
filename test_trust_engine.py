#!/usr/bin/env python3
"""Tests for the ported trust engine + ML scorer (sql/0003 server-side scoring).

Pure/offline — no Supabase, no network. Verifies:
  1. trust_engine integer math matches the on-chain Solana program (mint4.rs).
  2. apply_trust_delta reproduces the probation → ban state machine.
  3. ml_scorer is deterministic, range-bounded, and falls back safely.
  4. The packaged model loads and its feature order matches training order.

Run: python3 test_trust_engine.py   (exits non-zero on any failure)
"""
import json
import sys

import trust_engine as te
import ml_scorer as ml

_fail = 0


def check(name, got, exp):
    global _fail
    if got == exp:
        print(f"  ✅ PASS  {name}")
    else:
        _fail += 1
        print(f"  ❌ FAIL  {name}: got={got!r} exp={exp!r}")


print("[1] trust_engine: math ported from mint4.rs")
check("normalize_complexity(1500,1000)", te.normalize_complexity(1500, 1000), 1500)
check("normalize clamps high → 2000", te.normalize_complexity(3000, 1000), 2000)
check("normalize clamps low → 500", te.normalize_complexity(100, 1000), 500)
check("normalize network_avg=0 → SCALE", te.normalize_complexity(1500, 0), 1500)
check("network_avg empty window → 1000", te.network_avg_complexity(0, 0), 1000)
check("network_avg clamps to [500,2000]", te.network_avg_complexity(6000, 2), 2000)
check("warmup(0) = 500 (50%)", te.warmup_multiplier(0), 500)
check("warmup(15) = 750", te.warmup_multiplier(15), 750)
check("warmup(30) = 1000 (100%)", te.warmup_multiplier(30), 1000)
check("warmup caps at 1000", te.warmup_multiplier(999), 1000)
# README invariant: 1h @ 1.0x complexity @ warmup 1.0 = 18 MINT = 18_000_000 micro
check("base_score(3600,1000,1000) = 18e6", te.compute_base_score(3600, 1000, 1000), 18_000_000)
check("trust_weighted @100 = base", te.trust_weighted_score(18_000_000, 100), 18_000_000)
check("trust_weighted @50 = base/2", te.trust_weighted_score(18_000_000, 50), 9_000_000)
check("trust_weighted on probation = 0", te.trust_weighted_score(18_000_000, 100, on_probation=True), 0)

print("[2] apply_trust_delta: probation → ban state machine")
r = te.apply_trust_delta(100, -5)
check("healthy decrement", (r["new_trust"], r["on_probation"], r["is_banned"]), (95, False, False))
r = te.apply_trust_delta(3, -5, was_on_probation=False, probation_count=0, now_iso="T")
check("hit 0 first time → probation (count 1)",
      (r["new_trust"], r["on_probation"], r["is_banned"], r["probation_count"]), (0, True, False, 1))
r = te.apply_trust_delta(3, -5, was_on_probation=True, probation_count=1, now_iso="T")
check("hit 0 again on probation → BAN", (r["new_trust"], r["is_banned"]), (0, True))
r = te.apply_trust_delta(0, 1, was_on_probation=True, probation_count=1)
check("recover clears probation", (r["new_trust"], r["on_probation"], r["probation_started_at"]), (1, False, None))

print("[3] ml_scorer: determinism, ranges, fallback")
att = {"work_type": "code_review", "duration_seconds": 1800, "complexity_claimed": 1500,
       "input_hash": "a" * 64, "output_hash": "b" * 64, "summary": "reviewed PR"}
agent = {"trust_score": 94, "job_count": 40, "total_duration": 72000, "complexity_sum": 60000}
net = {"window_jobs": 120, "window_complexity_sum": 150000, "window_duration": 600000}
a, b = ml.score_attestation(att, agent, net), ml.score_attestation(att, agent, net)
check("deterministic", a, b)
conf, delta = a
check("ml_confidence in [0,1000]", 0 <= conf <= 1000, True)
check("trust_delta in [-5,3]", -5 <= delta <= 3, True)
# rule-based fallback path (also the shape used when the model can't load)
rb = ml._rule_based_score({"work_type": "research", "duration_seconds": 1,
                           "input_hash": "x" * 64, "output_hash": "x" * 64}, {}, {})
check("fallback deterministic", rb, ml._rule_based_score(
    {"work_type": "research", "duration_seconds": 1, "input_hash": "x" * 64, "output_hash": "x" * 64}, {}, {}))
check("fallback flags 1s dup-hash research (delta<0)", rb[1] < 0, True)

print("[4] packaged model integrity")
feats = json.load(open("models/model_features.json"))
check("model loaded (gbm)", ml.model_loaded(), True)
try:
    import joblib
    m = joblib.load("models/model.pkl")
    check("feature order == training order", feats == list(getattr(m, "feature_names_in_", [])), True)
except Exception as e:
    check("feature order == training order", f"load error: {e}", True)

print("\n======================================================")
print("  ALL PASS ✅" if _fail == 0 else f"  {_fail} FAILURE(S) ❌")
print("======================================================")
sys.exit(1 if _fail else 0)
