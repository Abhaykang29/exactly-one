"""Rollback as its own exactly-once effect: reverse a proven refund."""
from __future__ import annotations

import os

from once.config import db
from once.effects import EffectSpec, log, run_effect

ROLLBACK_GRANT = "g_manager_rollback"


def reverse(key: str, by: str, world_url: str | None = None) -> dict:
    from agent.tools import World

    orig = db().effects.find_one({"_id": key})
    if orig is None or orig.get("tool") != "bank.refund":
        raise ValueError(f"{key} is not a refund receipt")
    comp_key = "comp:" + key
    if orig["status"] == "compensated":
        return db().effects.find_one({"_id": comp_key}) or orig
    if orig["status"] != "proven":
        raise ValueError(f"{key} is {orig['status']}, only proven refunds can be reversed")

    w = World(world_url or os.environ.get("WORLD_ONCE_URL", "http://localhost:8002"))
    refund_id = orig["evidence"]["refund_id"]
    spec = EffectSpec(
        key=comp_key, run_id=orig["run_id"], tenant=orig.get("tenant", "maple"),
        order_id=orig["order_id"], tool="bank.reverse", grant_id=ROLLBACK_GRANT, amount_cents=0,
        decide=lambda: {"refund_id": refund_id, "reverses": key, "requested_by": by},
        act=w.bank_reverse_act, verify=w.bank_reverse_verify,
        inputs=[{"kind": "receipt", "ref": key, "trust": "system"}, {"kind": "manager", "ref": by, "trust": "human"}],
        compensates=key,
    )
    comp = run_effect(spec)
    if comp["status"] == "proven":
        r = db().effects.update_one({"_id": key, "status": "proven"},
                                    {"$set": {"status": "compensated", "compensated_by": comp_key}})
        if r.matched_count:
            log(key, "proven", "compensated", f"(by {comp_key})")
    return comp
