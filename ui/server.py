"""Demo console for the layer.  uvicorn ui.server:app --port 8000   (admin client)"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from once.config import admin_db

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
GRANT = "g_ops_demo"
BY = "ops-manager@maple.test"
AGENTS = ("baseline", "once")
WORLDS = {"baseline": os.environ.get("WORLD_BASELINE_URL", "http://localhost:8001"),
          "once": os.environ.get("WORLD_ONCE_URL", "http://localhost:8002")}

app = FastAPI(title="Exactly Once console")
_race: dict = {"proc": None}
_http = httpx.Client(timeout=1.5)


def ok(x) -> JSONResponse:
    return JSONResponse(jsonable_encoder(x))


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def _truth(url: str) -> dict | None:
    try:
        return _http.get(f"{url}/truth/summary").json()
    except Exception:
        return None


def _false_done(claimed: list[str], truth: dict | None, restock: dict) -> int:
    if not truth:
        return 0
    per = truth["per_order"]
    n = 0
    for o in claimed:
        p = per.get(o, {})
        if p.get("refunds", 0) == 0 or p.get("emails", 0) == 0 or (restock.get(o, True) and p.get("restocks", 0) == 0):
            n += 1
    return n


def _row(e: dict) -> dict:
    ev = e.get("evidence") or {}
    proof = ev.get("reversal_id") or ev.get("refund_id") or ev.get("restock_id") or ev.get("message_id") or ""
    action = e["_id"].split(":")[-1] if not e["_id"].startswith("comp:") else "reverse"
    return {"key": e["_id"], "order_id": e.get("order_id"), "action": action, "status": e["status"],
            "proof": proof, "t": e.get("t"), "memory": bool(e.get("memory_used")),
            "reason": e.get("held_reason")}


@app.get("/api/state")
def state():
    d = admin_db()
    procs = {p["_id"]: p for p in d.processes.find({"_id": {"$in": list(AGENTS)}})}
    run_id = (procs.get("once") or procs.get("baseline") or {}).get("run_id")
    restock = {e["order_id"]: bool(e["decision"].get("restock", True))
               for e in d.effects.find({"run_id": run_id, "tool": "bank.refund"}, {"order_id": 1, "decision.restock": 1})}
    agents = {}
    for a in AGENTS:
        p = procs.get(a, {})
        truth = _truth(p.get("world_url") or WORLDS[a])
        claimed = p.get("claimed_done", [])
        agents[a] = {"status": p.get("status", "idle"), "crashes": p.get("crashes", 0), "pid": p.get("pid"),
                     "finished": len(claimed), "held": len(p.get("held", [])),
                     "dup_refunds": truth["dup_refunds"] if truth else None,
                     "false_done": _false_done(claimed, truth, restock),
                     "refunds_total": truth["refunds_total"] if truth else None,
                     "world_up": truth is not None}
    counts = {c["_id"]: c["n"] for c in d.effects.aggregate([
        {"$match": {"run_id": run_id}}, {"$group": {"_id": "$status", "n": {"$sum": 1}}}])} if run_id else {}
    feed = [_row(e) for e in d.effects.aggregate([
        {"$match": {"run_id": run_id}},
        {"$addFields": {"t": {"$max": ["$created_at", "$sent_at", "$proven_at"]}}},
        {"$sort": {"t": -1}}, {"$limit": 25},
        {"$project": {"decision": 0, "inputs": 0}}])] if run_id else []
    g = d.grants.find_one({"_id": GRANT}) or {}
    recalled = d.effects.find_one({"run_id": run_id, "memory_used.0": {"$exists": True}},
                                  sort=[("created_at", -1)]) if run_id else None
    rec_banner = None
    if recalled:
        m = d.memories.find_one({"_id": recalled["memory_used"][0]}, {"embedding": 0})
        if m:
            src = m["source_receipts"][0].split(":")[1] if ":" in m["source_receipts"][0] else m["source_receipts"][0]
            rec_banner = {"memory_id": m["_id"], "learned_from": src, "status": m["status"]}
    flag = d.flags.find_one({"_id": "memory"}) or {"on": True}
    chaos = None
    try:
        chaos = _http.get(f"{WORLDS['once']}/chaos").json()
    except Exception:
        pass
    drift = any(c.get("mode") == "schema_v2" and c.get("enabled") for c in (chaos or []))
    proc = _race["proc"]
    return ok({
        "run_id": run_id, "agents": agents, "receipts": counts, "feed": feed,
        "grant": {"id": GRANT, "revoked": g.get("revoked"), "revoked_at": g.get("revoked_at"),
                  "revoked_by": g.get("revoked_by"), "issued_by": g.get("issued_by")},
        "memory": {"on": flag.get("on", True), "count": d.memories.count_documents({"status": "active"}),
                   "recalled": rec_banner},
        "drift": drift,
        "race_running": bool(proc and proc.poll() is None),
        "results": list(d.results.find({}, {"_id": 0}).sort("created_at", -1).limit(2)),
    })


@app.post("/api/crash")
def crash_both():
    killed = []
    for p in admin_db().processes.find({"_id": {"$in": list(AGENTS)}, "status": "running"}):
        if p.get("pid"):
            try:
                os.kill(p["pid"], signal.SIGKILL)
                killed.append(p["_id"])
            except ProcessLookupError:
                pass
    return {"killed": killed}


@app.post("/api/revoke")
def api_revoke():
    from once.grants import revoke
    t = revoke(GRANT, by=BY)
    return ok({"revoked_at": t, "by": BY})


@app.post("/api/regrant")
def api_regrant():
    from scripts.seed_grant import reset_demo
    reset_demo()
    return {"ok": True}


class On(BaseModel):
    on: bool


@app.post("/api/drift")
def api_drift(b: On):
    out = {}
    for a, url in WORLDS.items():
        try:
            out[a] = _http.post(f"{url}/chaos", json={"system": "bank", "mode": "schema_v2", "rate": 1,
                                                      "enabled": b.on}).json()
        except Exception as e:
            out[a] = {"error": str(e)}
    return out


@app.post("/api/memory")
def api_memory(b: On):
    admin_db().flags.update_one({"_id": "memory"}, {"$set": {"on": b.on}}, upsert=True)
    return {"on": b.on}


@app.get("/api/receipt/{key:path}")
def receipt(key: str):
    rows = list(admin_db().effects.aggregate([
        {"$match": {"_id": key}},
        {"$lookup": {"from": "grants", "localField": "authority.grant_id", "foreignField": "_id", "as": "grant"}},
        {"$lookup": {"from": "memories", "localField": "memory_used", "foreignField": "_id", "as": "memories"}},
        {"$lookup": {"from": "effects", "localField": "_id", "foreignField": "compensates", "as": "compensations"}},
        {"$project": {"memories.embedding": 0}},
    ]))
    if not rows:
        return JSONResponse({"error": "not found"}, status_code=404)
    return ok(rows[0])


@app.post("/api/reverse/{key:path}")
def api_reverse(key: str):
    from once.compensate import reverse
    try:
        return ok(reverse(key, by=BY, world_url=WORLDS["once"]))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


class RaceParams(BaseModel):
    orders: int = 20
    crash_prob: float = 0.0
    chaos: str = "default"
    decider: str | None = None
    drift: bool = False


@app.post("/api/race")
def api_race(p: RaceParams):
    if _race["proc"] and _race["proc"].poll() is None:
        return {"error": "race already running"}
    cmd = [sys.executable, "-m", "scripts.race", "--headless", "--orders", str(p.orders),
           "--crash-prob", str(p.crash_prob), "--chaos", p.chaos]
    if p.decider:
        cmd += ["--decider", p.decider]
    if p.drift:
        cmd += ["--drift"]
    log = open(ROOT / "race.log", "w")
    _race["proc"] = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                     env={k: v for k, v in os.environ.items() if k != "ONCE_CLIENT"})
    return {"started": True, "cmd": " ".join(cmd[1:])}


@app.post("/api/reset")
def api_reset():
    from scripts.seed_grant import reset_demo
    reset_demo()
    for url in WORLDS.values():
        try:
            _http.post(f"{url}/reset", json={"orders": 20, "seed": 7}, timeout=10)
        except Exception:
            pass
    admin_db().processes.delete_many({"_id": {"$in": list(AGENTS)}})
    return {"ok": True}


@app.get("/api/log")
def race_log():
    f = ROOT / "race.log"
    lines = f.read_text().splitlines()[-40:] if f.exists() else []
    return {"lines": lines}
