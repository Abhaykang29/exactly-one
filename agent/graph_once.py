"""The identical graph, with every tool call wrapped in run_effect()."""
from __future__ import annotations

import os

from agent.adapt import make_adapt_bank
from agent.decide import decide, to_dict
from agent.graph_baseline import build_graph
from agent.tools import World
from once import EffectSpec, run_effect
from once.config import db, tenant
from once.errors import Denied, Stopped
from once.grants import KILL


def make_process(world: World, run_id: str, grant_id: str):
    adapt_bank = make_adapt_bank(world)

    def process_one(order: dict, state: dict) -> dict:
        if KILL.is_set():
            raise Stopped("kill switch set between orders")
        oid = order["order_id"]
        base = f"{run_id}:{oid}"
        inputs = [{"kind": "order", "ref": oid, "trust": "system"},
                  {"kind": "customer_note", "text": order.get("note", ""), "trust": "untrusted"}]

        def spec(action: str, tool: str, amount: int, dec, act, verify, adapt=None) -> EffectSpec:
            return EffectSpec(key=f"{base}:{action}", run_id=run_id, tenant=tenant(), order_id=oid, tool=tool,
                              grant_id=grant_id, amount_cents=amount, decide=dec, act=act, verify=verify,
                              adapt=adapt, inputs=inputs)

        def frozen() -> dict:                       # restock/email read the refund receipt's frozen decision
            return db().effects.find_one({"_id": f"{base}:refund"})["decision"]

        try:
            r = run_effect(spec("refund", "bank.refund", order["amount_cents"],
                                lambda: to_dict(order, decide(order)),   # the LLM is called once per order, ever
                                world.bank_refund_act, world.bank_refund_verify, adapt_bank))
            receipts = [r]
            if r["status"] == "proven":
                if r["decision"].get("restock"):
                    receipts.append(run_effect(spec("restock", "inventory.restock", 0, frozen,
                                                    world.restock_act, world.restock_verify)))
                receipts.append(run_effect(spec("email", "email.send", 0, frozen,
                                                world.email_act, world.email_verify)))
        except Denied as e:
            print(f"[once] {oid} denied: {e}", flush=True)
            return {"held": [oid]}
        if all(x["status"] == "proven" for x in receipts):
            print(f"[once] {oid} done (all receipts proven)", flush=True)
            return {"done": [oid]}
        print(f"[once] {oid} held: {[x['_id'].split(':')[-1] + '=' + x['status'] for x in receipts]}", flush=True)
        return {"held": [oid]}

    return process_one


def make_graph(world: World, run_id: str, n_orders: int, proc_id: str, checkpointer, grant_id: str | None = None):
    grant_id = grant_id or os.environ.get("GRANT_ID", "g_ops_demo")
    return build_graph(world, n_orders, make_process(world, run_id, grant_id), proc_id, checkpointer)
