"""M5 demo: bank renames its fields; only a proven fix becomes a memory; memory removes first-try failures.

python -m scripts.demo_drift [--orders 5]
"""
from __future__ import annotations

import argparse
import time

from once import memory
from once.config import admin_db, tenant
from scripts.race import run_race

PROBLEM = "bank POST /bank/refunds 422 invalid refund request"


def run(orders: int, label: str) -> dict:
    r = run_race(orders, 0.0, ("once",), "none", drift=True, quiet=True)["once"]
    print(f"{label}: run {r['run_id']} first_try_fails={r['first_try_fails']} finished={r['claimed_done']} "
          f"held={r['held']}")
    return r


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--orders", type=int, default=5)
    a = p.parse_args()
    d = admin_db()
    scope = {"scope.system": "bank", "scope.tenant": tenant()}
    d.memories.delete_many(scope)
    d.candidates.delete_many(scope)
    d.flags.update_one({"_id": "memory"}, {"$set": {"on": True}}, upsert=True)

    print("(1) drift on, memory on, no memories yet")
    r1 = run(a.orders, "    ")
    print(f"    candidates created: {d.candidates.count_documents(scope)}, "
          f"promoted: {d.candidates.count_documents({**scope, 'status': 'promoted'})}, "
          f"memories: {d.memories.count_documents(scope)}")
    for m in d.memories.find(scope, {"embedding": 0}):
        print(f"    memory {m['_id']} fix={m['fix']['field_map']} source_receipts={m['source_receipts']}")

    print("    waiting for the vector index to see the memory ...")
    for _ in range(60):
        if memory.recall(PROBLEM, "bank", tenant()):
            break
        time.sleep(1)

    print("(2) new run, memory on")
    r2 = run(a.orders, "    ")
    used = d.effects.find_one({"run_id": r2["run_id"], "memory_used.0": {"$exists": True}})
    if used:
        m = d.memories.find_one({"_id": used["memory_used"][0]}, {"embedding": 0})
        print(f"    recalled {m['_id']} learned from {m['source_receipts'][0]} (successes {m['successes']})")

    print("(3) new run, memory OFF")
    d.flags.update_one({"_id": "memory"}, {"$set": {"on": False}})
    try:
        run(a.orders, "    ")
    finally:
        d.flags.update_one({"_id": "memory"}, {"$set": {"on": True}})
    print(f"\nexpected: (2) first_try_fails = 0, (3) first_try_fails > 0. got (1)={r1['first_try_fails']} "
          f"(2)={r2['first_try_fails']}")


if __name__ == "__main__":
    main()
