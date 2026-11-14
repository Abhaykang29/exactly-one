"""Rebalancing / offboarding / alerts agents in the same three styles as refunds. Rule-based decisions.

python -m agent.usecase_agent --usecase rebalancing --agent naive|textbook|once --run-id r1 --world-url ... --items 6
Exit 0 = finished, 137 = simulated crash (the supervisor restarts us with the same run id).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import TypedDict

os.environ["ONCE_CLIENT"] = "agent"

import httpx  # noqa: E402
from langgraph.checkpoint.mongodb import MongoDBSaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402

from agent.graph_baseline import report  # noqa: E402
from agent.usecases import USECASES, items  # noqa: E402
from once import EffectSpec, crash, run_effect  # noqa: E402
from once.config import agent_client, db, lg_db_name, tenant  # noqa: E402
from once.errors import Denied, Stopped, Unknown  # noqa: E402
from once.grants import now, start_kill_watcher  # noqa: E402


class S(TypedDict, total=False):
    orders: list[dict]          # the items (same field name as the refund graphs)
    i: int
    done: list[str]
    held: list[str]
    cur: dict | None


class System:
    def __init__(self, url: str, system: str):
        self.system = system
        self.http = httpx.Client(base_url=url.rstrip("/"), timeout=2.0)

    def act(self, decision: dict, key: str, field_map: dict | None) -> None:
        try:
            r = self.http.post(f"/{self.system}/actions", json={**{k: decision[k] for k in
                               ("item_id", "target", "amount_cents", "detail")}, "reference": key})
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise Unknown(type(e).__name__) from e
        if r.status_code >= 500:
            raise Unknown(f"HTTP {r.status_code}")
        r.raise_for_status()

    def verify(self, decision: dict, key: str) -> dict | None:
        try:
            rows = self.http.get(f"/{self.system}/actions", params={"reference": key}).json()
        except (httpx.TimeoutException, httpx.TransportError):
            return None
        if not rows:
            return None
        return {"record_id": rows[0]["record_id"], "amount_cents": rows[0]["amount_cents"],
                "checked_at": datetime.now(timezone.utc).isoformat()}


def rule_decide(item: dict) -> dict:
    return {k: item[k] for k in ("item_id", "target", "amount_cents", "detail")}


def _retry(fn, *a) -> None:
    for _ in range(3):                         # SDK-style: retry on timeout
        try:
            return fn(*a)
        except Unknown:
            continue


def build(style: str, uc: str, sysc: System, run_id: str, n: int, proc_id: str, grant_id: str, saver):
    cfg = USECASES[uc]
    action = cfg["action"]

    def key(item: dict) -> str:
        return f"{run_id}:{item['item_id']}:{action}"

    def load(state: S) -> dict:
        return {"orders": items(uc, n), "i": 0, "done": [], "held": [], "cur": None}

    def advance(state: S, ok: bool) -> dict:
        iid = state["orders"][state["i"]]["item_id"]
        new = {"i": state["i"] + 1, "cur": None,
               "done": state.get("done", []) + ([iid] if ok else []),
               "held": state.get("held", []) + ([] if ok else [iid])}
        report(proc_id, new)
        return new

    def route(state: S) -> str:
        return "process" if state["i"] < len(state["orders"]) else END

    g = StateGraph(S)
    g.add_node("load", load)
    g.add_edge(START, "load")

    if style == "naive":
        def process(state: S) -> dict:
            item = state["orders"][state["i"]]
            d = rule_decide(item)
            _retry(sysc.act, d, key(item), None)          # trust the 200
            crash.maybe_crash("after_act")
            return advance(state, True)
        g.add_node("process", process)
        g.add_conditional_edges("load", route, ["process", END])
        g.add_conditional_edges("process", route, ["process", END])

    elif style == "textbook":
        def process(state: S) -> dict:                    # decide, frozen by the checkpoint
            return {"cur": rule_decide(state["orders"][state["i"]])}

        def act(state: S) -> dict:                        # side effect in its own node
            _retry(sysc.act, state["cur"], key(state["orders"][state["i"]]), None)
            crash.maybe_crash("after_act")
            return {}

        def finish(state: S) -> dict:
            return advance(state, True)
        g.add_node("process", process)
        g.add_node("act", act)
        g.add_node("finish", finish)
        g.add_conditional_edges("load", route, ["process", END])
        g.add_edge("process", "act")
        g.add_edge("act", "finish")
        g.add_conditional_edges("finish", route, ["process", END])

    else:  # once
        def process(state: S) -> dict:
            from once.grants import KILL
            if KILL.is_set():
                raise Stopped("kill switch")
            item = state["orders"][state["i"]]
            try:
                r = run_effect(EffectSpec(
                    key=key(item), run_id=run_id, tenant=tenant(), order_id=item["item_id"], tool=cfg["tool"],
                    grant_id=grant_id, amount_cents=item["amount_cents"], decide=lambda: rule_decide(item),
                    act=sysc.act, verify=sysc.verify,
                    inputs=[{"kind": uc, "ref": item["item_id"], "trust": "system"}]))
            except Denied as e:
                print(f"[once] {item['item_id']} denied: {e}", flush=True)
                return advance(state, False)
            return advance(state, r["status"] == "proven")
        g.add_node("process", process)
        g.add_conditional_edges("load", route, ["process", END])
        g.add_conditional_edges("process", route, ["process", END])

    return g.compile(checkpointer=saver)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--usecase", required=True, choices=[k for k, v in USECASES.items() if "system" in v])
    p.add_argument("--agent", required=True, choices=["naive", "textbook", "once"])
    p.add_argument("--run-id", required=True)
    p.add_argument("--world-url", required=True)
    p.add_argument("--items", type=int, default=6)
    p.add_argument("--grant-id", default=None)
    p.add_argument("--proc-id", default=None)
    a = p.parse_args()
    proc_id = a.proc_id or f"{a.usecase}:{a.agent}"
    grant_id = a.grant_id or f"g_uc_{a.usecase}"
    db().processes.update_one({"_id": proc_id}, {"$set": {"pid": os.getpid(), "run_id": a.run_id, "agent": a.agent,
                                                         "usecase": a.usecase, "status": "running"}}, upsert=True)
    saver = MongoDBSaver(agent_client(), db_name=lg_db_name())
    config = {"configurable": {"thread_id": f"{a.run_id}:{a.usecase}:{a.agent}"}, "recursion_limit": 4 * a.items + 10}
    if a.agent == "once":
        start_kill_watcher(grant_id)
    graph = build(a.agent, a.usecase, System(a.world_url, USECASES[a.usecase]["system"]), a.run_id, a.items,
                  proc_id, grant_id, saver)
    resume = saver.get_tuple(config) is not None
    status = "finished"
    try:
        graph.invoke(None if resume else {"orders": [], "i": 0, "done": [], "held": []}, config, durability="sync")
    except Stopped:
        status = "stopped"
    st = graph.get_state(config).values
    db().processes.update_one({"_id": proc_id}, {"$set": {"status": status, "stopped_at": now(),
                                                         "claimed_done": st.get("done", []),
                                                         "held": st.get("held", [])}})
    print(f"[{a.usecase}:{a.agent}] {status}: {len(st.get('done', []))} done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
