"""Fake bank. Honors NO idempotency keys on purpose: it stores the client's `reference` and nothing more."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mocks.chaos import on, wdb

router = APIRouter()
V1 = ["order_id", "amount_cents", "account", "reference"]
V2 = ["order_ref", "refund_amount_cents", "account", "reference"]


def _now():
    return datetime.now(timezone.utc)


def _required() -> tuple[int, list[str]]:
    return (2, V2) if on("bank", "schema_v2") else (1, V1)


@router.get("/bank/schema")
def schema():
    v, req = _required()
    return {"version": v, "required": req}


@router.post("/bank/refunds")
async def create_refund(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = None
    v, req = _required()
    if not isinstance(body, dict) or any(k not in body for k in req):
        return JSONResponse({"error": "invalid refund request", "hint": "see GET /bank/schema"}, status_code=422)
    if on("bank", "fail_before_commit"):
        return JSONResponse({"error": "bank unavailable"}, status_code=503)
    order_id = body["order_id"] if v == 1 else body["order_ref"]
    amount = body["amount_cents"] if v == 1 else body["refund_amount_cents"]
    if not isinstance(amount, int) or amount <= 0:
        return JSONResponse({"error": "invalid refund request", "hint": "amount must be a positive int"},
                            status_code=422)
    doc = {"refund_id": "tx_" + uuid.uuid4().hex[:6], "order_id": order_id, "amount_cents": amount,
           "account": body["account"], "reference": body["reference"], "status": "settled",
           "schema_version": v, "created_at": _now()}
    wdb().refunds.insert_one(dict(doc))
    if on("bank", "timeout_after_commit"):
        await asyncio.sleep(3)              # the refund IS stored; the client times out at 2s
        return JSONResponse({"error": "gateway timeout"}, status_code=504)
    return JSONResponse({"refund_id": doc["refund_id"], "status": "settled"}, status_code=201)


@router.get("/bank/refunds")
def list_refunds(reference: str | None = None, order_id: str | None = None):
    q = {k: v for k, v in {"reference": reference, "order_id": order_id}.items() if v is not None}
    return list(wdb().refunds.find(q, {"_id": 0}).sort("created_at", 1))


@router.get("/bank/refunds/{refund_id}")
def get_refund(refund_id: str):
    r = wdb().refunds.find_one({"refund_id": refund_id}, {"_id": 0})
    return r if r else JSONResponse({"error": "not found"}, status_code=404)


@router.post("/bank/refunds/{refund_id}/reverse")
async def reverse_refund(refund_id: str, request: Request):
    body = await request.json()
    r = wdb().refunds.find_one({"refund_id": refund_id})
    if r is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if "reference" not in body:
        return JSONResponse({"error": "invalid reversal request", "hint": "reference required"}, status_code=422)
    wdb().refunds.update_one({"refund_id": refund_id}, {"$set": {"status": "reversed"}})
    rev = {"reversal_id": "rv_" + uuid.uuid4().hex[:6], "refund_id": refund_id, "order_id": r["order_id"],
           "amount_cents": r["amount_cents"], "reference": body["reference"], "created_at": _now()}
    wdb().reversals.insert_one(dict(rev))
    rev["created_at"] = rev["created_at"].isoformat()
    return rev


@router.get("/bank/reversals")
def list_reversals(reference: str | None = None):
    q = {"reference": reference} if reference else {}
    return list(wdb().reversals.find(q, {"_id": 0}).sort("created_at", 1))
