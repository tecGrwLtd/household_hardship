"""Database access: one SQLAlchemy engine, tables reflected from the live
schema (db/schema.sql stays the single source of truth — no ORM models to
drift out of sync with it), and a per-request connection in a transaction."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import MetaData, Table, create_engine
from sqlalchemy.engine import Connection, Engine

from .config import settings

TABLES = [
    "area_reference", "funding_cycles", "caseworkers", "households", "household_surveys",
    "protected_attributes", "applications", "model_versions", "model_scores",
    "application_reviews", "awards", "fairness_audits",
]


class Database:
    def __init__(self, dsn: str):
        self.engine: Engine = create_engine(dsn, pool_pre_ping=True)
        self.metadata = MetaData()
        self.metadata.reflect(self.engine, only=TABLES)

    def table(self, name: str) -> Table:
        return self.metadata.tables[name]


_db: Database | None = None


def get_database() -> Database:
    global _db
    if _db is None:
        _db = Database(settings.dsn)
    return _db


def get_conn() -> Iterator[Connection]:
    """FastAPI dependency: one transaction per request, committed if the
    handler returns, rolled back if it raises."""
    with get_database().engine.begin() as conn:
        yield conn
