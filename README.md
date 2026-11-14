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

**Chaos run** (`scripts.chaos_run`, 10 races x 10 orders = 100 orders per agent, crash probability 0.2 at every
crash point, default chaos: bank `timeout_after_commit` 15%, inventory `fake_ok` 15%, **stub decider**), measured
on the event's Atlas cluster against separate worlds and grant `g_chaos`:

|                        | standard | exactly-once |
|------------------------|---------:|-------------:|
| crashes injected       |       88 |          144 |
| refunds paid twice     |      116 |            0 |
| restocks done twice    |       42 |            0 |
| emails sent twice      |       31 |            0 |
| said done, wasn't      |        7 |            0 |
| missed refunds         |        0 |            0 |
| orders finished        |      100 |          100 |

Exactly Once has more crash points per order (two per action, three actions), so it was crashed more often.
The big run used the stub decider for speed. The live demo uses the real model.

**Single races, 10 orders, crash probability 0.2** (ground truth from the fake bank):

| decider | agent | refunds paid twice | $ paid twice | orders refunded | missed | held | said done, wasn't |
|---|---|---:|---:|---:|---:|---:|---:|
| stub | standard | 7 | $201.93 | 10/10 | 0 | 0 | 0 |
| stub | exactly-once | 0 | $0.00 | 10/10 | 0 | 0 | 0 |
| LLM (muse-spark-1.3) | standard | 15 | $958.85 | 10/10 | 0 | 0 | 0 |
| LLM (muse-spark-1.3) | exactly-once | 0 | $0.00 | 10/10 | 0 | 0 | 0 |

LLM race timing (`muse-wonder-1.0`, about 8s per decision): 10 orders 278s, 6 orders 102s.

## Built during the event

- 10:56 M0–M6 code written: once/ layer, agents, mock world, race/chaos/demo scripts, tests, console. Smoke-tested in-process; Atlas run pending `.env`
- 12:02 M0+M2 verified on Atlas: setup_db checklist green, memory_vec READY (1024 dims), pytest 11 passed
- 12:19 Agent user proven read-only on grants (test). Memory fixes gated on bank schema_version. Grant budget >= 2x race order value.
- 12:19 M3 on Atlas, stub decider, 10 orders, crash 0.2: standard 14 refunds paid twice, exactly-once 0
- 12:19 Real LLM on (Meta muse-wonder-1.0, OpenAI-compatible, structured output): race 10 orders / crash 0.2 took 278s, standard 21 paid twice vs exactly-once 0; muse-spark-1.3 took 275s (15 vs 0). Traces in LangSmith project exactly-once
- 12:48 Reliability: LLM decider has a 30s timeout + 1 retry, then a rule-based fallback; an LLM refund above the order amount becomes a held receipt. "$ paid twice" on the race table and console.
- 12:48 Console demo flow (reset -> race, 20 orders, real LLM, Crash both x2) run 3 times back to back: no failures; exactly-once 0 double refunds and 20/20 finished every time (standard: $53.97, $121.97, $34.98 paid twice). In run 3 the bank API change was switched on mid-race: exactly-once learned the fix once, proved it, and reused the memory for 17 orders
- 14:26 Phase 1: third agent 'textbook' (best-practice LangGraph, one node per side effect). 6 orders, real LLM, crash 0.2: standard 8 refunds paid twice ($238.92), textbook 0 refunds twice / 1 email twice (2 crashes), exactly-once 0 of anything

## Disclosure

The spec (CLAUDE.md) and day plan (PROMPTS.md) were written before the event. All code was written on Nov 14, 2026,
during hacking hours.
