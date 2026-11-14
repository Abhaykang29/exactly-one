"""Ground truth computed from what the world actually stored."""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter

from mocks.chaos import wdb

router = APIRouter()


@router.get("/truth/summary")
def summary():
    d = wdb()
    orders = [o["order_id"] for o in d.orders.find({}, {"order_id": 1}).sort("order_id", 1)]
    counts = {name: Counter(x["order_id"] for x in d[coll].find({}, {"order_id": 1}))
              for name, coll in (("refunds", "refunds"), ("restocks", "restocks"), ("emails", "emails"))}
    per = {o: {k: counts[k].get(o, 0) for k in counts} for o in orders}
    # money paid more than once: every refund for an order after its first (reversed ones still count as paid)
    dup_cents, seen = 0, set()
    for r in d.refunds.find({}, {"order_id": 1, "amount_cents": 1}).sort("created_at", 1):
        if r["order_id"] in seen:
            dup_cents += int(r.get("amount_cents") or 0)
        seen.add(r["order_id"])

    def dup(k):
        return sum(max(0, p[k] - 1) for p in per.values())

    def missing(k):
        return sum(1 for p in per.values() if p[k] == 0)

    return {"orders": len(orders),
            "refunds_total": sum(counts["refunds"].values()),
            "orders_refunded": sum(1 for p in per.values() if p["refunds"] > 0),
            "dup_refunds": dup("refunds"), "dup_refund_cents": dup_cents,
            "refunds_reversed": d.reversals.count_documents({}),
            "restocks_total": sum(counts["restocks"].values()), "dup_restocks": dup("restocks"),
            "emails_total": sum(counts["emails"].values()), "dup_emails": dup("emails"),
            "missing_refunds": missing("refunds"), "missing_restocks": missing("restocks"),
            "missing_emails": missing("emails"),
            "per_order": per}
