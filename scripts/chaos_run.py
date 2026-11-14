"""Many races in a row on SEPARATE worlds and grant, summed into one table.

python -m scripts.chaos_run --runs 10 --orders 10 --crash-prob 0.2 --decider stub \
  --baseline-url http://localhost:8011 --once-url http://localhost:8012 --grant-id g_chaos
"""
from __future__ import annotations

import argparse

from scripts.race import ROWS, run_race, table

SUM_KEYS = [k for _, k in ROWS]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--orders", type=int, default=10)
    p.add_argument("--crash-prob", type=float, default=0.2)
    p.add_argument("--decider", choices=["llm", "stub"], default="stub")
    p.add_argument("--baseline-url", default="http://localhost:8011")
    p.add_argument("--once-url", default="http://localhost:8012")
    p.add_argument("--textbook-url", default="http://localhost:8013")
    p.add_argument("--agents", default="baseline,textbook,once")
    p.add_argument("--grant-id", default="g_chaos")
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args()
    if a.grant_id == "g_ops_demo" or any(u.endswith(p) for u, p in ((a.baseline_url, ":8001"), (a.once_url, ":8002"),
                                                                     (a.textbook_url, ":8003"))):
        raise SystemExit("chaos_run must not share worlds or grants with the live demo")
    agents = tuple(a.agents.split(","))
    total = {ag: {k: 0 for k in SUM_KEYS} for ag in agents}
    for i in range(1, a.runs + 1):
        res = run_race(a.orders, a.crash_prob, agents, "default", a.baseline_url, a.once_url,
                       a.grant_id, a.decider, tag="chaos:", quiet=not a.verbose, textbook_url=a.textbook_url)
        for ag, r in res.items():
            for k in SUM_KEYS:
                total[ag][k] += r.get(k) or 0
        print(f"[chaos] run {i}/{a.runs}: " + " | ".join(
            f"{ag} dup={r['dup_refunds']} false_done={r['false_done']} crashes={r['crashes']}"
            for ag, r in res.items()), flush=True)
    for ag in agents:
        if ag != "once":
            total[ag]["first_try_fails"] = None
    print(f"\n{a.runs} runs x {a.orders} orders, crash_prob {a.crash_prob}, decider {a.decider}\n")
    print(table(total), flush=True)


if __name__ == "__main__":
    main()
