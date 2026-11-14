"""The fake world: one FastAPI app, run twice with different WORLD_DB.

  WORLD_DB=world_baseline uvicorn mocks.app:app --port 8001
  WORLD_DB=world_once     uvicorn mocks.app:app --port 8002
"""
from fastapi import FastAPI

from mocks import actions, bank, chaos, email, inventory, orders, truth

app = FastAPI(title="Exactly Once mock world")
for m in (orders, bank, inventory, email, chaos, truth):
    app.include_router(m.router)


@app.get("/health")
def health():
    return {"ok": True, "db": chaos.wdb().name}


app.include_router(actions.router)   # generic /{system}/actions last, so it never shadows the core routes
