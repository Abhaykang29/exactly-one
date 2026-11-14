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
    p.add_argument("--grant-id", default="g_chaos")
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args()
    if a.grant_id == "g_ops_demo" or a.baseline_url.endswith(":8001") or a.once_url.endswith(":8002"):
        raise SystemExit("chaos_run must not share worlds or grants with the live demo")
    total = {ag: {k: 0 for k in SUM_KEYS} for ag in ("baseline", "once")}
    for i in range(1, a.runs + 1):
        res = run_race(a.orders, a.crash_prob, ("baseline", "once"), "default", a.baseline_url, a.once_url,
                       a.grant_id, a.decider, tag="chaos:", quiet=not a.verbose)
        for ag, r in res.items():
            for k in SUM_KEYS:
                total[ag][k] += r.get(k) or 0
        b, o = res["baseline"], res["once"]
        print(f"[chaos] run {i}/{a.runs}: standard dup={b['dup_refunds']} false_done={b['false_done']} "
              f"crashes={b['crashes']} | exactly-once dup={o['dup_refunds']} false_done={o['false_done']} "
              f"crashes={o['crashes']}", flush=True)
    total["once"]["first_try_fails"] = total["once"]["first_try_fails"]
    total["baseline"]["first_try_fails"] = None
    print(f"\n{a.runs} runs x {a.orders} orders, crash_prob {a.crash_prob}, decider {a.decider}\n")
    print(table(total), flush=True)


if __name__ == "__main__":
    main()
