"""M4 demo: start a race, revoke g_ops_demo after ~5s, then prove nothing slipped through and reverse a refund.

python -m scripts.demo_revoke [--orders 20] [--after 5] [--crash-prob 0]
"""
from __future__ import annotations

import argparse
import threading
import time

import httpx

from once.compensate import reverse
from once.config import admin_db
from once.grants import revoke
from scripts.race import run_race, table

BY = "ops-manager@maple.test"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--orders", type=int, default=20)
    p.add_argument("--after", type=float, default=5.0)
    p.add_argument("--crash-prob", type=float, default=0.0)
    p.add_argument("--decider", default=None)
    a = p.parse_args()
    d = admin_db()
    holder: dict = {}
    t = threading.Thread(target=lambda: holder.update(res=run_race(a.orders, a.crash_prob, decider=a.decider,
                                                                   chaos="none")))
    t.start()
    time.sleep(a.after)
    run_id = d.processes.find_one({"_id": "once"})["run_id"]
    revoked_at = revoke("g_ops_demo", by=BY)
    stopped_at = None
    deadline = time.time() + 30
    while time.time() < deadline:
        pr = d.processes.find_one({"_id": "once"}) or {}
        if pr.get("status") in ("stopped", "finished") and pr.get("stopped_at"):
            stopped_at = pr["stopped_at"]
            break
        time.sleep(0.1)
    t.join()

    print("\n" + table(holder["res"]))
    print("\n=== M4: permission, kill switch, compensation ===")
    if stopped_at:
        print(f"(a) revoke -> once agent stopped: {(stopped_at - revoked_at).total_seconds():.2f}s")
    else:
        print("(a) once agent did NOT stop within 30s")
    late = d.effects.count_documents({"authority.grant_id": "g_ops_demo", "run_id": run_id,
                                      "created_at": {"$gt": revoked_at}})
    print(f"(b) effects created after revoked_at: {late}")
    denied = d.effects.count_documents({"run_id": run_id, "status": "denied"})
    print(f"    denied receipts after revoke: {denied}")

    refund = d.effects.find_one({"run_id": run_id, "tool": "bank.refund", "status": "proven"},
                                sort=[("proven_at", 1)])
    if refund is None:
        print("(c) no proven refund to reverse")
        return
    comp = reverse(refund["_id"], by=BY)
    again = reverse(refund["_id"], by=BY)          # exactly once: a second click changes nothing
    orig = d.effects.find_one({"_id": refund["_id"]})
    url = d.processes.find_one({"_id": "once"})["world_url"]
    world = httpx.get(f"{url}/bank/reversals", params={"reference": comp["_id"]}, timeout=5).json()
    print(f"(c) reversed {refund['_id']}: comp receipt {comp['_id']} is {comp['status']}, "
          f"evidence {comp.get('evidence')}")
    print(f"    original receipt now {orig['status']} (compensated_by {orig.get('compensated_by')})")
    print(f"    reversals in the bank for that reference: {len(world)} (second reverse() returned "
          f"{again['status']}, no new reversal)")


if __name__ == "__main__":
    main()
