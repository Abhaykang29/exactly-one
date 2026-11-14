"""The demo engine: run both agents side by side under a crash supervisor, then count from ground truth.

python -m scripts.race --orders 20 --crash-prob 0.15 [--agents baseline,once] [--chaos default|none] [--headless]
                       [--baseline-url ...] [--once-url ...] [--grant-id ...] [--decider llm|stub] [--drift]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time

import httpx

from once.config import admin_db
from once.grants import now
from scripts.seed_grant import budget_for, reset_demo, reset_grant

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CHAOS = [("bank", "timeout_after_commit", 0.15), ("inventory", "fake_ok", 0.15)]
MAX_RESTARTS = 300
MAX_ERRORS = 5


def post(url: str, path: str, body: dict) -> dict:
    r = httpx.post(url.rstrip("/") + path, json=body, timeout=10)
    r.raise_for_status()
    return r.json()


def prepare_world(url: str, orders: int, chaos: str, drift: bool) -> None:
    post(url, "/reset", {"orders": orders, "seed": 7})
    if chaos == "default":
        for system, mode, rate in DEFAULT_CHAOS:
            post(url, "/chaos", {"system": system, "mode": mode, "rate": rate, "enabled": True})
    if drift:
        post(url, "/chaos", {"system": "bank", "mode": "schema_v2", "rate": 1, "enabled": True})


def supervise(agent: str, run_id: str, url: str, orders: int, crash_prob: float, grant_id: str,
              proc_id: str, decider: str | None, out: dict, quiet: bool) -> None:
    d = admin_db()
    env = dict(os.environ, CRASH_PROB=str(crash_prob), GRANT_ID=grant_id, PYTHONUNBUFFERED="1")
    env.pop("ONCE_CLIENT", None)
    if decider:
        env["DECIDER"] = decider
    cmd = [sys.executable, "-m", "agent.run_agent", "--agent", agent, "--run-id", run_id, "--world-url", url,
           "--orders", str(orders), "--grant-id", grant_id, "--proc-id", proc_id]
    crashes = errors = 0
    status = "failed"
    while crashes <= MAX_RESTARTS:
        p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        tail: list[str] = []
        for line in p.stdout:
            tail = (tail + [line.rstrip()])[-20:]
            if not quiet:
                print(f"{agent:>8} | {line.rstrip()}", flush=True)
        code = p.wait()
        if code == 0:
            status = (d.processes.find_one({"_id": proc_id}) or {}).get("status", "finished")
            break
        if code in (137, -9):
            crashes += 1
            d.processes.update_one({"_id": proc_id}, {"$set": {"crashes": crashes}})
            continue
        errors += 1
        print(f"{agent:>8} | exited with code {code} (error {errors}/{MAX_ERRORS}); last lines:", flush=True)
        for t in tail[-8:]:
            print(f"{agent:>8} |   {t}", flush=True)
        if errors >= MAX_ERRORS:
            break
    d.processes.update_one({"_id": proc_id}, {"$set": {"status": status, "crashes": crashes, "pid": None}})
    out[agent] = {"crashes": crashes, "status": status, "errors": errors}


def compute(agent: str, run_id: str, url: str, orders: int, crashes: int, tag: str) -> dict:
    d = admin_db()
    truth = httpx.get(url.rstrip("/") + "/truth/summary", timeout=10).json()
    run = d.runs.find_one({"_id": f"{run_id}:{agent}"}) or {}
    claimed, held = run.get("claimed_done", []), set(run.get("held", []))
    per = truth["per_order"]
    reached = sorted(per)[: run.get("reached", orders)]
    restock_decided = {e["order_id"]: bool(e["decision"].get("restock", True))
                       for e in d.effects.find({"run_id": run_id, "tool": "bank.refund"},
                                               {"order_id": 1, "decision.restock": 1})}

    def missing_any(oid: str) -> bool:
        p = per.get(oid, {})
        need_restock = restock_decided.get(oid, True)
        return p.get("refunds", 0) == 0 or p.get("emails", 0) == 0 or (need_restock and p.get("restocks", 0) == 0)

    res = {
        "run_id": run_id, "agent": agent, "tag": tag, "orders": orders, "crashes": crashes,
        "dup_refunds": truth["dup_refunds"], "dup_restocks": truth["dup_restocks"],
        "dup_emails": truth["dup_emails"],
        "missed": sum(1 for o in reached if per[o]["refunds"] == 0 and o not in held),
        "false_done": sum(1 for o in claimed if missing_any(o)),
        "claimed_done": len(claimed), "held": len(held),
        "first_try_fails": d.effects.count_documents({"run_id": run_id, "first_try_failed": True})
        if agent == "once" else None,
        "created_at": now(),
    }
    return res


ROWS = [("crashes injected", "crashes"), ("refunds paid twice", "dup_refunds"),
        ("restocks done twice", "dup_restocks"), ("emails sent twice", "dup_emails"),
        ("said done, wasn't", "false_done"), ("missed refunds", "missed"),
        ("orders finished", "claimed_done"), ("held for a human", "held"),
        ("first-try schema fails", "first_try_fails")]


def table(results: dict) -> str:
    b, o = results.get("baseline", {}), results.get("once", {})
    lines = [f"{'':24}{'standard':>10}{'exactly-once':>15}"]
    for label, k in ROWS:
        def fmt(r):
            v = r.get(k)
            return "-" if v is None else str(v)
        lines.append(f"{label:24}{fmt(b):>10}{fmt(o):>15}")
    return "\n".join(lines)


def run_race(orders: int = 20, crash_prob: float = 0.15, agents=("baseline", "once"), chaos: str = "default",
             baseline_url: str | None = None, once_url: str | None = None, grant_id: str | None = None,
             decider: str | None = None, drift: bool = False, tag: str = "", quiet: bool = False,
             run_id: str | None = None) -> dict:
    urls = {"baseline": baseline_url or os.environ.get("WORLD_BASELINE_URL", "http://localhost:8001"),
            "once": once_url or os.environ.get("WORLD_ONCE_URL", "http://localhost:8002")}
    grant_id = grant_id or os.environ.get("GRANT_ID", "g_ops_demo")
    d = admin_db()
    for a in agents:
        prepare_world(urls[a], orders, chaos, drift)
    budget = budget_for([urls[a] for a in agents], orders)
    if grant_id == "g_ops_demo":
        reset_demo(budget_cents=budget)
    else:
        reset_grant(grant_id, budget_cents=budget)
    # chaos races get their own prefix so they can never share receipt keys with a demo race
    run_id = run_id or (f"c{int(time.time() * 1000)}" if tag else f"r{int(time.time())}")
    print(f"[race] {run_id}: {orders} orders, crash_prob {crash_prob}, chaos {chaos}"
          f"{', drift on' if drift else ''}, agents {','.join(agents)}, budget {budget}c", flush=True)
    for a in agents:
        d.processes.replace_one({"_id": tag + a}, {"_id": tag + a, "agent": a, "run_id": run_id, "crashes": 0,
                                                    "status": "starting", "pid": None, "world_url": urls[a],
                                                    "claimed_done": [], "held": [], "started_at": now()},
                                upsert=True)
    out: dict = {}
    threads = [threading.Thread(target=supervise, args=(a, run_id, urls[a], orders, crash_prob, grant_id,
                                                        tag + a, decider, out, quiet)) for a in agents]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    results = {}
    for a in agents:
        r = compute(a, run_id, urls[a], orders, out[a]["crashes"], tag)
        r["status"] = out[a]["status"]
        d.results.insert_one(dict(r))
        d.runs.update_one({"_id": f"{run_id}:{a}"}, {"$set": {"crashes": out[a]["crashes"]}})
        results[a] = r
    return results


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--orders", type=int, default=20)
    p.add_argument("--crash-prob", type=float, default=0.15)
    p.add_argument("--agents", default="baseline,once")
    p.add_argument("--chaos", choices=["default", "none"], default="default")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--baseline-url", default=None)
    p.add_argument("--once-url", default=None)
    p.add_argument("--grant-id", default=None)
    p.add_argument("--decider", choices=["llm", "stub"], default=None)
    p.add_argument("--drift", action="store_true", help="turn on bank schema_v2 after reset")
    p.add_argument("--tag", default="")
    a = p.parse_args()
    res = run_race(a.orders, a.crash_prob, tuple(a.agents.split(",")), a.chaos, a.baseline_url, a.once_url,
                   a.grant_id, a.decider, a.drift, a.tag)
    print("\n" + table(res), flush=True)


if __name__ == "__main__":
    main()
