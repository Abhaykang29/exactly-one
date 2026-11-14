"""Fake email provider."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mocks.chaos import on, wdb

router = APIRouter()


class Message(BaseModel):
    to: str
    subject: str
    body: str
    order_id: str
    reference: str


@router.post("/email/messages")
def send(m: Message):
    if on("email", "fail_before_commit"):
        return JSONResponse({"error": "email unavailable"}, status_code=503)
    wdb().emails.insert_one({"message_id": "em_" + uuid.uuid4().hex[:6], **m.model_dump(),
                             "created_at": datetime.now(timezone.utc)})
    return {"ok": True}


@router.get("/email/messages")
def list_messages(reference: str | None = None, order_id: str | None = None):
    q = {k: v for k, v in {"reference": reference, "order_id": order_id}.items() if v is not None}
    return list(wdb().emails.find(q, {"_id": 0}).sort("created_at", 1))
