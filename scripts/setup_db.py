"""Idempotent Atlas setup: collections, $jsonSchema validator, indexes, memory_vec vector index, seeds.

python -m scripts.setup_db [--skip-vector]
"""
from __future__ import annotations

import argparse
import time

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database
from pymongo.errors import CollectionInvalid, OperationFailure
from pymongo.operations import SearchIndexModel

from once.config import admin_client, admin_db, lg_db_name, once_db_name
from scripts.seed_grant import reset_demo

COLLECTIONS = ["effects", "grants", "grant_usage", "candidates", "memories", "flags", "processes", "runs",
               "results"]

EFFECTS_VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["_id", "run_id", "tool", "decision", "authority", "status", "created_at"],
    "properties": {
        "_id": {"bsonType": "string"},
        "status": {"enum": ["intent", "sent", "proven", "held", "denied", "compensated"]},
        "decision": {"bsonType": "object"},
        "authority": {"bsonType": "object"},
    },
    "anyOf": [
        {"properties": {"status": {"enum": ["intent", "sent", "held", "denied"]}}},
        {"properties": {"status": {"enum": ["proven"]}, "evidence": {"bsonType": "object"}},
         "required": ["evidence"]},
        {"properties": {"status": {"enum": ["compensated"]}}, "required": ["evidence"]},
    ],
}}


def create_collections(d: Database) -> None:
    existing = set(d.list_collection_names())
    for c in COLLECTIONS:
        if c == "effects":
            if c in existing:
                d.command("collMod", "effects", validator=EFFECTS_VALIDATOR, validationLevel="strict")
            else:
                d.create_collection("effects", validator=EFFECTS_VALIDATOR, validationLevel="strict")
        elif c not in existing:
            try:
                d.create_collection(c)
            except CollectionInvalid:
                pass
    d.effects.create_index([("run_id", ASCENDING), ("status", ASCENDING)])
    d.effects.create_index([("authority.grant_id", ASCENDING)])
    d.effects.create_index([("compensates", ASCENDING)])
    d.memories.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
    d.results.create_index([("agent", ASCENDING), ("created_at", DESCENDING)])


def vector_def(dims: int) -> dict:
    return {"fields": [
        {"type": "vector", "path": "embedding", "numDimensions": dims, "similarity": "cosine"},
        {"type": "filter", "path": "scope.system"},
        {"type": "filter", "path": "scope.tenant"},
        {"type": "filter", "path": "status"},
    ]}


def ensure_vector_index(d: Database, wait: bool = True) -> str:
    from once.memory import embed
    dims = len(embed(["probe: bank refund schema changed"], "document")[0])
    print(f"  embedding dimension from Atlas Model API: {dims}")
    names = {i["name"]: i for i in d.memories.list_search_indexes()}
    if "memory_vec" not in names:
        d.memories.create_search_index(SearchIndexModel(definition=vector_def(dims), name="memory_vec",
                                                        type="vectorSearch"))
        print("  created memory_vec, waiting until queryable ...")
    else:
        have = next((f.get("numDimensions") for f in
                     (names["memory_vec"].get("latestDefinition") or {}).get("fields", []) if f.get("type") == "vector"),
                    None)
        print(f"  memory_vec exists with numDimensions={have}")
        if have != dims:
            d.memories.update_search_index("memory_vec", vector_def(dims))
            print(f"  updated memory_vec numDimensions {have} -> {dims}, waiting until queryable ...")
            time.sleep(5)
    deadline = time.time() + 300
    while wait:
        idx = next((i for i in d.memories.list_search_indexes() if i["name"] == "memory_vec"), None)
        if idx and idx.get("queryable"):
            return idx.get("status", "READY")
        if time.time() > deadline:
            return f"NOT READY ({idx and idx.get('status')})"
        time.sleep(3)
    return "created"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--skip-vector", action="store_true")
    a = p.parse_args()
    d = admin_db()
    print(f"setup {once_db_name()} (+ {lg_db_name()} for checkpoints)")
    create_collections(d)
    reset_demo(d)
    d.flags.update_one({"_id": "memory"}, {"$setOnInsert": {"on": True}}, upsert=True)
    vec = "skipped"
    if not a.skip_vector:
        try:
            vec = ensure_vector_index(d)
        except (OperationFailure, RuntimeError) as e:
            vec = f"FAILED: {e}"

    names = set(d.list_collection_names())
    print("\nchecklist")
    for c in COLLECTIONS:
        print(f"  [{'x' if c in names else ' '}] collection {c}")
    opts = d.command("listCollections", filter={"name": "effects"})["cursor"]["firstBatch"][0]["options"]
    print(f"  [{'x' if 'validator' in opts else ' '}] effects $jsonSchema validator")
    for c in ("effects", "memories", "results"):
        for name, info in d[c].index_information().items():
            if name != "_id_":
                ttl = f" ttl={info['expireAfterSeconds']}" if "expireAfterSeconds" in info else ""
                print(f"  [x] index {c}.{name}{ttl}")
    print(f"  [{'x' if vec in ('READY',) else ' '}] vector index memory_vec: {vec}")
    for g in d.grants.find():
        print(f"  [x] grant {g['_id']} tools={g['tools']} revoked={g['revoked']}")
    for u in d.grant_usage.find():
        print(f"  [x] grant_usage {u['_id']} spent={u['spent_cents']} uses={u['uses']}")
    print(f"  [x] flags.memory on={d.flags.find_one({'_id': 'memory'})['on']}")
    admin_client().close()


if __name__ == "__main__":
    main()
