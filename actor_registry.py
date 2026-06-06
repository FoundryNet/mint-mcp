"""Best-effort in-process map of mint_id → actor descriptor.

The relay is the authoritative source for trust + job history, but it does not
store an actor's semantic identity (type, name, capabilities) — that's a
MCP-layer concept. We stash it here on register/attest so mint_verify can echo a
richer profile within a live process.

This is deliberately NOT a database: it resets on redeploy and is empty for
mint_ids first seen on another instance. mint_verify treats a miss as "unknown,
still verifiable on-chain" rather than an error — the on-chain history is the
real proof; this cache is only a convenience label. Keep it lean (the build
brief is explicit: no DB, no dashboard).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Optional

_LOCK = threading.Lock()
_MAX = 10_000
_actors: "OrderedDict[str, dict]" = OrderedDict()
# mint_id -> {actor_type, name, capabilities, operator, work_types: {type: n}}


def remember(mint_id: str, *, actor_type: str, name: str,
             capabilities: Optional[list] = None,
             operator: Optional[str] = None) -> None:
    if not mint_id:
        return
    with _LOCK:
        entry = _actors.get(mint_id) or {"work_types": {}}
        entry.update({
            "actor_type":   actor_type,
            "name":         name,
            "capabilities": capabilities or entry.get("capabilities") or [],
            "operator":     operator if operator is not None else entry.get("operator"),
        })
        _actors[mint_id] = entry
        _actors.move_to_end(mint_id)
        while len(_actors) > _MAX:
            _actors.popitem(last=False)


def record_work(mint_id: str, work_type: str) -> None:
    if not mint_id or not work_type:
        return
    with _LOCK:
        entry = _actors.get(mint_id)
        if entry is None:
            return
        wt = entry.setdefault("work_types", {})
        wt[work_type] = wt.get(work_type, 0) + 1
        _actors.move_to_end(mint_id)


def lookup(mint_id: str) -> Optional[dict]:
    with _LOCK:
        entry = _actors.get(mint_id)
        return dict(entry) if entry else None


def find_by_name(name: str, actor_type: Optional[str] = None) -> Optional[tuple[str, dict]]:
    """Reverse lookup mint_id from (name[, actor_type]). Best-effort; first match."""
    with _LOCK:
        for mid, entry in reversed(_actors.items()):
            if entry.get("name") == name and (actor_type is None or entry.get("actor_type") == actor_type):
                return mid, dict(entry)
    return None
