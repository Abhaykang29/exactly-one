"""Exactly-once layer tests against database once_test. Crashes are simulated by monkeypatching maybe_crash."""
from __future__ import annotations

import os
import threading
import time
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv()
if not os.environ.get("MONGODB_URI_ADMIN"):
    pytest.skip("MONGODB_URI_ADMIN not set (.env)", allow_module_level=True)

from once import Denied, EffectSpec, SchemaError, Unknown, crash, effects, memory, run_effect  # noqa: E402
from once.config import admin_client, db  # noqa: E402
from once.effects import AdaptResult  # noqa: E402
from once.grants import KILL, revoke  # noqa: E402
from scripts.seed_grant import reset_grant  # noqa: E402
from scripts.setup_db import create_collections  # noqa: E402

GRANT = "g_test"


class SimulatedCrash(Exception):
    pass


@pytest.fixture(scope="session", autouse=True)
def fresh_db():
    assert db().name == "once_test"
    admin_client().drop_database("once_test")
    create_collections(db())
    yield


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    KILL.clear()
    reset_grant(GRANT, db())
    db().flags.update_one({"_id": "memory"}, {"$set": {"on": True}}, upsert=True)
    monkeypatch.setattr(effects, "VERIFY_DELAYS", [0, 0, 0])
    yield
    KILL.clear()


class FakeWorld:
    """In-memory system keyed by reference. Counts calls."""

    def __init__(self, amount=1000):
        self.store: dict[str, dict] = {}
        self.acts = 0
        self.decides = 0
        self.amount = amount

    def decide(self):
        self.decides += 1
        return {"order_id": "ORD-1", "amount_cents": self.amount, "account": "acct_1"}

    def act(self, decision, key, field_map):
        self.acts += 1
        self.store[key] = {"refund_id": f"tx_{self.acts}", "amount_cents": decision["amount_cents"]}

    def verify(self, decision, key):
        return self.store.get(key)


def spec(w: FakeWorld, key: str | None = None, amount: int = 1000, **kw) -> EffectSpec:
    return EffectSpec(key=key or f"t{uuid.uuid4().hex[:6]}:ORD-1:refund", run_id="t", tenant="maple",
                      order_id="ORD-1", tool="bank.refund", grant_id=GRANT, amount_cents=amount,
                      decide=kw.pop("decide", w.decide), act=kw.pop("act", w.act),
                      verify=kw.pop("verify", w.verify), **kw)


def crash_at(monkeypatch, point: str, times: int = 1):
    left = {"n": times}

    def fake(p):
        if p == point and left["n"] > 0:
            left["n"] -= 1
            raise SimulatedCrash(p)
    monkeypatch.setattr(crash, "maybe_crash", fake)


def test_crash_after_act_no_duplicate(monkeypatch):
    w = FakeWorld()
    s = spec(w)
    crash_at(monkeypatch, "after_act")
    with pytest.raises(SimulatedCrash):
        run_effect(s)
    assert db().effects.find_one({"_id": s.key})["status"] == "sent"
    r = run_effect(s)
    assert w.acts == 1
    assert r["status"] == "proven" and r["reconciled"] is True


def test_decision_frozen(monkeypatch):
    w = FakeWorld()
    s = spec(w)
    crash_at(monkeypatch, "after_mark_sent", times=2)
    for _ in range(2):
        with pytest.raises(SimulatedCrash):
            run_effect(s)
    r = run_effect(s)
    assert w.decides == 1
    assert r["status"] == "proven"
    assert r["decision"]["amount_cents"] == 1000


def test_timeout_after_commit():
    w = FakeWorld()

    def act(decision, key, fm):
        w.act(decision, key, fm)
        raise Unknown("504 after commit")

    r = run_effect(spec(w, act=act))
    assert r["status"] == "proven" and w.acts == 1


def test_fake_ok_retries_then_proven():
    w = FakeWorld()
    calls = {"n": 0}

    def act(decision, key, fm):
        calls["n"] += 1
        if calls["n"] == 1:
            return                       # 200 OK, stored nothing
        w.act(decision, key, fm)

    r = run_effect(spec(w, act=act))
    assert r["status"] == "proven" and calls["n"] == 2 and r["attempts"] == 2


def test_never_confirmed_is_held():
    w = FakeWorld()
    r = run_effect(spec(w, verify=lambda d, k: None))
    assert r["status"] == "held" and r["evidence"] is None and r["attempts"] == 3
    r2 = run_effect(spec(w, key=r["_id"], verify=lambda d, k: None))
    assert r2["status"] == "held" and w.acts == 3


def test_amount_mismatch_is_held():
    w = FakeWorld()
    r = run_effect(spec(w, verify=lambda d, k: {"refund_id": "tx", "amount_cents": 1}))
    assert r["status"] == "held" and "mismatch" in r["held_reason"]


def test_revoked_is_denied():
    revoke(GRANT, by="test")
    w = FakeWorld()
    s = spec(w)
    with pytest.raises(Denied):
        run_effect(s)
    r = db().effects.find_one({"_id": s.key})
    assert r["status"] == "denied" and w.acts == 0
    assert db().effects.count_documents({"_id": s.key, "status": "intent"}) == 0
    assert db().grant_usage.find_one({"_id": GRANT})["uses"] == 0


def test_budget_is_atomic():
    budget = db().grants.find_one({"_id": GRANT})["budget_cents"]
    amount = int(budget * 0.6)
    db().grants.update_one({"_id": GRANT}, {"$set": {"max_per_action_cents": budget}})
    out: list = []
    barrier = threading.Barrier(2)

    def go(i):
        w = FakeWorld(amount)
        barrier.wait()
        try:
            out.append(run_effect(spec(w, key=f"budget:{i}:{uuid.uuid4().hex[:4]}", amount=amount))["status"])
        except Denied:
            out.append("denied")

    ts = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(out) == ["denied", "proven"]
    assert db().grant_usage.find_one({"_id": GRANT})["spent_cents"] == amount


def test_schema_error_adapts_and_records_first_try_failure():
    w = FakeWorld()

    def act(decision, key, fm):
        if not fm:
            raise SchemaError({"error": "invalid refund request"})
        w.act(decision, key, fm)

    def adapt(err, key):
        return None if err is None else AdaptResult({"order_id": "order_ref"})

    r = run_effect(spec(w, act=act, adapt=adapt))
    assert r["status"] == "proven" and r["first_try_failed"] is True and r["field_map"] == {"order_id": "order_ref"}


# ---------------- memory (need Voyage + the memory_vec index; tests 8-9)

needs_voyage = pytest.mark.skipif(not os.environ.get("VOYAGE_API_KEY"), reason="VOYAGE_API_KEY not set")


@needs_voyage
def test_memory_scope():
    """A memory for tenant maple is never recalled for birch. Uses the real `once` db (vector index lives there)."""
    real = admin_client()[os.environ.get("ONCE_REAL_DB", "once")]
    system = "testsys_" + uuid.uuid4().hex[:6]
    text = "bank POST /bank/refunds 422 invalid refund request"
    mid = "mem_test_" + uuid.uuid4().hex[:6]
    from datetime import datetime, timedelta, timezone
    real.memories.insert_one({"_id": mid, "kind": "procedural", "scope": {"system": system, "tenant": "maple"},
                              "problem_text": text, "fix": {"field_map": {"a": "b"}},
                              "embedding": memory.embed([text], "document")[0], "source_receipts": ["x"],
                              "status": "active", "successes": 1, "failures": 0,
                              "created_at": datetime.now(timezone.utc),
                              "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)})
    os.environ["ONCE_DB"] = real.name
    try:
        hit = None
        for _ in range(60):
            hit = memory.recall(text, system, "maple")
            if hit:
                break
            time.sleep(1)
        assert hit and hit["_id"] == mid
        assert memory.recall(text, system, "birch") is None
    finally:
        real.memories.delete_one({"_id": mid})
        os.environ["ONCE_DB"] = "once_test"


def test_only_proven_becomes_memory(monkeypatch):
    monkeypatch.setattr(memory, "embed", lambda texts, input_type: [[0.1, 0.2, 0.3] for _ in texts])
    monkeypatch.setattr(memory, "recall", lambda *a, **k: None)
    scope = {"system": "bank", "tenant": "maple"}

    def make(verify_ok: bool):
        w = FakeWorld()
        cid_box = {}

        def act(decision, key, fm):
            if not fm:
                raise SchemaError({"error": "invalid refund request"})
            w.act(decision, key, fm)

        def adapt(err, key):
            if err is None:
                return None
            cid_box["cid"] = memory.save_candidate({"order_id": "order_ref"}, "p", scope, key)
            return AdaptResult({"order_id": "order_ref"}, candidate_id=cid_box["cid"])

        verify = w.verify if verify_ok else (lambda d, k: None)
        return spec(w, act=act, adapt=adapt, verify=verify), cid_box

    s, box = make(verify_ok=False)
    assert run_effect(s)["status"] == "held"
    assert db().candidates.find_one({"_id": box["cid"]})["status"] == "candidate"
    assert db().memories.count_documents({"source_receipts": s.key}) == 0

    s, box = make(verify_ok=True)
    assert run_effect(s)["status"] == "proven"
    assert db().candidates.find_one({"_id": box["cid"]})["status"] == "promoted"
    m = db().memories.find_one({"source_receipts": s.key})
    assert m and m["status"] == "active"
