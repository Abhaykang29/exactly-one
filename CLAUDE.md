# Exactly Once: Build Spec

> **Claude Code: read this whole file before writing any code.** It is the source of truth for the project.
> If a library's real API differs from what is written here, check the installed version
> (`python -c "import x; help(x.Y)"`), use the real API, and add one line under **Deviations** at the bottom.
> Work only on the milestone you are asked for. When its acceptance check passes, commit and stop.

---

## 0. Context

- **Event:** MongoDB "Agents in Context" hackathon, Toronto, Saturday Nov 14, 2026. Hacking runs 10:45 AM–5:00 PM ET. **Code freeze 4:00 PM.**
- **Track:** 3, "Finish the Job." The same records also answer Track 1 (audit) and Track 2 (memory).
- **Rules that bind the code:**
  - All code is written today. This spec was written before the event; the README must say so.
  - The repo is **public**, so secrets live only in `.env`, which git ignores.
  - **No Streamlit** (banned). No dashboard as the product. The web page is a demo console for the layer.
  - Must run on the hackathon's **MongoDB Atlas sandbox cluster** (`Cluster0`, M10).
- **Team:**
  - **Abhay:** lead dev. Owns `once/`, `agent/` and `scripts/`.
  - **Teammate T:** owns `mocks/`, then `ui/`.
  - **Friend A:** runs scripts, presses the crash button. Writes no code.
  - **Friend B:** pitch and video. Writes no code.

## 1. The product in one paragraph

AI agents that take real actions (refunds, restocks, emails) can crash mid-job. The standard setup, LangGraph with
MongoDB's checkpointer, resumes after a crash, but **LangGraph re-runs the interrupted node, including its API calls**, so
customers can get refunded twice. Agents also report "done" when an action never landed. **Exactly Once** is a layer that
gives every action a **receipt** in Atlas: permission is checked before it starts (Track 1), it counts as done only
when the real system confirms it (Track 3), and only proven fixes become memories (Track 2). **The demo** races the
standard agent against the same agent with our layer, crashes both repeatedly, and counts double refunds: standard X, ours 0.

**Motto:** *Nothing counts without a receipt.*

## 2. Ground rules for Claude Code

1. Python 3.11, type hints, small modules, plain functions over classes where possible.
2. **Demo reliability beats features.** Simple, obvious code. No speculative abstractions.
3. Secrets come only from env, via `once/config.py` (python-dotenv). Never print a URI or key.
4. Log one line for every receipt transition, e.g. `[once] r42:ORD-007:refund intent -> sent`.
5. Fail loudly with clear messages. Don't swallow exceptions, except where this spec says the outcome is "unknown".
6. When a milestone's acceptance check passes:
   - `git add -A && git commit -m "M<n>: <summary>" && git push`
   - Append a timestamped line to the README section "Built during the event".
7. Stay inside the file layout below. Don't start the next milestone until asked.
8. Never use Streamlit, and never add a frontend framework or build step: the UI is one HTML file with vanilla JS.

## 3. Stack

| Need | Use |
|---|---|
| Database | `pymongo` (sync), Atlas `Cluster0` |
| Agent graph | `langgraph` + `langgraph-checkpoint-mongodb` (`MongoDBSaver`) |
| LLM | `langchain` `init_chat_model(os.environ["MODEL"])` + provider package (`langchain-anthropic` or `langchain-openai`), structured output with Pydantic v2 |
| Mock systems and UI server | `fastapi` + `uvicorn` |
| HTTP client | `httpx` (timeouts!) |
| Embeddings | Voyage through MongoDB's Atlas Model API over plain HTTP (below) |
| Tracing | LangSmith, enabled by env only (`LANGSMITH_TRACING=true`) |
| Tests | `pytest` |

**Embeddings call** (don't use any other endpoint):
```
POST https://ai.mongodb.com/v1/embeddings
Authorization: Bearer $VOYAGE_API_KEY
{"input": ["..."], "model": "$EMBED_MODEL", "input_type": "document" | "query"}
```
Use `input_type="document"` when storing and `"query"` when searching. Read the vector length from the first response
and use it as `numDimensions` for the vector index.

`requirements.txt` (unpinned; after the first install run `pip freeze > requirements.lock`):
```
pymongo
fastapi
uvicorn
httpx
pydantic
python-dotenv
langgraph
langgraph-checkpoint-mongodb
langchain
langchain-anthropic
langchain-openai
langsmith
pytest
```

## 4. Environment (`.env.example`, committed; `.env`, never committed)

```
# Atlas: admin user (setup, mocks, UI) and restricted agent user (agents only)
MONGODB_URI_ADMIN=mongodb+srv://<admin_user>:<password>@<cluster-host>/?retryWrites=true&w=majority
MONGODB_URI_AGENT=mongodb+srv://agent_user:<password>@<cluster-host>/?retryWrites=true&w=majority
ONCE_DB=once
LG_DB=lg

# LLM (fast, cheap model, in init_chat_model "provider:model" format)
MODEL=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
DECIDER=llm            # llm | stub   (stub = fast fake decisions for big chaos runs)

# Voyage via Atlas Model API
VOYAGE_API_KEY=
EMBED_MODEL=voyage-4-lite

# LangSmith
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=exactly-once

# Worlds (two copies of the fake systems, one per agent)
WORLD_BASELINE_URL=http://localhost:8001
WORLD_ONCE_URL=http://localhost:8002

# Demo knobs
CRASH_PROB=0
TENANT=maple
GRANT_ID=g_ops_demo
MEMORY_TTL_SECONDS=604800
```

`.gitignore`: `.env`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `requirements.lock` is fine to commit.

## 5. Repo layout

```
exactly-once/
  CLAUDE.md            # this file
  PROMPTS.md           # day plan + prompts
  README.md
  .env.example  .gitignore  requirements.txt
  once/                # THE LAYER (Abhay)
    __init__.py        # exports run_effect, EffectSpec, Denied, Stopped, SchemaError
    config.py          # env loading, Mongo clients (admin + agent), db handles
    effects.py         # run_effect(): the five rules
    grants.py          # authorize_and_insert_intent() transaction, revoke(), kill-switch watcher
    memory.py          # candidates, promote, vector recall, quarantine
    compensate.py      # reverse a proven effect as its own exactly-once effect
    crash.py           # maybe_crash(point)
  agent/               # the refund agent (Abhay)
    decide.py          # RefundDecision model; llm + stub deciders
    tools.py           # HTTP clients for the fake systems: act + verify functions
    adapt.py           # schema-drift adaptation (memory first, then schema + LLM)
    graph_baseline.py  # standard LangGraph agent (naive tool calls)
    graph_once.py      # identical graph, tools wrapped in run_effect
    run_agent.py       # CLI: --agent baseline|once --run-id --world-url --orders
  mocks/               # THE FAKE WORLD (Teammate T)
    app.py  bank.py  inventory.py  email.py  orders.py  chaos.py  truth.py
  ui/                  # DEMO CONSOLE (Teammate T, after mocks)
    server.py
    static/index.html
  scripts/
    setup_db.py        # collections, validators, indexes, vector index
    seed_grant.py      # create/reset the demo grant
    race.py            # run both agents side by side with a crash supervisor
    chaos_run.py       # many races in a row → results table
  tests/
    test_once.py
```

## 6. Data model (Atlas)

### 6.1 Database `once`

**`effects`**: one receipt per action. `_id` is the idempotency key.
```js
{
  _id: "r42:ORD-007:refund",          // run_id:order_id:action  (unique by definition)
  run_id: "r42", tenant: "maple", order_id: "ORD-007",
  tool: "bank.refund",                 // bank.refund | inventory.restock | email.send | bank.reverse
  decision: { amount_cents: 4999, account: "acct_007" },   // FROZEN at intent; never re-asked
  inputs: [ { kind: "order", ref: "ORD-007", trust: "system" },
            { kind: "customer_note", trust: "untrusted" } ],
  authority: { grant_id: "g_ops_demo", issued_by: "ops-manager@maple.test",
               amount_cents: 4999, authorized_at: ISODate },
  memory_used: [ "mem_..." ],          // memories that shaped this action
  candidate_id: null,                  // a not-yet-proven fix tried during this action
  status: "intent",                    // intent | sent | proven | held | denied | compensated
  attempts: 0,
  first_try_failed: false,             // true if the first act hit a schema error (Track 2 metric)
  evidence: null,                      // set when proven, e.g. { refund_id, amount_cents, checked_at }
  compensates: null,                   // for bank.reverse receipts: the key it reverses
  held_reason: null,
  created_at, sent_at, proven_at
}
```

**`grants`**: the rules. The **agent user can only read this collection.**
```js
{ _id: "g_ops_demo", agent: "refund-agent", issued_by: "ops-manager@maple.test",
  tools: ["bank.refund","inventory.restock","email.send"],
  max_per_action_cents: 50000, budget_cents: 300000,
  expires_at: ISODate, revoked: false, revoked_at: null, revoked_by: null }
```
Also seed `g_manager_rollback` (tools: `["bank.reverse"]`, issued_by `ops-manager@maple.test`), used only by the UI for reversals.

**`grant_usage`**: the spend counter. The agent can update it; it can't touch the rules.
```js
{ _id: "g_ops_demo", spent_cents: 0, uses: 0, revoked: false }
```

**`candidates`**: fixes tried but not yet proven.
```js
{ _id, scope: { system: "bank", tenant: "maple" }, problem_text, fix: { field_map: {...} },
  from_receipt: "r42:ORD-012:refund", status: "candidate", created_at }
```

**`memories`**: only fixes that a **proven** receipt confirmed.
```js
{ _id: "mem_...", kind: "procedural", scope: { system: "bank", tenant: "maple" },
  problem_text, fix: { field_map: {...} }, embedding: [..],
  source_receipts: ["r42:ORD-012:refund"], status: "active",   // active | quarantined
  successes: 1, failures: 0, created_at, expires_at }
```

**`flags`**: `{ _id: "memory", on: true }`, written by the UI and read by the agent before each effect.
**`processes`**: `{ _id: "baseline"|"once", pid, run_id, started_at }`.
**`runs`**: `{ _id: run_id, agent, orders, crashes, claimed_done: [order_ids], finished_at }`.
**`results`**: `{ run_id, agent, orders, crashes, dup_refunds, dup_restocks, dup_emails, missed, false_done, first_try_fails, created_at }`.

### 6.2 Database `lg`
LangGraph checkpoints (`MongoDBSaver`). Configure it with `db_name=LG_DB`. Check the constructor signature of the installed version.

### 6.3 Validator on `effects` (`$jsonSchema`, set in `setup_db.py`)
- Required: `_id, run_id, tool, decision, authority, status, created_at`.
- `status` enum: `intent, sent, proven, held, denied, compensated`.
- `anyOf`:
  - `{status ∈ [intent, sent, held, denied]}`
  - `{status: proven, required: [evidence], evidence: object}`
  - `{status: compensated, required: [evidence]}`

Order (intent → sent → proven) is enforced by **conditional updates** (the filter includes the allowed previous status), not by the validator.

### 6.4 Indexes (in `setup_db.py`, idempotent)
- `effects`: `{run_id:1, status:1}`, `{"authority.grant_id":1}`, `{compensates:1}`
- `memories`: TTL `{expires_at:1}` with `expireAfterSeconds=0`
- `memories` vector search index named `memory_vec` (type `vectorSearch`):
  `{"fields":[{"type":"vector","path":"embedding","numDimensions":<D>,"similarity":"cosine"},`
  `{"type":"filter","path":"scope.system"},{"type":"filter","path":"scope.tenant"},{"type":"filter","path":"status"}]}`.
  Create it with `create_search_index(SearchIndexModel(..., type="vectorSearch"))`, then poll `list_search_indexes()` until it's queryable (about a minute).
- `results`: `{agent:1, created_at:-1}`

### 6.5 Atlas users and roles (done manually in the Atlas UI, see PROMPTS.md)
- `admin_user`: Atlas admin. Used by `setup_db.py`, `mocks/`, `ui/`, `race.py`, `chaos_run.py`, `seed_grant.py` and the tests.
- `once/config.py` exposes `db()`. It uses `MONGODB_URI_AGENT` when env `ONCE_CLIENT=agent` (set by `run_agent.py` for its
  subprocesses), and `MONGODB_URI_ADMIN` otherwise. So the same `once/` code runs as the agent in agents and as the manager in the UI.
- `agent_user` with custom role **`agent_role`**:
  - built-in `readWrite` on database `lg`
  - `once.effects`: find, insert, update, changeStream
  - `once.grants`: find, changeStream ← **read-only rules**
  - `once.grant_usage`: find, update
  - `once.candidates`, `once.memories`: find, insert, update
  - `once.flags`: find
  - `once.processes`, `once.runs`: find, insert, update
  - **No remove anywhere. No write on `grants`.**
- Agents (`agent/run_agent.py`) connect **only** with `MONGODB_URI_AGENT`.

### 6.6 Databases `world_baseline` and `world_once`: owned by the mocks (section 7)

## 7. Mock world (Teammate T builds this)

One FastAPI app, run twice with different env:
```
WORLD_DB=world_baseline uvicorn mocks.app:app --port 8001
WORLD_DB=world_once     uvicorn mocks.app:app --port 8002
```
All state lives in Mongo (`WORLD_DB`, admin URI), so it survives restarts and is the **ground truth** for metrics.
No idempotency keys are honored: the fake bank behaves like many real systems. Every write stores the client's
`reference` string so it can be looked up later.

| Method | Path | Body / query | Behavior |
|---|---|---|---|
| POST | `/reset` | `{orders:50, seed:7}` | Drop world collections, seed N delayed orders deterministically, set all chaos off, schema v1 |
| GET | `/orders` | `?status=delayed` | `[{order_id:"ORD-001", customer, email, sku, qty, amount_cents, account, note}]` |
| GET | `/bank/schema` | | `{"version":1,"required":["order_id","amount_cents","account","reference"]}`, or v2 `["order_ref","refund_amount_cents","account","reference"]` |
| POST | `/bank/refunds` | v1 `{order_id, amount_cents, account, reference}` or v2 renamed fields | Wrong schema → **422** `{"error":"invalid refund request","hint":"see GET /bank/schema"}`. Else store `{refund_id, order_id, amount_cents, reference, status:"settled", created_at}`, return 201 `{refund_id, status}` |
| GET | `/bank/refunds` | `?reference=` or `?order_id=` | list |
| GET | `/bank/refunds/{refund_id}` | | one refund |
| POST | `/bank/refunds/{refund_id}/reverse` | `{reference}` | Mark refund `reversed`, store a reversal with `reference`, 200 |
| GET | `/bank/reversals` | `?reference=` | list |
| POST | `/inventory/restocks` | `{sku, qty, order_id, reference}` | Store, 200 `{ok:true}` |
| GET | `/inventory/restocks` | `?reference=` or `?order_id=` | list |
| POST | `/email/messages` | `{to, subject, body, order_id, reference}` | Store, 200 `{ok:true}` |
| GET | `/email/messages` | `?reference=` or `?order_id=` | list |
| POST | `/chaos` | `{system, mode, rate, enabled}` | Set a chaos switch |
| GET | `/chaos` | | current switches |
| GET | `/truth/summary` | | Ground-truth counts (below) |

**Chaos switches** (each has `rate` 0–1 and `enabled`):

| system | mode | What happens |
|---|---|---|
| bank | `fail_before_commit` | 503, nothing stored |
| bank | `timeout_after_commit` | **Store the refund**, then sleep 3s and return 504. Clients use a 2s timeout, so they see a timeout |
| bank | `schema_v2` | (rate ignored) Require v2 field names; v1 bodies get 422 |
| inventory | `fake_ok` | Return 200 `{ok:true}` but **store nothing** |
| email | `fail_before_commit` | 503, nothing stored |

**`/truth/summary`** (computed against the seeded orders):
```js
{ orders: 50,
  refunds_total, orders_refunded, dup_refunds,     // dup = sum over orders of max(0, count-1); reversed refunds still count as paid-then-reversed
  restocks_total, dup_restocks, emails_total, dup_emails,
  missing_refunds, missing_restocks, missing_emails,
  per_order: { "ORD-001": {refunds:1, restocks:1, emails:1}, ... } }
```

**Mock acceptance check:**
```
curl -s -X POST localhost:8001/reset -H 'content-type: application/json' -d '{"orders":50}'
curl -s 'localhost:8001/orders?status=delayed' | python -c "import sys,json;print(len(json.load(sys.stdin)))"   # 50
# refund + lookup by reference works; timeout_after_commit at rate 1 → client sees 504 but GET by reference finds it;
# fake_ok at rate 1 → 200 but GET finds nothing; schema_v2 → v1 body gets 422, v2 body 201.
```

## 8. The layer: `once/`

### 8.1 Public API
```python
@dataclass
class EffectSpec:
    key: str                     # run_id:order_id:action
    run_id: str
    tenant: str
    order_id: str
    tool: str                    # "bank.refund" etc.
    grant_id: str
    amount_cents: int            # 0 for non-money actions
    decide: Callable[[], dict]   # called AT MOST ONCE per key, ever
    act: Callable[[dict, str, dict | None], None]   # (decision, key, field_map) → raises Timeout/Unavailable/SchemaError
    verify: Callable[[dict, str], dict | None]       # (decision, key) → evidence or None. Looks up by reference=key
    adapt: Callable[[SchemaError, str], "AdaptResult"] | None = None
    inputs: list[dict] = field(default_factory=list)

def run_effect(spec: EffectSpec) -> dict   # returns the final receipt document
```
Exceptions: `Denied` (permission), `Stopped` (kill switch fired), `SchemaError` (422 from a system), `Unknown` (timeout or 5xx: outcome unknown).

### 8.2 `run_effect`: the five rules
```
doc = effects.find_one({_id: key})
if doc and doc.status in {proven, compensated, denied}: return doc                  # already settled

if doc is None:                                                                     # RULE 1: write it down first
    decision = spec.decide()                                                        # RULE 3: decided once, then frozen
    try:
        grants.authorize_and_insert_intent(spec, decision)                          # one transaction (8.3)
    except Denied:
        insert {_id: key, status: denied, ...}; raise
    except DuplicateKeyError:                                                       # RULE 2: database refuses doubles
        doc = effects.find_one({_id: key}); (continue with that doc)
    doc = effects.find_one({_id: key})

decision = doc.decision                                                             # always the frozen one

if doc.status == sent:                                                              # RULE 5: after a crash, check first
    ev = spec.verify(decision, key)
    if ev: mark_proven(key, ev, reconciled=True); return receipt

field_map = None
for attempt in 1..3:
    if KILL.is_set(): raise Stopped
    effects.update_one({_id:key, status:{$in:[intent, sent]}},
                       {$set:{status:sent, sent_at:now}, $inc:{attempts:1}})          # sent BEFORE act
    crash.maybe_crash("after_mark_sent")
    try:
        spec.act(decision, key, field_map)
    except SchemaError as e:
        if attempt == 1: set first_try_failed = true
        if spec.adapt is None: raise
        r = spec.adapt(e, key)          # returns field_map + memory_id or candidate_id; record them on the receipt
        field_map = r.field_map; continue
    except Unknown:
        pass                            # timeout/5xx: did it land? verify decides
    crash.maybe_crash("after_act")
    ev = verify_with_backoff(spec.verify, decision, key, delays=[0.3, 0.8, 1.5])   # RULE 4: done means proven
    if ev:
        mark_proven(key, ev)            # filter status: sent; sets evidence, proven_at
        memory.on_proven(receipt)       # promote candidate / count memory success
        return receipt
mark_held(key, "not confirmed after 3 attempts"); memory.on_failed(receipt); return receipt
```
- `mark_proven` and `mark_held` use conditional filters (`status: sent`). If the update matches 0 documents, re-read and return.
- Also check the amount in the evidence against `decision.amount_cents`. A mismatch is a hold, never proven.
- Log every transition.

### 8.3 `grants.py`: permission as one atomic write (Track 1)
```
def authorize_and_insert_intent(spec, decision):
  with client.start_session() as s:
    s.with_transaction(lambda s: _txn(s, spec, decision))     # retries transient write conflicts automatically

def _txn(s, spec, decision):
  g = grants.find_one({_id: spec.grant_id}, session=s)        # rules (agent can only read)
  deny unless: g exists, not g.revoked, now < g.expires_at, spec.tool in g.tools,
               spec.amount_cents <= g.max_per_action_cents
  u = grant_usage.find_one_and_update(
        {_id: spec.grant_id, revoked: False,
         spent_cents: {$lte: g.budget_cents - spec.amount_cents}},
        {$inc: {spent_cents: spec.amount_cents, uses: 1}}, session=s)
  if u is None: raise Denied("revoked or over budget")
  effects.insert_one(intent_doc(spec, decision, g), session=s)
```
- **Every** effect increments `grant_usage.uses`, including $0 actions. That write is what makes a concurrent revoke conflict.
- **`revoke(grant_id, by)`** (admin client, used by the UI) runs one transaction that sets `revoked:true, revoked_at, revoked_by` on
  `grants` **and** `revoked:true` on `grant_usage`. Both transactions touch the same `grant_usage` document, so one of them
  retries, and no intent can slip in after a revoke.
- **Kill switch:** `start_kill_watcher(grant_id)` starts a daemon thread that watches `grants` with a change stream
  (`$match` on `documentKey._id` plus `operationType: update`, `full_document="updateLookup"`) and sets a global `KILL` event when
  `revoked` becomes true. The agent loop checks `KILL` between orders and inside `run_effect`. Print `[once] KILL: grant revoked by ... at ...`.

### 8.4 `memory.py`: only proven fixes become memories (Track 2)
- `recall(problem_text, system, tenant) -> memory | None`: if flag `memory.on` is false, return None. Otherwise embed with `input_type="query"` and run
  `$vectorSearch` on `memory_vec` with `filter: {"scope.system": system, "scope.tenant": tenant, "status": "active"}`,
  `numCandidates: 50, limit: 3`. Return the top hit if its score is ≥ 0.75 (tune once on the day).
- `save_candidate(field_map, problem_text, scope, key) -> candidate_id`
- `on_proven(receipt)`: if the receipt has `candidate_id`, **promote** it: embed `problem_text` (`input_type="document"`), insert into
  `memories` with `source_receipts: [key]`, `status: "active"`, `expires_at = now + MEMORY_TTL_SECONDS`, and mark the candidate
  promoted. If it has `memory_used`, `$inc successes`.
- `on_failed(receipt)`: for each `memory_used`, `$inc failures`. At `failures ≥ 2`, set `status: "quarantined"` and log it.
- **Scope:** `tenant` comes from env `TENANT`. A memory with tenant `maple` must never come back for tenant `birch` (there's a test for this).

### 8.5 `compensate.py`: rollback as its own exactly-once effect
`reverse(key, by)`: load the proven `bank.refund` receipt, then `run_effect` with key `"comp:" + key`, tool `bank.reverse`,
grant `g_manager_rollback`, `act` = POST `/bank/refunds/{refund_id}/reverse`, and `verify` = GET `/bank/reversals?reference=comp:key`.
When proven, also set the original receipt's `status: compensated` (keep its evidence) and `compensated_by: "comp:" + key`.

### 8.6 `crash.py`
`maybe_crash(point)`: if `random() < float(CRASH_PROB)`, print `[crash] <point>` and call `os._exit(137)`. Seed the RNG from
`time.time_ns() ^ os.getpid()` so each restart crashes somewhere different. In tests, monkeypatch it to raise a `SimulatedCrash` exception.

## 9. The agent: `agent/`

### 9.1 `decide.py`
```python
class RefundDecision(BaseModel):
    refund_amount_cents: int
    restock: bool
    email_subject: str
    email_body: str
```
- `llm` decider: `init_chat_model(MODEL).with_structured_output(RefundDecision)`. The prompt contains the order JSON and the
  customer note; the note is labelled untrusted. Clamp-check in code: if `refund_amount_cents > order.amount_cents`, raise. Never auto-fix money.
- `stub` decider: refund the full amount, restock true, and an email body with a random phrase so it differs every call (this mimics LLM
  non-determinism). Used for large chaos runs because it's fast and free.

### 9.2 `tools.py`
Per world base URL, `httpx` with **timeout 2.0s**. Map responses to exceptions: 422 → `SchemaError(body)`, 5xx or timeout → `Unknown`.
- `bank_refund_act(decision, key, field_map)`: build the v1 body, then apply `field_map` (rename keys) if given.
- `bank_refund_verify(decision, key)`: GET `/bank/refunds?reference=key` → `{refund_id, amount_cents, checked_at}` or None.
- `restock_act`/`restock_verify`, `email_act`/`email_verify`: the same pattern, with `reference=key`.

### 9.3 `adapt.py`: schema drift
`adapt_bank(err, key) -> AdaptResult(field_map, memory_id=None, candidate_id=None)`:
1. `problem_text = f"bank POST /bank/refunds 422 {err.body['error']}"`.
2. `m = memory.recall(problem_text, "bank", TENANT)`. If found, return `field_map = m.fix.field_map, memory_id = m._id` and log
   `[memory] recalled <id> (learned from <source_receipts[0]>)`.
3. Otherwise GET `/bank/schema` and derive the mapping from the v1 required fields to the new required fields. Use the LLM to propose
   the old→new field map (structured output `{"field_map": {old: new}}`), with a deterministic fallback by position if the LLM is off.
   Save it as a candidate and return `field_map` + `candidate_id`. Log `[memory] new candidate (not a memory until proven)`.

### 9.4 Graphs: identical shape, one difference
State: `{orders: list[dict], i: int, done: list[str], held: list[str]}`.
Nodes: `load` (GET `/orders`, first N) → `process` (handles **one** order, `i += 1`) → conditional edge back to `process` until
`i == len(orders)` → END. Checkpointer: `MongoDBSaver` on `LG_DB`, `thread_id = run_id`. Use `durability="sync"` if the installed
LangGraph supports it, so the baseline isn't handicapped. Set `recursion_limit = 2*N + 10`.

- **`graph_baseline.py`** `process`: decide → refund (on `Unknown` retry up to 2 times; on `SchemaError` stop and report) →
  `maybe_crash("after_refund")` → restock (trust 200) → `maybe_crash("after_restock")` → email → `maybe_crash("after_email")` →
  append to `done`. **No receipts, no verification**: this is the standard way people build it.
- **`graph_once.py`** `process`: three `run_effect` calls (refund, restock if decided, email) with keys `run:order:refund|restock|email`.
  The order goes in `done` only if all its receipts are `proven`. Otherwise it goes in `held`. Catch `Stopped`: save state, exit 0, print `stopped by kill switch`.
  - The refund's `decide` calls the decider once and caches the full `RefundDecision` in the refund receipt. The restock and email
    `decide` functions read the **frozen** refund receipt's decision, so the LLM is called once per order, ever.

### 9.5 `run_agent.py`
`python -m agent.run_agent --agent baseline|once --run-id r42 --world-url http://localhost:8001 --orders 20`
- Registers `{_id: agent, pid, run_id}` in `once.processes`.
- First start: `graph.invoke(initial_state, config)`. Restart (a checkpoint exists for `thread_id`): `graph.invoke(None, config)` to resume.
- On normal finish, write `runs` with `claimed_done` = state `done`, then exit 0.
- `once` agent: start the kill watcher before running.

## 10. Scripts

### 10.1 `setup_db.py` (admin)
Idempotent: create the collections with the validator, the indexes, and the vector index (embed one probe string to learn `D`). Seed
`grants`, `grant_usage` and `flags`. Print a checklist of what exists.

### 10.2 `seed_grant.py`
`--reset`: un-revoke, `spent_cents=0`, `expires_at = now + 8h`, for both grants.

### 10.3 `race.py`: the demo engine
`python -m scripts.race --orders 20 --crash-prob 0.15 [--agents baseline,once] [--chaos default|none] [--headless]`
1. POST `/reset` to both worlds. Default chaos: bank `timeout_after_commit` 0.15, inventory `fake_ok` 0.15.
2. `seed_grant --reset`. New `run_id = r<epoch>`.
3. For each agent, run a **supervisor thread**: spawn `run_agent` as a subprocess with `CRASH_PROB` in its env. If the exit code is 137 or
   −9 (killed by the crash button), `crashes += 1` and restart with the same `run_id`. On exit 0, it's finished. Cap at 300 restarts.
   Store live crash counts in `processes`.
4. When both finish, read `/truth/summary` from each world and `runs.claimed_done`, then compute per agent:
   - `dup_refunds`, `dup_restocks`, `dup_emails`: from truth
   - `missed`: orders with no refund that weren't held
   - `false_done`: orders in `claimed_done` missing any effect in truth
   - `first_try_fails`: `once` only, count of receipts with `first_try_failed`
5. Insert into `results` and print a table.

### 10.4 `chaos_run.py`
`python -m scripts.chaos_run --runs 10 --orders 10 --crash-prob 0.2 --decider stub --baseline-url http://localhost:8011 --once-url http://localhost:8012 --grant-id g_chaos`
- It must **not** share worlds or grants with the live demo, so it doesn't reset Abhay's work mid-test. It uses its own two mock
  worlds (`world_chaos_baseline` on 8011, `world_chaos_once` on 8012) and its own grant `g_chaos`, which it creates and resets itself.
  `race.py` therefore also takes `--baseline-url`, `--once-url` and `--grant-id` (defaulting to the demo values).
- It loops `race.py` logic headless, sums the results per agent, and prints:
```
                      standard   exactly-once
crashes injected         93          97
refunds paid twice       11           0
said done, wasn't         6           0
missed refunds            0           0
```
These numbers are an **illustration only**. Present only measured numbers, and say in the pitch that the big run used the stub decider for speed.

## 11. Demo console: `ui/` (Teammate T, after mocks)

`uvicorn ui.server:app --port 8000` (admin client). The browser polls `GET /api/state` every 1s; no SSE needed.

| Method | Path | Does |
|---|---|---|
| GET | `/` | serves `static/index.html` |
| GET | `/api/state` | per agent: crashes, running/finished, truth summary (proxy both worlds), latest results; `once`: receipt counts by status, last 25 receipt transitions (sort `effects` by `sent_at`/`proven_at`), grant status, memory count, `flags.memory` |
| POST | `/api/crash` | SIGKILL both pids from `processes` (same machine) |
| POST | `/api/revoke` | `grants.revoke("g_ops_demo", by="ops-manager@maple.test")` (the transaction from 8.3) |
| POST | `/api/regrant` | `seed_grant --reset` logic |
| POST | `/api/drift` | `{on:bool}` → POST `/chaos` bank `schema_v2` to both worlds |
| POST | `/api/memory` | `{on:bool}` → `flags.memory` |
| GET | `/api/receipt/{key}` | aggregation: `$match _id` → `$lookup grants` on `authority.grant_id` → `$lookup memories` on `memory_used` → `$lookup effects` where `compensates == key` |
| POST | `/api/reverse/{key}` | `compensate.reverse(key, by="ops-manager@maple.test")` |
| POST | `/api/race` | start `scripts.race` in the background with JSON params |

**`index.html`** (projector-friendly: large type, white background, no frameworks):
- **Top bar:** title "Exactly Once" and the motto. Buttons: **Start race · Crash both · Bank changes its API · Memory on/off · Revoke permission · Reset**.
- **Two columns:**
  - Left: "Standard (LangGraph + MongoDB checkpointer)". Right: "With Exactly Once".
  - Each column shows big numbers: **crashes**, **refunds paid twice**, **said done but wasn't**, **orders finished**.
  - Red when a "twice" or "wasn't" number is > 0.
- **Under the right column:** live receipt feed (`ORD-007 refund: proven · tx_8831`). Click a row to open a receipt card:
  - who allowed it (grant + issuer)
  - the frozen decision
  - memory used (with "learned from ORD-012, proven")
  - evidence
  - a **Reverse this refund** button
- **Banners:** "Permission revoked by ops-manager at 14:02:11, agent stopped" and "Memory recalled: fix for bank field rename".

## 12. Tests: `tests/test_once.py`
Use database `once_test` (drop it at the start). Fake `act`/`verify` functions over an in-memory dict. Crashes are simulated by monkeypatching `maybe_crash`.
1. `test_crash_after_act_no_duplicate`: crash at `after_act`, run again → `act` called once, status proven.
2. `test_decision_frozen`: three resumes → `decide` called once.
3. `test_timeout_after_commit`: `act` stores then raises `Unknown` → proven, `act` called once.
4. `test_fake_ok_retries_then_proven`: first `act` stores nothing → retried → proven, `act` called twice.
5. `test_never_confirmed_is_held`: `verify` always None → `held`, never proven.
6. `test_revoked_is_denied`: revoke → `Denied`, receipt `denied`, no intent.
7. `test_budget_is_atomic`: two threads each want 60% of the budget → exactly one is denied.
8. `test_memory_scope`: a memory for `maple` isn't recalled for `birch`.
9. `test_only_proven_becomes_memory`: a candidate stays a candidate when the effect is held, and becomes a memory when proven.

## 13. Milestones and acceptance checks

| # | Owner | Target | Done when |
|---|---|---|---|
| M0 | Abhay | 11:00 | Repo, venv, `.env`, `requirements.txt`, README with the "Built during the event" log; `python -m scripts.setup_db` prints all collections and indexes, and the vector index is READY |
| M1 | T | 11:45 | Mock acceptance check (section 7) passes on both ports |
| M2 | Abhay | 12:45 | `pytest tests/test_once.py -k "not memory"` all green (tests 1–7) |
| M3 | Abhay | 2:00 | `python -m scripts.race --orders 10 --crash-prob 0.2 --headless` prints the table: `once` has dup_refunds 0 and false_done 0; baseline shows ≥ 1 duplicate or false-done (if not, raise crash-prob/chaos and rerun) |
| M4 | Abhay | 2:40 | During a race, `/api/revoke` (or a test script) stops `once` within 2s; query: no `effects` with `created_at > revoked_at`; `reverse` produces a `comp:` receipt and a reversal in the world |
| M5 | Abhay | 3:20 | Drift on: the first run creates 1 candidate → promoted to 1 memory with `source_receipts`. Second run with memory on: `first_try_fails = 0`. Memory off: `first_try_fails > 0`. Tests 8–9 green |
| M6 | T | 2:30–3:30 | Console works: Start race, Crash both, Revoke, Drift, Memory toggle, receipt card, Reverse |
| — | Friend A | from 2:00 | `chaos_run` results saved and screenshotted |
| FREEZE | all | **4:00** | No new code. Rehearse, record the video, final README, push |

**Cut order if behind** (cut from the top first): memory quarantine → TTL demo → compensation/Reverse → console polish (fall back
to the terminal table from `race.py --headless`) → drift and memory → grants. **Never cut M2 or M3.**

## 14. README skeleton (Claude Code writes this in M0)
- What it is (the motto and one paragraph), the architecture diagram as text, how to run (`setup_db`, mocks ×2, `ui`, `race`), results table (filled at 3:45).
- **Built during the event:** `- 10:52 M0 repo + Atlas setup script`, and so on, appended at each milestone.
- **Disclosure:** "The spec (CLAUDE.md) and day plan (PROMPTS.md) were written before the event. All code was written on Nov 14, 2026, during hacking hours."

## 15. Judge-proof facts (keep claims true)
- LangGraph re-runs the interrupted node on resume. This is documented behavior, not a bug. We say "the standard setup needs a side-effect layer," never "LangGraph is broken."
- The mocks honor no idempotency keys **on purpose**: our layer works even when downstream APIs don't support them.
- The agent's database user **can't write `grants`** (custom role). It *can* update `grant_usage` (the counter), and every spend is also on a receipt, so tampering shows up in one query. In production, the spend would move to a separate gate service.
- The big chaos numbers used the stub decider. The live demo uses the real LLM.

## Deviations
(Claude Code: one line per place where the real library or Atlas behavior forced a change to this spec.)
- Exceptions live in `once/errors.py` (not listed in §5) to avoid a grants↔effects import cycle.
- `held` is treated as settled in `run_effect` (§8.2 lists proven/compensated/denied); a held receipt is never re-acted on.
- `EffectSpec` gains `compensates`; authorization spends `decision.amount_cents` when the decision has one (the LLM's real refund), else `spec.amount_cents`.
- `run_effect` calls `adapt(None, key)` once before the first act so a recalled memory shapes the first request (that is what makes `first_try_fails = 0` with memory on); the field map is stored on the receipt so a resume reuses it.
- A memory whose fix gets a 422 counts a failure immediately (`memory_failed` on the receipt) and is quarantined at 2; promotion de-dupes an identical active fix (adds the source receipt instead of a second memory).
- Both agents share `run_id`, so the LangGraph `thread_id` and the `runs` `_id` are `run_id:agent`.
- `processes` `_id` gets a tag prefix for `chaos_run` (`chaos:once`) so the crash button and console never touch the chaos test.
- `race.py` adds `--drift`, `--decider`, `--tag`; the console adds `/api/reset` and `/api/log` for the Reset button.
- langgraph 1.2.22 supports `invoke(..., durability="sync")`; `MongoDBSaver(client, db_name=LG_DB)` per the installed 0.5.1 signature.
- Receipt card uses plain `$lookup` + `$project` (no `$lookup` sub-pipeline) for portability.
- Atlas Model API embedding response is read as `data[].embedding` (verify against the live API in M0).
- A memory's `fix` carries `schema_version` (from `GET /bank/schema` when the candidate was made); `adapt_bank` applies a recalled fix only if the bank currently reports that version, otherwise it attempts normally.
