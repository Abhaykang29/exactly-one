"""Schema-drift adaptation: memory first, then schema + LLM (deterministic fallback)."""
from __future__ import annotations

import os

from pydantic import BaseModel

from agent.tools import V1_REFUND_FIELDS, World
from once import memory
from once.config import db, tenant
from once.effects import AdaptResult
from once.errors import SchemaError

DEFAULT_ERROR = "invalid refund request"


class FieldMap(BaseModel):
    old_to_new: dict[str, str]


def problem_text(err: SchemaError | None) -> str:
    e = (err.body.get("error") if err else None) or DEFAULT_ERROR
    return f"bank POST /bank/refunds 422 {e}"


def _llm_map(old: list[str], new: list[str]) -> dict | None:
    if os.environ.get("DECIDER", "llm") != "llm" or not os.environ.get("MODEL"):
        return None
    try:
        from langchain.chat_models import init_chat_model
        m = init_chat_model(os.environ["MODEL"], temperature=0).with_structured_output(FieldMap)
        r = m.invoke(f"A bank API renamed its refund request fields. Old required fields: {old}. "
                     f"New required fields: {new}. Return old_to_new mapping each old field name to the new "
                     f"name that carries the same value (include unchanged fields mapped to themselves).")
        fm = {k: v for k, v in r.old_to_new.items() if k in old and v in new}
        return fm if set(fm.values()) == set(new) else None
    except Exception as e:
        print(f"[adapt] LLM field map failed ({e!r}); using positional fallback", flush=True)
        return None


def derive_field_map(world: World, schema: dict | None = None) -> dict:
    new = (schema or world.bank_schema())["required"]
    fm = _llm_map(V1_REFUND_FIELDS, new)
    if fm is None:
        fm = dict(zip(V1_REFUND_FIELDS, new))
    return {k: v for k, v in fm.items() if k != v}


def make_adapt_bank(world: World):
    def adapt_bank(err: SchemaError | None, key: str) -> AdaptResult | None:
        text = problem_text(err)
        schema: dict | None = None
        # 1. memory first: only a proven memory may shape the request (also before the first try),
        #    and only if the bank still speaks the schema version the fix was proven against
        m = memory.recall(text, "bank", tenant())
        if m:
            r = db().effects.find_one({"_id": key}, {"memory_failed": 1}) or {}
            schema = world.bank_schema()
            learned_on = m["fix"].get("schema_version")
            if m["_id"] in (r.get("memory_failed") or []):
                pass
            elif learned_on != schema.get("version"):
                print(f"[memory] skipped {m['_id']}: learned on bank schema v{learned_on}, "
                      f"bank is now v{schema.get('version')}", flush=True)
            else:
                print(f"[memory] recalled {m['_id']} (learned from {m['source_receipts'][0]})", flush=True)
                return AdaptResult(m["fix"]["field_map"], memory_id=m["_id"])
        if err is None:
            return None                                    # no usable memory: attempt normally
        # 2. read the schema and derive old -> new names; it is only a candidate until proven
        schema = world.bank_schema()                       # re-read: the 422 is newer than any cached schema
        fm = derive_field_map(world, schema)
        cid = None
        if fm:
            cid = memory.save_candidate(fm, text, {"system": "bank", "tenant": tenant()}, key,
                                        schema_version=schema.get("version"))
        print(f"[adapt] bank schema v{schema.get('version')}; field map {fm or 'identity'}", flush=True)
        return AdaptResult(fm, candidate_id=cid)

    return adapt_bank
