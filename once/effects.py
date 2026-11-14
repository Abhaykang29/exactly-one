"""run_effect(): the five rules.

1. Write it down first (intent receipt before any action).
2. The database refuses doubles (_id is the idempotency key).
3. Decided once, then frozen (decision lives on the receipt).
4. Done means proven (verify against the real system).
5. After a crash, check first (a `sent` receipt is verified before acting again).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from pymongo.errors import DuplicateKeyError

from once import crash
from once.config import db
from once.errors import Denied, SchemaError, Stopped, Unknown
from once.grants import KILL, authorize_and_insert_intent, now

SETTLED = {"proven", "compensated", "denied", "held"}
VERIFY_DELAYS = [0.3, 0.8, 1.5]
MAX_ATTEMPTS = 3


@dataclass
class AdaptResult:
    field_map: dict
    memory_id: str | None = None
    candidate_id: str | None = None


@dataclass
class EffectSpec:
    key: str                     # run_id:order_id:action
    run_id: str
    tenant: str
    order_id: str
    tool: str                    # "bank.refund" etc.
    grant_id: str
    amount_cents: int            # 0 for non-money actions (the decision's amount_cents wins if present)
    decide: Callable[[], dict]   # called AT MOST ONCE per key, ever
    act: Callable[[dict, str, dict | None], None]
    verify: Callable[[dict, str], dict | None]
    adapt: Callable[[SchemaError | None, str], AdaptResult | None] | None = None
    inputs: list[dict] = field(default_factory=list)
    compensates: str | None = None


def log(key: str, a: str, b: str, extra: str = "") -> None:
    print(f"[once] {key} {a} -> {b}{(' ' + extra) if extra else ''}", flush=True)


def _get(key: str) -> dict:
    return db().effects.find_one({"_id": key})


def _evidence_ok(decision: dict, ev: dict) -> bool:
    want = decision.get("amount_cents")
    got = ev.get("amount_cents")
    return want is None or got is None or int(want) == int(got)


def mark_proven(key: str, ev: dict, reconciled: bool = False) -> dict:
    r = db().effects.update_one(
        {"_id": key, "status": "sent"},
        {"$set": {"status": "proven", "evidence": ev, "proven_at": now(), "reconciled": reconciled}},
    )
    if r.matched_count:
        log(key, "sent", "proven", f"({'reconciled after crash' if reconciled else 'verified'})")
    return _get(key)


def mark_held(key: str, reason: str) -> dict:
    r = db().effects.update_one(
        {"_id": key, "status": {"$in": ["intent", "sent"]}},
        {"$set": {"status": "held", "held_reason": reason}},
    )
    if r.matched_count:
        log(key, "sent", "held", f"({reason})")
    return _get(key)


def verify_with_backoff(verify, decision: dict, key: str, delays=None) -> dict | None:
    for d in (VERIFY_DELAYS if delays is None else delays):
        time.sleep(d)
        try:
            ev = verify(decision, key)
        except Unknown:
            ev = None
        if ev:
            return ev
    return None


def _insert_denied(spec: EffectSpec, decision: dict, reason: str) -> None:
    t = now()
    try:
        db().effects.insert_one({
            "_id": spec.key, "run_id": spec.run_id, "tenant": spec.tenant, "order_id": spec.order_id,
            "tool": spec.tool, "decision": decision, "inputs": spec.inputs,
            "authority": {"grant_id": spec.grant_id, "denied_reason": reason, "checked_at": t},
            "status": "denied", "held_reason": reason, "created_at": t,
        })
        log(spec.key, "none", "denied", f"({reason})")
    except DuplicateKeyError:
        pass


def _proven(spec: EffectSpec, doc: dict, ev: dict, reconciled: bool = False) -> dict:
    if not _evidence_ok(doc["decision"], ev):
        return mark_held(spec.key, f"amount mismatch: decided {doc['decision'].get('amount_cents')}, "
                                   f"system shows {ev.get('amount_cents')}")
    receipt = mark_proven(spec.key, ev, reconciled)
    from once import memory
    memory.on_proven(receipt)
    return receipt


def run_effect(spec: EffectSpec) -> dict:
    """Run one side effect exactly once. Returns the final receipt document."""
    E = db().effects
    key = spec.key
    doc = E.find_one({"_id": key})
    if doc and doc["status"] in SETTLED:
        return doc
    if KILL.is_set():
        raise Stopped(f"kill switch set before {key}")

    if doc is None:                                        # RULE 1: write it down first
        decision = spec.decide()                           # RULE 3: decided once, then frozen
        amount = int(decision.get("amount_cents", spec.amount_cents))
        try:
            authorize_and_insert_intent(spec, decision, amount)
            log(key, "none", "intent", f"(grant {spec.grant_id}, {amount}c)")
        except Denied as e:
            _insert_denied(spec, decision, str(e))
            raise
        except DuplicateKeyError:                          # RULE 2: database refuses doubles
            pass
        doc = E.find_one({"_id": key})
        if doc["status"] in SETTLED:
            return doc

    decision = doc["decision"]                             # always the frozen one

    if doc["status"] == "sent":                            # RULE 5: after a crash, check first
        try:
            ev = spec.verify(decision, key)
        except Unknown:
            ev = None
        if ev:
            return _proven(spec, doc, ev, reconciled=True)

    field_map = doc.get("field_map")
    used_memory: str | None = None
    if field_map is None and spec.adapt is not None:       # apply a remembered fix before the first try
        pre = spec.adapt(None, key)
        if pre is not None:
            field_map, used_memory = pre.field_map, pre.memory_id
            E.update_one({"_id": key}, {"$set": {"field_map": field_map},
                                        "$addToSet": {"memory_used": pre.memory_id}})

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if KILL.is_set():
            raise Stopped(f"kill switch during {key}")
        prev = doc["status"]
        cur = E.find_one_and_update(
            {"_id": key, "status": {"$in": ["intent", "sent"]}},
            {"$set": {"status": "sent", "sent_at": now()}, "$inc": {"attempts": 1}},
            return_document=True,
        )
        if cur is None:                                    # someone else settled it
            return _get(key)
        doc = cur
        log(key, prev, "sent", f"(attempt {cur['attempts']})")
        crash.maybe_crash("after_mark_sent")
        try:
            spec.act(decision, key, field_map)
        except SchemaError as e:
            if cur["attempts"] == 1:
                E.update_one({"_id": key}, {"$set": {"first_try_failed": True}})
            if used_memory:
                from once import memory
                memory.on_memory_failed(used_memory, key)
                E.update_one({"_id": key}, {"$addToSet": {"memory_failed": used_memory}})
            if spec.adapt is None:
                mark_held(key, f"schema error, no adapter: {e.body.get('error')}")
                raise
            r = spec.adapt(e, key)
            if r is None:
                return mark_held(key, "schema error, could not adapt")
            field_map, used_memory = r.field_map, r.memory_id
            upd: dict = {"$set": {"field_map": field_map}}
            if r.memory_id:
                upd["$addToSet"] = {"memory_used": r.memory_id}
            if r.candidate_id:
                upd["$set"]["candidate_id"] = r.candidate_id
            E.update_one({"_id": key}, upd)
            continue
        except Unknown as e:
            print(f"[once] {key} outcome unknown ({e}); verifying", flush=True)
        crash.maybe_crash("after_act")
        ev = verify_with_backoff(spec.verify, decision, key)   # RULE 4: done means proven
        if ev:
            return _proven(spec, doc, ev)

    receipt = mark_held(key, f"not confirmed after {MAX_ATTEMPTS} attempts")
    from once import memory
    memory.on_failed(receipt)
    return receipt
