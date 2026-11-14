"""One generic fake system for the extra use cases: /{system}/actions. Honors no idempotency keys (like the bank).

Chaos per system (stored as chaos docs "<system>:<mode>"): timeout_after_commit (store, then 504 after 3s),
fake_ok (200, nothing stored).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mocks.chaos import on, wdb

router = APIRouter()
CORE = {"bank", "inventory", "email", "orders", "chaos", "truth", "reset", "health"}


class Action(BaseModel):
    item_id: str
    target: str
    amount_cents: int = 0
    detail: str = ""
    reference: str


def _coll(system: str):
    return wdb()[f"act_{system}"]


@router.post("/{system}/actions")
async def create(system: str, a: Action):
    if system in CORE:
        return JSONResponse({"error": "reserved system"}, status_code=404)
    if on(system, "fake_ok"):
        return {"ok": True}
    rec = {"record_id": f"{system[:3]}_" + uuid.uuid4().hex[:6], **a.model_dump(),
           "created_at": datetime.now(timezone.utc)}
    _coll(system).insert_one(dict(rec))
    if on(system, "timeout_after_commit"):
        await asyncio.sleep(3)              # stored; the client times out at 2s
        return JSONResponse({"error": "gateway timeout"}, status_code=504)
    return {"ok": True, "record_id": rec["record_id"]}


@router.get("/{system}/actions")
def list_actions(system: str, reference: str | None = None, item_id: str | None = None):
    q = {k: v for k, v in {"reference": reference, "item_id": item_id}.items() if v is not None}
    return list(_coll(system).find(q, {"_id": 0}).sort("created_at", 1))


@router.post("/{system}/reset")
def reset_system(system: str):
    _coll(system).drop()
    _coll(system).create_index("reference")
    wdb().chaos.delete_many({"system": system})
    return {"ok": True}


@router.get("/{system}/truth")
def truth(system: str):
    per: dict = {}
    dup = dup_cents = 0
    for r in _coll(system).find({}, {"item_id": 1, "amount_cents": 1}).sort("created_at", 1):
        p = per.setdefault(r["item_id"], {"count": 0, "cents": 0})
        if p["count"] >= 1:
            dup += 1
            dup_cents += int(r.get("amount_cents") or 0)
        p["count"] += 1
        p["cents"] += int(r.get("amount_cents") or 0)
    return {"system": system, "records": sum(p["count"] for p in per.values()), "done_twice": dup,
            "done_twice_cents": dup_cents, "per_item": per}
