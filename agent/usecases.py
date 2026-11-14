"""The four use cases, as config. Refunds is the existing flow; the other three use the generic fake system."""
from __future__ import annotations

import random

PRICES = {"AAPL": 23412, "MSFT": 41850, "NVDA": 13290, "SHOP": 10544, "RY": 16120, "TD": 8231}
APPS = ["Salesforce", "GitHub", "Okta", "Slack", "AWS", "Workday"]
DISTS = ["Northwind Foods", "Maple Grocers", "Prairie Pharma", "Coastal Supply", "Lakeshore Medical",
         "Summit Wholesale"]


def _rebalancing(n: int, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for i in range(1, n + 1):
        t = rng.choice(list(PRICES))
        shares = rng.randint(5, 40)
        out.append({"item_id": f"ACC-{i:03d}-{t}", "target": f"ACC-{i:03d}", "amount_cents": shares * PRICES[t],
                    "detail": f"sell {shares} {t}", "label": f"ACC-{i:03d} sell {shares} {t}"})
    return out


def _offboarding(n: int, seed: int = 7) -> list[dict]:
    rng = random.Random(seed + 1)
    return [{"item_id": f"EMP-{100 + i}-{app}", "target": f"EMP-{100 + i}", "amount_cents": 0,
             "detail": f"remove access from {app}", "label": f"EMP-{100 + i} from {app}"}
            for i, app in ((i, rng.choice(APPS)) for i in range(1, n + 1))]


def _alerts(n: int, seed: int = 7) -> list[dict]:
    return [{"item_id": f"DIST-{i:02d}", "target": DISTS[(i - 1) % len(DISTS)], "amount_cents": 0,
             "detail": f"recall alert lot #{4400 + i}", "label": f"{DISTS[(i - 1) % len(DISTS)]}"}
            for i in range(1, n + 1)]


USECASES = {
    "refunds": {"title": "Refunds", "danger": "A crash mid-refund pays the customer twice.",
                "metric": "money", "metric_label": "paid out twice"},
    "rebalancing": {"title": "Portfolio rebalancing", "danger": "A crash mid-trade sells the same shares twice.",
                    "system": "broker", "action": "sell", "tool": "broker.sell", "items": _rebalancing,
                    "chaos": [("timeout_after_commit", 0.25)], "metric": "money", "metric_label": "traded twice"},
    "offboarding": {"title": "Employee offboarding", "danger": "The agent says access was removed. It wasn't.",
                    "system": "iam", "action": "revoke", "tool": "iam.revoke_access", "items": _offboarding,
                    "chaos": [("fake_ok", 0.25)], "metric": "false_done", "metric_label": "said removed, wasn't"},
    "alerts": {"title": "Recall alerts", "danger": "Distributors get the alert twice, or never.",
               "system": "notify", "action": "send", "tool": "notify.send", "items": _alerts,
               "chaos": [("timeout_after_commit", 0.2), ("fake_ok", 0.2)], "metric": "alerts",
               "metric_label": "sent twice or never arrived"},
}
GENERIC = [k for k, v in USECASES.items() if "system" in v]


def items(uc: str, n: int) -> list[dict]:
    return USECASES[uc]["items"](n)
