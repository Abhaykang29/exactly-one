"""All four use cases x three agent styles at once, under crash supervisors.

python -m scripts.race_all --items 6 --crash-prob 0.2 [--headless]
Refunds use the existing flow (Muse Wonder decides); rebalancing, offboarding and alerts use rule-based decisions.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from agent.usecases import GENERIC, USECASES, items
from once.config import admin_db
from once.grants import now
from scripts.race import DEFAULT_CHAOS, ROOT, post, supervise
from scripts.seed_grant import budget_for, reset_demo, reset_usecase_grant

STYLES = ["naive", "textbook", "once"]
REFUND_AGENT = {"naive": "baseline", "textbook": "textbook", "once": "once"}
MAX_RESTARTS, MAX_ERRORS = 300, 5


def worlds() -> dict:
    return {"naive": os.environ.get("WORLD_BASELINE_URL", "http://localhost:8001"),
            "textbook": os.environ.get("WORLD_TEXTBOOK_URL", "http://localhost:8003"),
            "once": os.environ.get("WORLD_ONCE_URL", "http://localhost:8002")}


def proc_id(uc: str, style: str) -> str:
    return REFUND_AGENT[style] if uc == "refunds" else f"{uc}:{style}"


def reset_all(n: int) -> None:
    for url in worlds().values():
        post(url, "/reset", {"orders": n, "seed": 7})
        for system, mode, rate in DEFAULT_CHAOS:
            post(url, "/chaos", {"system": system, "mode": mode, "rate": rate, "enabled": True})
        for uc in GENERIC:
            cfg = USECASES[uc]
            post(url, f"/{cfg['system']}/reset", {})
            for mode, rate in cfg["chaos"]:
                post(url, "/chaos", {"system": cfg["system"], "mode": mode, "rate": rate, "enabled": True})
    reset_demo(budget_cents=budget_for(list(worlds().values()), n))
    for uc in GENERIC:
        its = items(uc, n)
        total = sum(i["amount_cents"] for i in its)
        reset_usecase_grant(uc, USECASES[uc]["tool"], max(2 * total, 1000), max(i["amount_cents"] for i in its))
    admin_db().processes.delete_many({"_id": {"$in": [proc_id(u, s) for u in USECASES for s in STYLES]}})


def _supervise(cmd: list[str], pid_: str, out: dict, quiet: bool, crash_prob: float) -> None:
    d = admin_db()
    env = dict(os.environ, CRASH_PROB=str(crash_prob), PYTHONUNBUFFERED="1")
    env.pop("ONCE_CLIENT", None)
    crashes = errors = 0
    while crashes <= MAX_RESTARTS:
        p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        tail: list[str] = []
        for line in p.stdout:
            tail = (tail + [line.rstrip()])[-10:]
            if not quiet:
                print(f"{pid_:>20} | {line.rstrip()}", flush=True)
        code = p.wait()
        if code == 0:
            break
        if code in (137, -9):
            crashes += 1
            d.processes.update_one({"_id": pid_}, {"$set": {"crashes": crashes}})
            continue
        errors += 1
        print(f"{pid_:>20} | exited {code} ({errors}/{MAX_ERRORS}): {' / '.join(tail[-3:])}", flush=True)
        if errors >= MAX_ERRORS:
            break
    d.processes.update_one({"_id": pid_}, {"$set": {"crashes": crashes, "pid": None}})
    out[pid_] = crashes


def run_all(n: int = 6, crash_prob: float = 0.2, quiet: bool = True) -> str:
    d = admin_db()
    reset_all(n)
    run_id = f"u{int(time.time())}"
    d.processes.replace_one({"_id": "race_all"}, {"_id": "race_all", "run_id": run_id, "items": n,
                                                  "status": "running", "started_at": now()}, upsert=True)
    W = worlds()
    for uc in USECASES:
        for s in STYLES:
            d.processes.replace_one({"_id": proc_id(uc, s)}, {
                "_id": proc_id(uc, s), "agent": REFUND_AGENT[s] if uc == "refunds" else s, "usecase": uc,
                "run_id": run_id, "crashes": 0, "status": "starting", "pid": None, "world_url": W[s],
                "claimed_done": [], "held": [], "started_at": now()}, upsert=True)
    print(f"[race_all] {run_id}: {n} items x 4 use cases x 3 agents, crash_prob {crash_prob}", flush=True)
    out: dict = {}
    threads = []
    for s in STYLES:
        a = REFUND_AGENT[s]
        threads.append(threading.Thread(target=supervise, args=(
            a, run_id, W[s], n, crash_prob, "g_ops_demo", a, None, {}, quiet)))
        for uc in GENERIC:
            cmd = [sys.executable, "-m", "agent.usecase_agent", "--usecase", uc, "--agent", s, "--run-id", run_id,
                   "--world-url", W[s], "--items", str(n), "--proc-id", proc_id(uc, s)]
            threads.append(threading.Thread(target=_supervise, args=(cmd, proc_id(uc, s), out, quiet, crash_prob)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    d.processes.update_one({"_id": "race_all"}, {"$set": {"status": "finished", "finished_at": now()}})
    b = board()
    d.results.insert_one({"run_id": run_id, "kind": "race_all", "board": b, "created_at": now()})
    return run_id


def _get(url: str):
    try:
        return httpx.get(url, timeout=3).json()
    except Exception:
        return None


def board(detail: str | None = None) -> dict:
    """Live scoreboard: per use case, per agent style, the headline number (from ground truth)."""
    d = admin_db()
    ra = d.processes.find_one({"_id": "race_all"}) or {}
    run_id, n = ra.get("run_id"), ra.get("items", 6)
    procs = {p["_id"]: p for p in d.processes.find({})}
    W = worlds()
    out = {"run_id": run_id, "status": ra.get("status", "idle"), "items": n, "usecases": {}}
    urls = [f"{W[s]}/truth/summary" if uc == "refunds" else f"{W[s]}/{cfg['system']}/truth"
            for uc, cfg in USECASES.items() for s in STYLES]
    with ThreadPoolExecutor(max_workers=len(urls)) as ex:      # 12 truth calls at once, not one by one
        fetched = dict(zip(urls, ex.map(_get, urls)))
    for uc, cfg in USECASES.items():
        block = {"title": cfg["title"], "danger": cfg["danger"], "metric": cfg["metric"],
                 "metric_label": cfg["metric_label"], "agents": {}}
        rows: dict = {}
        for s in STYLES:
            p = procs.get(proc_id(uc, s), {})
            claimed, held = p.get("claimed_done", []), set(p.get("held", []))
            if uc == "refunds":
                t = fetched.get(f"{W[s]}/truth/summary") or {}
                per = {o: {"count": v.get("refunds", 0), "ok": v.get("refunds", 0) and v.get("emails", 0)}
                       for o, v in (t.get("per_order") or {}).items()}
                twice, twice_cents = t.get("dup_refunds", 0), t.get("dup_refund_cents", 0)
                labels = {o: o for o in per}
            else:
                t = fetched.get(f"{W[s]}/{cfg['system']}/truth") or {}
                its = items(uc, n)
                labels = {i["item_id"]: i["label"] for i in its}
                per = {i["item_id"]: {"count": (t.get("per_item") or {}).get(i["item_id"], {}).get("count", 0)}
                       for i in its}
                for v in per.values():
                    v["ok"] = v["count"] > 0
                twice, twice_cents = t.get("done_twice", 0), t.get("done_twice_cents", 0)
            false_done = [i for i in claimed if not per.get(i, {}).get("ok")]
            never = [i for i, v in per.items() if v["count"] == 0 and i not in held and i in claimed]
            if cfg["metric"] == "money":
                value = twice_cents
            elif cfg["metric"] == "false_done":
                value = len(false_done)
            else:
                value = twice + len(never)
            block["agents"][s] = {"value": value, "twice": twice, "twice_cents": twice_cents,
                                  "false_done": len(false_done), "never_arrived": len(never),
                                  "crashes": p.get("crashes", 0), "status": p.get("status", "idle"),
                                  "finished": len(claimed)}
            if detail == uc:
                for i, v in per.items():
                    rows.setdefault(i, {"item": labels.get(i, i)})[s] = v["count"]
                    if i in claimed and not v.get("ok"):
                        rows[i][s + "_false"] = True
        if detail == uc and run_id:
            for e in d.effects.find({"run_id": run_id, "order_id": {"$in": list(rows)},
                                     "tool": {"$in": ["bank.refund", cfg.get("tool", "")]}},
                                    {"order_id": 1, "status": 1, "evidence": 1}):
                ev = e.get("evidence") or {}
                rows[e["order_id"]]["once_proof"] = ev.get("refund_id") or ev.get("record_id")
                rows[e["order_id"]]["once_status"] = e["status"]
            block["rows"] = [rows[k] for k in sorted(rows)]
        out["usecases"][uc] = block
    return out


def table(b: dict) -> str:
    lines = [f"{'':34}{'naive':>14}{'textbook':>14}{'exactly-once':>14}"]
    for uc, blk in b["usecases"].items():
        def fmt(x):
            return f"${x['value'] / 100:,.2f}" if blk["metric"] == "money" else str(x["value"])
        lines.append(f"{blk['title'] + ': ' + blk['metric_label']:34}" +
                     "".join(f"{fmt(blk['agents'][s]):>14}" for s in STYLES))
        lines.append(f"{'  crashes':34}" + "".join(f"{blk['agents'][s]['crashes']:>14}" for s in STYLES))
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--items", type=int, default=6)
    p.add_argument("--crash-prob", type=float, default=0.2)
    p.add_argument("--headless", action="store_true")
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args()
    run_all(a.items, a.crash_prob, quiet=not a.verbose)
    print("\n" + table(board()), flush=True)


if __name__ == "__main__":
    main()
