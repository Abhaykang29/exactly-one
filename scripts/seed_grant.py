"""Create/reset grants.  python -m scripts.seed_grant --reset [--grant-id g_chaos]"""
from __future__ import annotations

import argparse
from datetime import timedelta

from once.config import admin_db
from once.grants import now

ISSUER = "ops-manager@maple.test"
AGENT_TOOLS = ["bank.refund", "inventory.restock", "email.send"]
DEFAULT_BUDGET = 300000


def budget_for(world_urls: list[str], n_orders: int) -> int:
    """At least 2x the total value of the orders a race will refund, so no refund is falsely denied."""
    import httpx
    total = 0
    for url in world_urls:
        orders = httpx.get(url.rstrip("/") + "/orders", params={"status": "delayed"}, timeout=10).json()
        total = max(total, sum(o["amount_cents"] for o in orders[:n_orders]))
    return max(DEFAULT_BUDGET, 2 * total)


def grant_doc(grant_id: str, budget_cents: int = DEFAULT_BUDGET) -> dict:
    if grant_id == "g_manager_rollback":
        return {"_id": grant_id, "agent": "ops-console", "issued_by": ISSUER, "tools": ["bank.reverse"],
                "max_per_action_cents": 50000, "budget_cents": 10_000_000}
    return {"_id": grant_id, "agent": "refund-agent", "issued_by": ISSUER, "tools": AGENT_TOOLS,
            "max_per_action_cents": 50000, "budget_cents": budget_cents}


def reset_grant(grant_id: str, d=None, budget_cents: int = DEFAULT_BUDGET) -> None:
    d = d if d is not None else admin_db()
    g = grant_doc(grant_id, budget_cents)
    g.update({"expires_at": now() + timedelta(hours=8), "revoked": False, "revoked_at": None, "revoked_by": None})
    d.grants.replace_one({"_id": grant_id}, g, upsert=True)
    d.grant_usage.replace_one({"_id": grant_id}, {"_id": grant_id, "spent_cents": 0, "uses": 0, "revoked": False},
                              upsert=True)


def reset_usecase_grant(uc: str, tool: str, budget_cents: int, max_per_action_cents: int, d=None) -> str:
    """One grant per extra use case (admin only): the agent can read it, never write it."""
    d = d if d is not None else admin_db()
    gid = f"g_uc_{uc}"
    d.grants.replace_one({"_id": gid}, {
        "_id": gid, "agent": f"{uc}-agent", "issued_by": ISSUER, "tools": [tool],
        "max_per_action_cents": max(1, max_per_action_cents), "budget_cents": max(1, budget_cents),
        "expires_at": now() + timedelta(hours=8), "revoked": False, "revoked_at": None, "revoked_by": None},
        upsert=True)
    d.grant_usage.replace_one({"_id": gid}, {"_id": gid, "spent_cents": 0, "uses": 0, "revoked": False}, upsert=True)
    return gid


def reset_demo(d=None, budget_cents: int = DEFAULT_BUDGET) -> None:
    reset_grant("g_ops_demo", d, budget_cents)
    reset_grant("g_manager_rollback", d)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--reset", action="store_true")
    p.add_argument("--grant-id", default=None)
    a = p.parse_args()
    if a.grant_id:
        reset_grant(a.grant_id)
        print(f"reset {a.grant_id}")
    else:
        reset_demo()
        print("reset g_ops_demo, g_manager_rollback")


if __name__ == "__main__":
    main()
