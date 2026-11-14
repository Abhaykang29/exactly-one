"""Create/reset grants.  python -m scripts.seed_grant --reset [--grant-id g_chaos]"""
from __future__ import annotations

import argparse
from datetime import timedelta

from once.config import admin_db
from once.grants import now

ISSUER = "ops-manager@maple.test"
AGENT_TOOLS = ["bank.refund", "inventory.restock", "email.send"]


def grant_doc(grant_id: str) -> dict:
    if grant_id == "g_manager_rollback":
        return {"_id": grant_id, "agent": "ops-console", "issued_by": ISSUER, "tools": ["bank.reverse"],
                "max_per_action_cents": 50000, "budget_cents": 10_000_000}
    return {"_id": grant_id, "agent": "refund-agent", "issued_by": ISSUER, "tools": AGENT_TOOLS,
            "max_per_action_cents": 50000, "budget_cents": 300000}


def reset_grant(grant_id: str, d=None) -> None:
    d = d if d is not None else admin_db()
    g = grant_doc(grant_id)
    g.update({"expires_at": now() + timedelta(hours=8), "revoked": False, "revoked_at": None, "revoked_by": None})
    d.grants.replace_one({"_id": grant_id}, g, upsert=True)
    d.grant_usage.replace_one({"_id": grant_id}, {"_id": grant_id, "spent_cents": 0, "uses": 0, "revoked": False},
                              upsert=True)


def reset_demo(d=None) -> None:
    for gid in ("g_ops_demo", "g_manager_rollback"):
        reset_grant(gid, d)


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
