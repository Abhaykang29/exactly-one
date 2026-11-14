"""The standard LangGraph agent: naive tool calls, no receipts, no verification.

build_graph() is shared with graph_once.py so both agents have the identical shape:
load -> process (one order per step) -> process ... -> END, checkpointed by MongoDBSaver.
"""
from __future__ import annotations

from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from agent.decide import decide, to_dict
from agent.tools import World
from once import crash
from once.config import db
from once.errors import SchemaError, Unknown


class State(TypedDict, total=False):
    orders: list[dict]
    i: int
    done: list[str]
    held: list[str]


def report(proc_id: str, state: dict) -> None:
    """Live progress for the console (same for both agents)."""
    db().processes.update_one({"_id": proc_id}, {"$set": {
        "claimed_done": state.get("done", []), "held": state.get("held", []), "i": state.get("i", 0)}})


def build_graph(world: World, n_orders: int, process_one: Callable[[dict, dict], dict], proc_id: str, checkpointer):
    def load(state: State) -> dict:
        return {"orders": world.orders(n_orders), "i": 0, "done": [], "held": []}

    def process(state: State) -> dict:
        order = state["orders"][state["i"]]
        upd = process_one(order, state)
        new = {"i": state["i"] + 1,
               "done": state.get("done", []) + upd.get("done", []),
               "held": state.get("held", []) + upd.get("held", [])}
        report(proc_id, new)
        return new

    def route(state: State) -> str:
        return "process" if state["i"] < len(state["orders"]) else END

    g = StateGraph(State)
    g.add_node("load", load)
    g.add_node("process", process)
    g.add_edge(START, "load")
    g.add_conditional_edges("load", route, ["process", END])
    g.add_conditional_edges("process", route, ["process", END])
    return g.compile(checkpointer=checkpointer)


def make_process(world: World, run_id: str):
    def process_one(order: dict, state: dict) -> dict:
        oid = order["order_id"]
        d = to_dict(order, decide(order))           # re-decided on every re-run of this node
        key = f"{run_id}:{oid}"
        for attempt in range(3):                    # retry up to 2 times on timeout/5xx
            try:
                world.bank_refund_act(d, f"{key}:refund", None)
                break
            except Unknown:
                print(f"[baseline] {oid} refund timed out, retrying", flush=True)
            except SchemaError as e:
                print(f"[baseline] {oid} refund rejected by bank: {e.body.get('error')}; skipping", flush=True)
                return {"held": [oid]}
        crash.maybe_crash("after_refund")
        if d["restock"]:
            try:
                world.restock_act(d, f"{key}:restock", None)   # trust the 200
            except Unknown:
                print(f"[baseline] {oid} restock failed, moving on", flush=True)
        crash.maybe_crash("after_restock")
        try:
            world.email_act(d, f"{key}:email", None)
        except Unknown:
            print(f"[baseline] {oid} email failed, moving on", flush=True)
        crash.maybe_crash("after_email")
        print(f"[baseline] {oid} done", flush=True)
        return {"done": [oid]}

    return process_one


def make_graph(world: World, run_id: str, n_orders: int, proc_id: str, checkpointer):
    return build_graph(world, n_orders, make_process(world, run_id), proc_id, checkpointer)
