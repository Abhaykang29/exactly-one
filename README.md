# Exactly Once

**Nothing counts without a receipt.**

AI agents that take real actions (refunds, restocks, emails) crash mid-job. The standard setup, LangGraph with
MongoDB's checkpointer, resumes after a crash, but LangGraph re-runs the interrupted node, API calls included
(that is documented behavior), so a customer can get refunded twice. Agents also report "done" when an action
never landed. **Exactly Once** is a layer that gives every action a **receipt** in MongoDB Atlas:

- **Permission to start** (Track 1): a grant is checked and spent in the same transaction that writes the intent.
  The agent's database user can only *read* its grants. Revoking fires a change-stream kill switch.
- **Proof to finish** (Track 3): an action counts as done only when the real system confirms it.
- **Proof to remember** (Track 2): a fix becomes a memory only after a proven receipt confirmed it.

The demo races the standard agent against the same agent with this layer, crashes both, and counts double refunds.

## Architecture

```
                 ┌──────────────── Atlas Cluster0 ────────────────┐
  agent (once)   │ once.effects     one receipt per action (_id = run:order:action)
  ─ run_effect ─►│ once.grants      rules (agent_user: read only) + change stream kill switch
     │           │ once.grant_usage spend counter (same txn as the intent)
     │           │ once.candidates → once.memories  ($vectorSearch memory_vec, Voyage embeddings)
     │           │ lg.*             LangGraph MongoDBSaver checkpoints
     ▼           └────────────────────────────────────────────────┘
  fake world (FastAPI, no idempotency keys)  bank · inventory · email · chaos · /truth/summary
     ▲
  agent (baseline): same graph, naive tool calls
```

`run_effect` rules: (1) write the intent first, (2) `_id` refuses doubles, (3) decide once then freeze,
(4) done means verified, (5) after a crash, verify before acting again.

## How to run

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in Atlas URIs, MODEL + key, VOYAGE_API_KEY
python -m scripts.setup_db    # collections, validator, indexes, memory_vec (waits until READY), grants
```

Two mock worlds (one per agent), the console, then a race:

```bash
WORLD_DB=world_baseline uvicorn mocks.app:app --port 8001
WORLD_DB=world_once     uvicorn mocks.app:app --port 8002
uvicorn ui.server:app --port 8000          # http://localhost:8000
python -m scripts.race --orders 10 --crash-prob 0.2 --headless
```

Other scripts: `python -m scripts.demo_revoke` (M4), `python -m scripts.demo_drift` (M5),
`python -m scripts.chaos_run ...` (big run on separate worlds 8011/8012 and grant `g_chaos`),
`pytest tests/test_once.py`.

## Results

_Filled in from the `results` collection after the measured runs._

|                      | standard | exactly-once |
|----------------------|---------:|-------------:|
| crashes injected     |          |              |
| refunds paid twice   |          |              |
| said done, wasn't    |          |              |
| missed refunds       |          |              |

The big chaos run uses the stub decider for speed; the live demo uses the real model.

## Built during the event

- 10:56 M0–M6 code written: once/ layer, agents, mock world, race/chaos/demo scripts, tests, console. Smoke-tested in-process; Atlas run pending `.env`
- 12:02 M0+M2 verified on Atlas: setup_db checklist green, memory_vec READY (1024 dims), pytest 11 passed

## Disclosure

The spec (CLAUDE.md) and day plan (PROMPTS.md) were written before the event. All code was written on Nov 14, 2026,
during hacking hours.
