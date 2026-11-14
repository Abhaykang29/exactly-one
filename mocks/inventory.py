"""Fake inventory. fake_ok chaos: says 200 but stores nothing."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from mocks.chaos import on, wdb

router = APIRouter()


class Restock(BaseModel):
    sku: str
    qty: int
    order_id: str
    reference: str


@router.post("/inventory/restocks")
def restock(r: Restock):
    if on("inventory", "fake_ok"):
        return {"ok": True}
    wdb().restocks.insert_one({"restock_id": "rs_" + uuid.uuid4().hex[:6], **r.model_dump(),
                               "created_at": datetime.now(timezone.utc)})
    return {"ok": True}


@router.get("/inventory/restocks")
def list_restocks(reference: str | None = None, order_id: str | None = None):
    q = {k: v for k, v in {"reference": reference, "order_id": order_id}.items() if v is not None}
    return list(wdb().restocks.find(q, {"_id": 0}).sort("created_at", 1))
