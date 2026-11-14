# Exactly Once: Day Plan and Prompts

How to use this file. Tonight, do section A. On Saturday:

1. At 10:45, create the repo and drop in `CLAUDE.md` and this file.
2. At each checkpoint, paste the matching prompt into Claude Code.
3. Teammates use their own sections.

---

## A. Tonight (no code, about 1 hour)

### A1. Accounts and keys (Abhay)
- [ ] **LLM key with credit.** MongoDB doesn't give you a model. Pick a fast, cheap one and note its `provider:model` name.
- [ ] **Voyage key.** In Atlas, go to Services → AI Model APIs → create a model API key.
- [ ] **LangSmith key**, or plan to get it from the sponsor at the event.
- [ ] Python 3.11 installed. Check that `pip install` works on your laptop.
- [ ] Ask in Discord #questions: *"Is it OK to prepare a design doc before the event if all code is written during hacking hours?"* Keep the answer.

### A2. Atlas setup in the UI (Abhay, 15 min, configuration only)
1. **Security → Database & Network Access → Database Users:**
   - Your auto-made user is the **admin_user**. **Change its password**, because the old one was visible in a screenshot. Save the new one in a password manager.
2. **Custom Roles → Add a custom role** named `agent_role`:
   - Built-in role `readWrite` on database `lg`.
   - Database `once`, collection `effects`: find, insert, update, changeStream.
   - `once.grants`: find, changeStream. **Nothing else.** This is the "agent can't edit its own rules" proof.
   - `once.grant_usage`: find, update.
   - `once.candidates`, `once.memories`: find, insert, update.
   - `once.flags`: find.
   - `once.processes`, `once.runs`: find, insert, update.
3. **Add a database user** `agent_user` with role `agent_role`, and save its password.
4. **Network Access:** you'll add the venue IP at 10:45. If the venue IP keeps changing, allow `0.0.0.0/0` **for Saturday only** and remove it afterward.
5. Copy the cluster's connection host (`cluster0.xxxx.mongodb.net`) for `.env`.

### A3. Team (send tonight)
Send everyone `CLAUDE.md`, this file and the one-page doc. Roles:

| Person | Job | Needs on Saturday |
|---|---|---|
| Abhay | `once/`, `agent/`, `scripts/` | Laptop, Claude Code, all keys |
| Teammate T | `mocks/`, then `ui/` | Laptop, Claude Code, Python 3.11, git access to the repo |
| Friend A | Runs the chaos test, presses the crash button, attacks the demo | Laptop with Python, or uses Abhay's after 2:00 |
| Friend B | Pitch script, 1-min video, rehearsal timer, README log | Phone for video, timer |

### A4. Sleep. Rehearsal later depends on it.

---

## B. Saturday timeline

| Time | Abhay | Teammate T | Friend A | Friend B |
|---|---|---|---|---|
| 9:00–10:45 | Kickoff, venue IP into Atlas, create the repo | Clone the repo, set up Python | Read the one-pager | Read the one-pager, draft the script |
| 10:45 | **Prompt 1 (M0)** | **Prompt T1 (M1 mocks)** | Learn the curl checks in CLAUDE.md §7 | Script v1 |
| 11:45 | Prompt 2 (M2 layer) | Prompt T2 (M6 console) | Test T's mocks with curl | Video shot list |
| 1:00 | Lunch while tests run | Lunch | Lunch | Lunch |
| ~1:00 | Prompt 3 (M3 race) | Console continued | — | — |
| 2:00 | Prompt 4 (M4 permission) | Console wiring | **Chaos runs start** (section E) | Script v2, using the real numbers |
| 2:40 | Prompt 5 (M5 memory) | Console polish | Screenshot results | — |
| 3:20 | Prompt 6 (integration + README) | Help test | Crash-button practice | — |
| 3:45 | Record a full backup demo video | — | — | Record the 1-min video |
| **4:00** | **CODE FREEZE** | | | Rehearsal ×5 with timer |
| 4:45 | Submit (public repo, video, all four teammates added) | | | |

---

## C. Abhay's prompts (paste in order)

### Prompt 1: 10:45 (M0, bootstrap)
```
Read CLAUDE.md completely before doing anything. Then do Milestone M0 only:
1. Create the repo layout from section 5 (empty modules with docstrings are fine), requirements.txt,
   .env.example, .gitignore, and README.md from section 14 (include the disclosure line and the
   "Built during the event" log).
2. Create a venv, install requirements, and run pip freeze > requirements.lock.
3. Write once/config.py (env loading, admin + agent Mongo clients) and scripts/setup_db.py exactly as
   section 6 and 10.1 describe: collections, $jsonSchema validator on effects, indexes, the memory_vec
   vector index (embed one probe string via the Atlas Model API to learn the dimension, then poll until READY),
   and seed grants, grant_usage and flags.
4. Run it and show me its checklist output.
Verify every library call against the installed version instead of guessing. When M0's acceptance check in
section 13 passes, commit "M0: bootstrap + Atlas setup", push, append to the README log, and stop.
```

### Prompt 2: when M0 passes (M2, the layer)
```
Do Milestone M2: build once/ (crash.py, grants.py, effects.py, __init__.py) exactly as section 8 describes:
the five rules in run_effect, the atomic authorize_and_insert_intent transaction, revoke(), the kill watcher
with a change stream, and conditional status updates. Then write tests 1–7 from section 12 in
tests/test_once.py against database once_test, with maybe_crash monkeypatched to raise SimulatedCrash.
Run pytest and fix until tests 1–7 pass. Show me the pytest output. Commit "M2: exactly-once layer + tests",
update the README log, and stop.
```

### Prompt 3: when M2 passes (M3, agents + race)
```
Do Milestone M3. Build agent/decide.py (llm + stub), agent/tools.py, graph_baseline.py, graph_once.py,
run_agent.py, scripts/race.py and scripts/chaos_run.py as sections 9, 10.3 and 10.4 describe (race.py takes
--baseline-url, --once-url and --grant-id so the chaos test can use separate worlds). The two graphs must have the identical
shape; only the tool calls differ. Use MongoDBSaver on LG_DB, check its real constructor first, and use
durability="sync" if the installed LangGraph supports it. The mock worlds are already running on ports
8001 and 8002 (Teammate T built them; don't edit mocks/).
Run: python -m scripts.race --orders 10 --crash-prob 0.2 --headless
Acceptance: once has dup_refunds 0 and false_done 0, and baseline shows at least one duplicate or false-done.
If baseline shows none, raise crash-prob or chaos rates and rerun. Show me the table, commit "M3: agents + race",
update the README log, and stop.
```

### Prompt 4: when M3 passes (M4, permission and rollback)
```
Do Milestone M4. Make sure revoke() and the kill watcher from section 8.3 work end to end, and add
once/compensate.py (section 8.5) with the g_manager_rollback grant. Write a small script
scripts/demo_revoke.py that starts a race, revokes g_ops_demo after ~5 seconds, then prints:
(a) seconds from revoke to the once agent stopping, (b) the count of effects created after revoked_at
(must be 0), (c) the result of reversing one proven refund (a comp: receipt plus a reversal in the world).
Show me the output. Commit "M4: atomic permission, kill switch, compensation", update the README log, stop.
```

### Prompt 5: when M4 passes (M5, schema drift and memory)
```
Do Milestone M5: agent/adapt.py and once/memory.py from sections 8.4 and 9.3, wired into graph_once's refund
effect through EffectSpec.adapt. Only proven receipts promote a candidate into a memory; memory recall uses
$vectorSearch on memory_vec with the scope filter; flags.memory toggles recall; failures >= 2 quarantine.
Write scripts/demo_drift.py that: (1) resets the worlds and turns schema_v2 on, runs 5 orders with memory on and
an empty memories collection, and prints candidates/memories created plus first_try_fails; (2) runs 5 more
orders in a new run with memory on and prints first_try_fails (expect 0) and the recalled memory id + source
receipt; (3) runs 5 more with memory off and prints first_try_fails (expect > 0).
Add tests 8–9 from section 12. Show me the output and the pytest summary. Commit "M5: proven-only memory",
update the README log, and stop.
```

### Prompt 6: 3:20 (integration and polish)
```
Integration pass, with no new features:
1. Run the full demo flow through the UI console's API (start race, crash, drift, memory toggle, revoke,
   receipt, reverse) and fix any bug you find.
2. Make all console log lines short and readable from a projector.
3. Fill the README results table with the latest numbers from the results collection, and add a run guide:
   setup_db, two mock worlds, ui, race.
4. Confirm .env isn't tracked: git ls-files | grep -c "\.env$" must print 0.
Commit "M6: integration + README", push, and stop.
```

### Emergency prompts
**Behind at 1:00** (M2 not green):
```
Stop all other work. Get tests 1, 2, 3 and 6 passing with the simplest possible code. Skip the kill watcher and
compensation for now. Show me pytest output.
```
**Behind at 2:45:**
```
Cut M5 (memory). Make sure race + revoke + reverse work reliably in the console. Then update README and commit.
```
**Something breaks after freeze:** don't touch the code. Use the backup video recorded at 3:45 and narrate over it.

---

## D. Teammate T's prompts

### Prompt T1: 10:45 (M1, mock world)
```
Read CLAUDE.md sections 0–4 and 7. Your job is ONLY the mocks/ folder: don't edit any other folder.
Build the FastAPI mock world exactly as section 7 specifies: orders, bank (with schema v1/v2), inventory,
email, chaos switches with rates, and /truth/summary, with all state in Mongo database $WORLD_DB using
MONGODB_URI_ADMIN from .env. No idempotency keys: store the client's reference field on every write.
Run two copies:
  WORLD_DB=world_baseline uvicorn mocks.app:app --port 8001
  WORLD_DB=world_once     uvicorn mocks.app:app --port 8002
Then run the mock acceptance check from section 7 against both ports, including each chaos mode at rate 1.
Show me the results. Commit "M1: mock world" (git pull --rebase first), push, and stop.
```

### Prompt T2: when M1 passes (M6, demo console)
```
Read CLAUDE.md sections 6, 8.3, 8.5 and 11. Build ui/server.py and ui/static/index.html exactly as section
11 describes. Vanilla HTML/CSS/JS only: no frameworks, no Streamlit, no build step. Large fonts for a
projector, white background. Use MONGODB_URI_ADMIN.
Abhay is building once/ in parallel. For /api/revoke, /api/reverse and /api/race, import from once/ and
scripts/ once they exist; until then, return {"todo": true} so the page still works.
Run: uvicorn ui.server:app --port 8000 and check that every button calls its endpoint and the page polls
/api/state every second without errors in the browser console.
Commit "M6: demo console" (git pull --rebase first), push, and stop.
```

### Git rule for T and Abhay
Only touch your own folders. Before every commit: `git pull --rebase`. If there's a conflict outside your folder, ask Abhay.

---

## E. Friend A: the chaos test (from about 2:00)

Run these in a terminal in the repo folder, with the venv activated (`source .venv/bin/activate`). They use
**separate** worlds and a separate grant, so they never disturb the live demo.

Terminal 1 and terminal 2 (leave both running):
```
WORLD_DB=world_chaos_baseline uvicorn mocks.app:app --port 8011
WORLD_DB=world_chaos_once     uvicorn mocks.app:app --port 8012
```
Terminal 3:
```
python -m scripts.chaos_run --runs 10 --orders 10 --crash-prob 0.2 --decider stub \
  --baseline-url http://localhost:8011 --once-url http://localhost:8012 --grant-id g_chaos
```
- It takes several minutes. When it finishes, **screenshot the table** and send it to Abhay and Friend B.
- If it errors, copy the last 20 lines to Abhay. Don't try to fix the code.
- Write down **exactly** these numbers for the pitch: crashes injected, refunds paid twice (both columns), said done but wasn't (both columns).

**Crash-button practice (3:20):** in the console, press **Start race**, wait 5 seconds, then press **Crash both** 3–4 times about 4 seconds apart. During the live demo, hand the button to a judge.

---

## F. Friend B: pitch and video

**The 3-minute script** comes from the one-pager. Rules:
- Use only numbers Friend A measured. Never round a 0 into "almost none."
- Say once: *"The big test used a fast stand-in for the AI; the live demo uses the real model."*
- The close: *"Nothing counts without a receipt. Permission to start, proof to finish, proof to remember."*

**The 1-minute video (record at 3:45):**

| Seconds | Shot |
|---|---|
| 0–10 | Title plus the hook line |
| 10–30 | Race starts, crash pressed, left counter climbs, right stays 0 |
| 30–40 | Revoke: agent stops |
| 40–50 | Receipt card: who allowed it, memory, proof |
| 50–60 | Results table plus "Built today on MongoDB Atlas" |

**Rehearsal (4:00–4:45):** five full runs with a timer. Stop at 3:00 exactly. After each run, cut one sentence.

---

## G. Questions to prepare (Abhay answers, everyone knows them)

| Question | Answer |
|---|---|
| Why not Stripe-style idempotency keys? | Only works if every API supports them and the key stays stable across AI retries. We freeze the decision and verify against the system, so it works even without key support. |
| Is the comparison fair to LangGraph? | Same graph, same checkpointer, same durability mode. LangGraph documents that it re-runs the interrupted node; side effects need their own layer. |
| Isn't this just a unique index? | That's rule 2 of 5. The hard part is the restart: deciding per step whether the world already changed. |
| What if verification itself fails? | The step stays "sent," never "proven," and is held for a human. We never retry blindly. |
| Can the agent change its own permissions? | No. Its database user can only read grants. It can update the spend counter, but every spend is also a receipt, so tampering shows up in one query. |
| What about a revoke that lands mid-action? | Revoke and spend write the same document in transactions; one retries, so nothing slips through. |
| Why not Atlas Agent Engine? | It's in public preview. We built on primitives that are production-ready today; this could sit under its governance layer. |
| What's next? | Fencing tokens for many workers, an open-source library, and running it under a freight-dispatch agent. |
