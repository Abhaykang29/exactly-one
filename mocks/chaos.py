"""World DB handle and chaos switches (stored in Mongo so they survive restarts)."""
from __future__ import annotations

import os
import random
from functools import lru_cache

from fastapi import APIRouter
from pydantic import BaseModel

from once.config import admin_client

router = APIRouter()
MODES = {("bank", "fail_before_commit"), ("bank", "timeout_after_commit"), ("bank", "schema_v2"),
         ("inventory", "fake_ok"), ("email", "fail_before_commit")}
_rng = random.Random()


@lru_cache(maxsize=None)
def wdb():
    return admin_client()[os.environ.get("WORLD_DB", "world_baseline")]


def on(system: str, mode: str) -> bool:
    """True if this request should suffer the chaos mode (rate-based; schema_v2 ignores rate)."""
    sw = wdb().chaos.find_one({"_id": f"{system}:{mode}"})
    if not sw or not sw.get("enabled"):
        return False
    return mode == "schema_v2" or _rng.random() < float(sw.get("rate", 0))


class Switch(BaseModel):
    system: str
    mode: str
    rate: float = 1.0
    enabled: bool = True


@router.post("/chaos")
def set_chaos(s: Switch):
    generic = s.system not in {"bank", "inventory", "email"} and s.mode in ("timeout_after_commit", "fake_ok")
    if (s.system, s.mode) not in MODES and not generic:
        return {"error": f"unknown chaos {s.system}:{s.mode}", "modes": sorted(f"{a}:{b}" for a, b in MODES)}
    wdb().chaos.update_one({"_id": f"{s.system}:{s.mode}"},
                           {"$set": {"system": s.system, "mode": s.mode, "rate": s.rate, "enabled": s.enabled}},
                           upsert=True)
    return {"ok": True, **s.model_dump()}


@router.get("/chaos")
def get_chaos():
    return list(wdb().chaos.find({}, {"_id": 0}))
