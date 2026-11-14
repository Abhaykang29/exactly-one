"""HTTP clients for the fake systems: act + verify functions. Timeout 2.0s everywhere."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from once.errors import SchemaError, Unknown

V1_REFUND_FIELDS = ["order_id", "amount_cents", "account", "reference"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class World:
    def __init__(self, base_url: str):
        self.url = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.url, timeout=2.0)

    def _call(self, method: str, path: str, **kw):
        try:
            r = self.http.request(method, path, **kw)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise Unknown(f"{method} {path}: {type(e).__name__}") from e
        if r.status_code == 422:
            try:
                body = r.json()
            except ValueError:
                body = {"error": r.text}
            raise SchemaError(body)
        if r.status_code >= 500:
            raise Unknown(f"{method} {path}: HTTP {r.status_code}")
        r.raise_for_status()
        return r.json()

    # ---- reads
    def orders(self, n: int) -> list[dict]:
        return self._call("GET", "/orders", params={"status": "delayed"})[:n]

    def bank_schema(self) -> dict:
        return self._call("GET", "/bank/schema")

    # ---- bank refund
    def bank_refund_act(self, decision: dict, key: str, field_map: dict | None) -> None:
        body = {"order_id": decision["order_id"], "amount_cents": decision["amount_cents"],
                "account": decision["account"], "reference": key}
        if field_map:
            body = {field_map.get(k, k): v for k, v in body.items()}
        self._call("POST", "/bank/refunds", json=body)

    def bank_refund_verify(self, decision: dict, key: str) -> dict | None:
        rows = self._call("GET", "/bank/refunds", params={"reference": key})
        if not rows:
            return None
        r = rows[0]
        return {"refund_id": r["refund_id"], "amount_cents": r["amount_cents"], "matches": len(rows),
                "checked_at": _now()}

    # ---- bank reverse
    def bank_reverse_act(self, decision: dict, key: str, field_map: dict | None) -> None:
        self._call("POST", f"/bank/refunds/{decision['refund_id']}/reverse", json={"reference": key})

    def bank_reverse_verify(self, decision: dict, key: str) -> dict | None:
        rows = self._call("GET", "/bank/reversals", params={"reference": key})
        if not rows:
            return None
        r = rows[0]
        return {"reversal_id": r["reversal_id"], "refund_id": r["refund_id"], "checked_at": _now()}

    # ---- inventory
    def restock_act(self, decision: dict, key: str, field_map: dict | None) -> None:
        self._call("POST", "/inventory/restocks", json={"sku": decision["sku"], "qty": decision["qty"],
                                                        "order_id": decision["order_id"], "reference": key})

    def restock_verify(self, decision: dict, key: str) -> dict | None:
        rows = self._call("GET", "/inventory/restocks", params={"reference": key})
        if not rows:
            return None
        return {"restock_id": rows[0]["restock_id"], "sku": rows[0]["sku"], "qty": rows[0]["qty"],
                "checked_at": _now()}

    # ---- email
    def email_act(self, decision: dict, key: str, field_map: dict | None) -> None:
        self._call("POST", "/email/messages", json={"to": decision["to"], "subject": decision["subject"],
                                                    "body": decision["body"], "order_id": decision["order_id"],
                                                    "reference": key})

    def email_verify(self, decision: dict, key: str) -> dict | None:
        rows = self._call("GET", "/email/messages", params={"reference": key})
        if not rows:
            return None
        return {"message_id": rows[0]["message_id"], "to": rows[0]["to"], "checked_at": _now()}
