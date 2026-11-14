"""Only proven fixes become memories (Track 2).

candidates: fixes tried but not yet proven.  memories: fixes a proven receipt confirmed.
Recall is $vectorSearch on `memory_vec`, always filtered by scope (system + tenant) and status.
"""
from __future__ import annotations

import os
import uuid
from datetime import timedelta

import httpx

from once.config import db, need
from once.grants import now

EMBED_URL = "https://ai.mongodb.com/v1/embeddings"
SCORE_MIN = float(os.environ.get("MEMORY_SCORE_MIN", "0.75"))


def embed(texts: list[str], input_type: str) -> list[list[float]]:
    r = httpx.post(
        EMBED_URL,
        headers={"Authorization": f"Bearer {need('VOYAGE_API_KEY')}"},
        json={"input": texts, "model": os.environ.get("EMBED_MODEL", "voyage-4-lite"), "input_type": input_type},
        timeout=20.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"embeddings API {r.status_code}: {r.text[:200]}")
    body = r.json()
    data = body.get("data", body.get("embeddings"))
    return [d["embedding"] if isinstance(d, dict) else d for d in data]


def memory_on() -> bool:
    f = db().flags.find_one({"_id": "memory"})
    return True if f is None else bool(f.get("on"))


def recall(problem_text: str, system: str, tenant: str) -> dict | None:
    if not memory_on():
        return None
    scope = {"scope.system": system, "scope.tenant": tenant, "status": "active"}
    if db().memories.count_documents(scope, limit=1) == 0:
        return None
    qv = embed([problem_text], "query")[0]
    hits = list(db().memories.aggregate([
        {"$vectorSearch": {"index": "memory_vec", "path": "embedding", "queryVector": qv,
                           "filter": scope, "numCandidates": 50, "limit": 3}},
        {"$project": {"embedding": 0, "score": {"$meta": "vectorSearchScore"}}},
    ]))
    if hits and hits[0]["score"] >= SCORE_MIN:
        return hits[0]
    return None


def save_candidate(field_map: dict, problem_text: str, scope: dict, key: str) -> str:
    cid = "cand_" + uuid.uuid4().hex[:10]
    db().candidates.insert_one({
        "_id": cid, "scope": scope, "problem_text": problem_text, "fix": {"field_map": field_map},
        "from_receipt": key, "status": "candidate", "created_at": now(),
    })
    print(f"[memory] new candidate {cid} (not a memory until proven)", flush=True)
    return cid


def _promote(cid: str, key: str) -> str | None:
    c = db().candidates.find_one({"_id": cid})
    if c is None or c.get("status") != "candidate":
        return None
    same = db().memories.find_one({"scope": c["scope"], "status": "active", "fix": c["fix"]}, {"embedding": 0})
    if same:
        db().memories.update_one({"_id": same["_id"]},
                                 {"$addToSet": {"source_receipts": key}, "$inc": {"successes": 1}})
        mid = same["_id"]
    else:
        mid = "mem_" + uuid.uuid4().hex[:10]
        t = now()
        db().memories.insert_one({
            "_id": mid, "kind": "procedural", "scope": c["scope"], "problem_text": c["problem_text"],
            "fix": c["fix"], "embedding": embed([c["problem_text"]], "document")[0],
            "source_receipts": [key], "status": "active", "successes": 1, "failures": 0,
            "created_at": t,
            "expires_at": t + timedelta(seconds=int(os.environ.get("MEMORY_TTL_SECONDS", "604800"))),
        })
    db().candidates.update_one({"_id": cid}, {"$set": {"status": "promoted", "memory_id": mid}})
    print(f"[memory] promoted {cid} -> {mid} (proven by {key})", flush=True)
    return mid


def on_proven(receipt: dict) -> None:
    if not receipt or receipt.get("status") != "proven":
        return
    if receipt.get("candidate_id"):
        _promote(receipt["candidate_id"], receipt["_id"])
    failed = set(receipt.get("memory_failed") or [])
    for mid in receipt.get("memory_used") or []:
        if mid and mid not in failed:
            db().memories.update_one({"_id": mid}, {"$inc": {"successes": 1}})


def on_memory_failed(mid: str, key: str) -> None:
    m = db().memories.find_one_and_update({"_id": mid}, {"$inc": {"failures": 1}},
                                          projection={"embedding": 0}, return_document=True)
    if m and m.get("failures", 0) >= 2 and m.get("status") == "active":
        db().memories.update_one({"_id": mid}, {"$set": {"status": "quarantined"}})
        print(f"[memory] QUARANTINED {mid} after {m['failures']} failures (last: {key})", flush=True)


def on_failed(receipt: dict) -> None:
    if not receipt:
        return
    failed = set(receipt.get("memory_failed") or [])
    for mid in receipt.get("memory_used") or []:
        if mid and mid not in failed:
            on_memory_failed(mid, receipt["_id"])
