"""Permission as one atomic write, revoke(), and the kill-switch watcher."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from once.config import admin_client, client, db, once_db_name
from once.errors import Denied

if TYPE_CHECKING:
    from once.effects import EffectSpec

KILL = threading.Event()


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime | None) -> datetime | None:
    if d is not None and d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc)
    return d


def intent_doc(spec: "EffectSpec", decision: dict, g: dict, amount: int) -> dict:
    t = now()
    return {
        "_id": spec.key,
        "run_id": spec.run_id,
        "tenant": spec.tenant,
        "order_id": spec.order_id,
        "tool": spec.tool,
        "decision": decision,
        "inputs": spec.inputs,
        "authority": {
            "grant_id": g["_id"],
            "issued_by": g.get("issued_by"),
            "amount_cents": amount,
            "authorized_at": t,
        },
        "memory_used": [],
        "memory_failed": [],
        "candidate_id": None,
        "field_map": None,
        "status": "intent",
        "attempts": 0,
        "first_try_failed": False,
        "evidence": None,
        "compensates": spec.compensates,
        "held_reason": None,
        "created_at": t,
        "sent_at": None,
        "proven_at": None,
    }


def _txn(s, spec: "EffectSpec", decision: dict, amount: int) -> None:
    d = s.client[once_db_name()]
    g = d.grants.find_one({"_id": spec.grant_id}, session=s)
    if g is None:
        raise Denied(f"grant {spec.grant_id} not found")
    if g.get("revoked"):
        raise Denied(f"grant {spec.grant_id} revoked by {g.get('revoked_by')}")
    exp = _aware(g.get("expires_at"))
    if exp is None or now() >= exp:
        raise Denied(f"grant {spec.grant_id} expired")
    if spec.tool not in g.get("tools", []):
        raise Denied(f"tool {spec.tool} not allowed by {spec.grant_id}")
    if amount > g.get("max_per_action_cents", 0):
        raise Denied(f"{amount} cents over per-action cap {g.get('max_per_action_cents')}")
    u = d.grant_usage.find_one_and_update(
        {"_id": spec.grant_id, "revoked": False,
         "spent_cents": {"$lte": g["budget_cents"] - amount}},
        {"$inc": {"spent_cents": amount, "uses": 1}},
        session=s,
    )
    if u is None:
        raise Denied("revoked or over budget")
    d.effects.insert_one(intent_doc(spec, decision, g, amount), session=s)


def authorize_and_insert_intent(spec: "EffectSpec", decision: dict, amount: int) -> None:
    """Check the grant, spend from grant_usage and insert the intent receipt in ONE transaction."""
    with client().start_session() as s:
        s.with_transaction(lambda s: _txn(s, spec, decision, amount))


def revoke(grant_id: str, by: str) -> datetime:
    """Admin: revoke a grant. Touches grant_usage too, so it conflicts with any in-flight spend."""
    t = now()

    def txn(s):
        d = s.client[once_db_name()]
        r = d.grants.update_one({"_id": grant_id},
                                {"$set": {"revoked": True, "revoked_at": t, "revoked_by": by}}, session=s)
        if r.matched_count == 0:
            raise ValueError(f"grant {grant_id} not found")
        d.grant_usage.update_one({"_id": grant_id}, {"$set": {"revoked": True}}, session=s)

    with admin_client().start_session() as s:
        s.with_transaction(txn)
    print(f"[once] REVOKE: {grant_id} by {by} at {t:%H:%M:%S}", flush=True)
    return t


def start_kill_watcher(grant_id: str) -> threading.Thread:
    """Daemon thread: sets KILL when the grant's `revoked` becomes true (change stream)."""
    ready = threading.Event()

    def fire(g: dict) -> None:
        at = g.get("revoked_at")
        when = at.strftime("%H:%M:%S") if at else "?"
        print(f"[once] KILL: grant revoked by {g.get('revoked_by')} at {when}", flush=True)
        KILL.set()

    def run() -> None:
        coll = db().grants
        pipeline = [{"$match": {"documentKey._id": grant_id,
                                "operationType": {"$in": ["update", "replace"]}}}]
        try:
            with coll.watch(pipeline, full_document="updateLookup") as stream:
                ready.set()
                g = coll.find_one({"_id": grant_id})
                if g and g.get("revoked"):
                    fire(g)
                    return
                for ch in stream:
                    fd = ch.get("fullDocument") or {}
                    if fd.get("revoked"):
                        fire(fd)
                        return
        except Exception as e:  # watcher must never die silently
            print(f"[once] kill watcher error: {e!r}", flush=True)
            ready.set()

    t = threading.Thread(target=run, name="kill-watcher", daemon=True)
    t.start()
    ready.wait(10)
    return t
