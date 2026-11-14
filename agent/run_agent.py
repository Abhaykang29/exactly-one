"""CLI: python -m agent.run_agent --agent baseline|once --run-id r42 --world-url http://localhost:8001 --orders 20

Agents connect to Atlas ONLY as agent_user (ONCE_CLIENT=agent -> MONGODB_URI_AGENT).
Exit 0 = finished (or stopped by the kill switch). Exit 137 = simulated crash; the supervisor restarts us.
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ["ONCE_CLIENT"] = "agent"

from langgraph.checkpoint.mongodb import MongoDBSaver  # noqa: E402

from agent.tools import World  # noqa: E402
from once.config import agent_client, db, lg_db_name  # noqa: E402
from once.errors import Stopped  # noqa: E402
from once.grants import now, start_kill_watcher  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--agent", choices=["baseline", "once"], required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--world-url", required=True)
    p.add_argument("--orders", type=int, default=20)
    p.add_argument("--grant-id", default=os.environ.get("GRANT_ID", "g_ops_demo"))
    p.add_argument("--proc-id", default=None, help="processes _id (default: the agent name)")
    a = p.parse_args()
    proc_id = a.proc_id or a.agent

    db().processes.update_one({"_id": proc_id}, {"$set": {
        "pid": os.getpid(), "run_id": a.run_id, "agent": a.agent, "started_at": now(), "status": "running"}},
        upsert=True)

    world = World(a.world_url)
    saver = MongoDBSaver(agent_client(), db_name=lg_db_name())
    thread = f"{a.run_id}:{a.agent}"
    config = {"configurable": {"thread_id": thread}, "recursion_limit": 2 * a.orders + 10}

    if a.agent == "once":
        from agent.graph_once import make_graph
        start_kill_watcher(a.grant_id)
        graph = make_graph(world, a.run_id, a.orders, proc_id, saver, a.grant_id)
    else:
        from agent.graph_baseline import make_graph
        graph = make_graph(world, a.run_id, a.orders, proc_id, saver)

    resume = saver.get_tuple(config) is not None
    print(f"[{a.agent}] {'resuming' if resume else 'starting'} {a.run_id} pid {os.getpid()}", flush=True)
    status = "finished"
    try:
        graph.invoke(None if resume else {"orders": [], "i": 0, "done": [], "held": []}, config,
                     durability="sync")
    except Stopped:
        print(f"[{a.agent}] stopped by kill switch", flush=True)
        status = "stopped"

    st = graph.get_state(config).values
    db().runs.update_one({"_id": f"{a.run_id}:{a.agent}"}, {"$set": {
        "run_id": a.run_id, "agent": a.agent, "orders": a.orders, "claimed_done": st.get("done", []),
        "held": st.get("held", []), "reached": st.get("i", 0), "status": status, "finished_at": now()}},
        upsert=True)
    db().processes.update_one({"_id": proc_id}, {"$set": {"status": status, "stopped_at": now(),
                                                          "claimed_done": st.get("done", []),
                                                          "held": st.get("held", [])}})
    print(f"[{a.agent}] {status}: {len(st.get('done', []))} done, {len(st.get('held', []))} held", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
