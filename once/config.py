"""Env loading and Mongo clients.

db() returns the `once` database through the agent user when ONCE_CLIENT=agent
(set by agent/run_agent.py), and through the admin user otherwise.
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.database import Database

load_dotenv()


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def need(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"missing env var {name} (see .env.example)")
    return v


@lru_cache(maxsize=None)
def _client(uri_var: str) -> MongoClient:
    return MongoClient(need(uri_var), tz_aware=True, serverSelectionTimeoutMS=10000)


def admin_client() -> MongoClient:
    return _client("MONGODB_URI_ADMIN")


def agent_client() -> MongoClient:
    return _client("MONGODB_URI_AGENT")


def is_agent() -> bool:
    return os.environ.get("ONCE_CLIENT") == "agent"


def client() -> MongoClient:
    return agent_client() if is_agent() else admin_client()


def once_db_name() -> str:
    return os.environ.get("ONCE_DB", "once")


def lg_db_name() -> str:
    return os.environ.get("LG_DB", "lg")


def db() -> Database:
    return client()[once_db_name()]


def admin_db() -> Database:
    return admin_client()[once_db_name()]


def tenant() -> str:
    return os.environ.get("TENANT", "maple")
