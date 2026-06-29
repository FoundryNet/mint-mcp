#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Reference validator for the OKF reliability object.
#
# Spec:   https://dynamicfeed.ai/schemas/okf-reliability-v1.json
# Origin: the reliability axis proposed in GoogleCloudPlatform/knowledge-catalog#151
#         (and in-toto/attestation#554). This is the runnable check that turns the
#         proposal's honesty rules into a property anyone can verify, with ZERO
#         dependencies (Python stdlib only) so it is trivial to run anywhere.
#
# The cardinal rule it enforces: SIGNED != VERIFIED. A signature proves integrity,
# not truth; `verified` is earned only by independent corroboration. The structural
# invariants below make that, and the rest of the honesty rules, checkable.
#
# Usage:
#   python3 verify_reliability.py <file.json>     # validate a reliability object,
#                                                 # a fact carrying `reliability`,
#                                                 # or a /v1/facts response
#   python3 verify_reliability.py --selftest      # run the built-in good/bad cases
# Exit code 0 = valid, 1 = invalid (or self-test failure).
from __future__ import annotations
import json
import sys

BANDS = {"HIGH", "MEDIUM", "LOW", "UNVERIFIED"}
BASES = {"live-source", "partner-attested", "vendor-doc", "forecast", "computed", "inferred"}
STATES = {"fresh", "stale", "unavailable"}
VANTAGES = {"independent", "producer-reported"}


def check(o: dict) -> list[tuple[str, bool, str]]:
    """Return [(invariant, passed, detail), ...] for one reliability object."""
    r: list[tuple[str, bool, str]] = []

    def ck(name, cond, detail=""):
        r.append((name, bool(cond), detail))
        return bool(cond)

    if not isinstance(o, dict):
        return [("is-object", False, "reliability is not an object")]

    conf = o.get("confidence")
    basis = o.get("basis")
    score = o.get("score")
    sources = o.get("sources")
    verified = o.get("verified")
    signals = o.get("signals") if isinstance(o.get("signals"), dict) else {}
    conflict = o.get("conflict") if isinstance(o.get("conflict"), dict) else None
    freshness = o.get("freshness") if isinstance(o.get("freshness"), dict) else {}

    # floor
    ck("confidence-band", conf in BANDS, f"confidence={conf!r}; must be one of {sorted(BANDS)}")
    ck("basis-enum", basis in BASES, f"basis={basis!r}; must be one of {sorted(BASES)}")

    # score / sources types
    if score is not None:
        ck("score-range", isinstance(score, (int, float)) and 0 <= score <= 1, f"score={score!r} must be 0..1")
    if sources is not None:
        ck("sources-int", isinstance(sources, int) and sources >= 0, f"sources={sources!r} must be int >= 0")

    # honesty rule: verified:true => sources >= 2 (corroboration, not a signature)
    if verified is True:
        ck("verified-needs-2-sources", isinstance(sources, int) and sources >= 2,
           f"verified=true but sources={sources!r} (must be >= 2)")

    # honesty rule: UNVERIFIED cannot be verified and cannot carry a high score
    if conf == "UNVERIFIED":
        ck("unverified-not-verified", verified is not True, "confidence=UNVERIFIED but verified=true")
        if score is not None:
            ck("unverified-low-score", score < 0.5, f"confidence=UNVERIFIED but score={score!r} (must be < 0.5)")

    # honesty rule: HIGH band cannot ride a near-zero computed score
    if conf == "HIGH" and score is not None:
        ck("high-coherent-score", score >= 0.5, f"confidence=HIGH but score={score!r} (must be >= 0.5)")

    # conflict object
    if conflict is not None:
        disputed = conflict.get("disputed")
        ck("conflict-disputed-bool", isinstance(disputed, bool), f"conflict.disputed={disputed!r} must be bool")
        if disputed is True:
            ck("disputed-not-verified", verified is not True, "conflict.disputed=true but verified=true")
            # honesty rule: a dispute is a corroboration failure, so the band caps at MEDIUM (HIGH excluded);
            # the prevailing position may carry up to MEDIUM, or a conservative producer may floor to LOW.
            ck("disputed-band-capped", conf in {"MEDIUM", "LOW", "UNVERIFIED"},
               f"conflict.disputed=true but confidence={conf!r} (a dispute caps the band at MEDIUM; HIGH excluded)")
            pos = conflict.get("positions")
            ck("disputed-two-positions", isinstance(pos, list) and len(pos) >= 2,
               "conflict.disputed=true requires >= 2 positions (retain both sides)")
            if isinstance(pos, list):
                for i, p in enumerate(pos):
                    ok = isinstance(p, dict) and p.get("statement") and p.get("basis") in BASES
                    ck(f"position[{i}]-shape", ok, f"position {i} needs statement + valid basis")
            ck("disputed-resolution", bool(conflict.get("resolution")),
               "conflict.disputed=true requires a resolution string")
        # signals.conflict, if present, must agree with the canonical conflict.disputed
        if "conflict" in signals and isinstance(disputed, bool):
            ck("signals-conflict-agrees", bool(signals.get("conflict")) == disputed,
               f"signals.conflict={signals.get('conflict')!r} disagrees with conflict.disputed={disputed!r}")

    # freshness state vocabulary (advisory)
    st = freshness.get("state")
    if st is not None:
        ck("freshness-state", st in STATES, f"freshness.state={st!r} must be one of {sorted(STATES)}")

    # observation vantage (orthogonal to reliability; corroboration != independence)
    vant = o.get("vantage")
    if vant is not None:
        ck("vantage-enum", vant in VANTAGES, f"vantage={vant!r} must be one of {sorted(VANTAGES)}")

    return r


def _extract(doc):
    """Accept a bare reliability object, a fact with `reliability`, or a /v1/facts response."""
    if isinstance(doc, dict):
        if "confidence" in doc and "basis" in doc:
            return doc
        if isinstance(doc.get("reliability"), dict):
            return doc["reliability"]
        facts = doc.get("facts")
        if isinstance(facts, list) and facts and isinstance(facts[0], dict) and isinstance(facts[0].get("reliability"), dict):
            return facts[0]["reliability"]
    return doc


def validate(doc) -> bool:
    obj = _extract(doc)
    results = check(obj)
    ok = all(p for _, p, _ in results)
    for name, passed, detail in results:
        mark = "ok  " if passed else "FAIL"
        print(f"  [{mark}] {name}" + (f"  — {detail}" if not passed and detail else ""))
    print(("VALID" if ok else "INVALID") + f"  ({sum(p for _,p,_ in results)}/{len(results)} invariants)")
    return ok


_GOOD = [
    {"confidence": "MEDIUM", "basis": "live-source", "score": 0.8, "sources": 1, "verified": False,
     "freshness": {"state": "fresh"}, "signals": {"signed": True, "corroborated": False, "fresh": True}},
    {"confidence": "HIGH", "basis": "live-source", "score": 0.95, "sources": 3, "verified": True,
     "signals": {"signed": True, "corroborated": True, "fresh": True}},
    {"confidence": "LOW", "basis": "live-source", "sources": 2, "verified": False,
     "conflict": {"disputed": True, "resolution": "live prevails; both kept",
                  "positions": [{"statement": "42", "basis": "live-source"}, {"statement": "11", "basis": "vendor-doc"}]},
     "signals": {"conflict": True}},
    {"confidence": "MEDIUM", "basis": "live-source", "sources": 2, "verified": False,                 # dispute capped at MEDIUM (clean live-source win)
     "conflict": {"disputed": True, "resolution": "live-source prevails under the trust ordering; both kept",
                  "positions": [{"statement": "42", "basis": "live-source"}, {"statement": "11", "basis": "vendor-doc"}]},
     "signals": {"conflict": True}},
    {"confidence": "LOW", "basis": "inferred"},
]
_BAD = [
    {"confidence": "HIGH", "basis": "live-source", "score": 0.9, "sources": 2, "verified": False,    # disputed cannot be HIGH
     "conflict": {"disputed": True, "resolution": "x",
                  "positions": [{"statement": "a", "basis": "live-source"}, {"statement": "b", "basis": "vendor-doc"}]},
     "signals": {"conflict": True}},
    {"confidence": "HIGH", "basis": "live-source", "verified": True, "sources": 0, "score": 0.9},   # verified w/o 2 sources
    {"confidence": "HIGH", "basis": "live-source", "score": 0.05},                                   # HIGH + low score
    {"confidence": "UNVERIFIED", "basis": "inferred", "verified": True},                             # UNVERIFIED + verified
    {"confidence": "LOW", "basis": "live-source", "conflict": {"disputed": True}},                   # disputed w/o positions
    {"confidence": "LOW", "basis": "live-source", "verified": False,
     "conflict": {"disputed": True, "resolution": "x",
                  "positions": [{"statement": "a", "basis": "live-source"}, {"statement": "b", "basis": "vendor-doc"}]},
     "signals": {"conflict": False}},                                                                # signals disagree
    {"confidence": "PRETTY_SURE", "basis": "live-source"},                                           # bad band
]


def selftest() -> bool:
    ok = True
    print("=== expect VALID ===")
    for i, g in enumerate(_GOOD):
        print(f"good[{i}]:")
        if not validate(g):
            ok = False
    print("\n=== expect INVALID ===")
    for i, b in enumerate(_BAD):
        print(f"bad[{i}]:")
        if validate(b):
            ok = False
            print("  !! should have been INVALID")
    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return ok


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--selftest":
        return 0 if selftest() else 1
    if len(argv) != 2:
        print(__doc__ or "usage: verify_reliability.py <file.json> | --selftest")
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        doc = json.load(fh)
    return 0 if validate(doc) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
