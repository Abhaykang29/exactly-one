"""Best-practice LangGraph, per its durable-execution guidance.

Same load -> process shape and MongoDBSaver (durability="sync") as the other two agents, but every side effect
is its own node, so a checkpoint lands between actions and the decision is frozen in graph state:

    load -> decide -> refund -> restock -> email -> (next order) decide ... -> END

Each action retries on timeout (as HTTP SDKs do by default), passes the key as `reference` (the bank ignores it,
like many real systems), and trusts a 200. What it cannot do: know whether an action that crashed before its node
checkpointed already landed. LangGraph re-runs that node on resume.
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agent.decide import decide, to_dict
from agent.graph_baseline import report
from agent.tools import World
from once import crash
from once.errors import NeedsHuman, SchemaError, Unknown


class TState(TypedDict, total=False):
    orders: list[dict]
    i: int
    done: list[str]
    held: list[str]
    cur: dict | None          # the current order's decision, frozen by the checkpoint after `decide`


def _retry(fn, *args, tries: int = 3) -> None:
    for _ in range(tries):
        try:
            return fn(*args)
        except Unknown:
            continue


def make_graph(world: World, run_id: str, n_orders: int, proc_id: str, checkpointer):
    def key(state, action: str) -> str:
        return f"{run_id}:{state['orders'][state['i']]['order_id']}:{action}"

    def load(state: TState) -> dict:
        return {"orders": world.orders(n_orders), "i": 0, "done": [], "held": [], "cur": None}

    def decide_node(state: TState) -> dict:
        order = state["orders"][state["i"]]
        try:
            return {"cur": to_dict(order, decide(order))}
        except NeedsHuman as e:
            print(f"[textbook] {order['order_id']} {e.reason}", flush=True)
            return {"cur": None}

    def refund(state: TState) -> dict:
        d = state["cur"]
        try:
            _retry(world.bank_refund_act, d, key(state, "refund"), None)
        except SchemaError as e:
            print(f"[textbook] {d['order_id']} refund rejected by bank: {e.body.get('error')}", flush=True)
            return {"cur": {**d, "rejected": True}}
        crash.maybe_crash("after_refund")
        return {}

    def restock(state: TState) -> dict:
        d = state["cur"]
        if d.get("restock"):
            _retry(world.restock_act, d, key(state, "restock"), None)       # trust the 200
        crash.maybe_crash("after_restock")
        return {}

    def email(state: TState) -> dict:
        d = state["cur"]
        _retry(world.email_act, d, key(state, "email"), None)
        crash.maybe_crash("after_email")
        return {}

    def finish(state: TState) -> dict:
        d, oid = state["cur"], state["orders"][state["i"]]["order_id"]
        ok = d is not None and not d.get("rejected")
        new = {"i": state["i"] + 1, "cur": None,
               "done": state.get("done", []) + ([oid] if ok else []),
               "held": state.get("held", []) + ([] if ok else [oid])}
        if ok:
            print(f"[textbook] {oid} done", flush=True)
        report(proc_id, new)
        return new

    def route_next(state: TState) -> str:
        return "decide" if state["i"] < len(state["orders"]) else END

    def after_decide(state: TState) -> str:
        return "refund" if state.get("cur") else "finish"

    def after_refund(state: TState) -> str:
        return "finish" if state["cur"].get("rejected") else "restock"

    g = StateGraph(TState)
    for name, fn in (("load", load), ("decide", decide_node), ("refund", refund), ("restock", restock),
                     ("email", email), ("finish", finish)):
        g.add_node(name, fn)
    g.add_edge(START, "load")
    g.add_conditional_edges("load", route_next, ["decide", END])
    g.add_conditional_edges("decide", after_decide, ["refund", "finish"])
    g.add_conditional_edges("refund", after_refund, ["restock", "finish"])
    g.add_edge("restock", "email")
    g.add_edge("email", "finish")
    g.add_conditional_edges("finish", route_next, ["decide", END])
    return g.compile(checkpointer=checkpointer)
