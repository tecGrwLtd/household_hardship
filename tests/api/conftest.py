"""API tests run against a real Postgres: a throwaway `hardship_test`
database on the same server as the dev database, rebuilt from schema.sql,
views.sql and the seed CSVs once per session. Skipped when no server is
reachable, so `pytest` still passes on a machine without Docker running.

Override the server with HARDSHIP_TEST_ADMIN_DSN (a DSN for any existing
database on it, used only to create/drop hardship_test)."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import psycopg2
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
ADMIN_DSN = os.environ.get("HARDSHIP_TEST_ADMIN_DSN",
                           "postgresql://hardship_app:hardship_dev_only@localhost:5432/postgres")
TEST_DB = "hardship_test"
TEST_DSN = ADMIN_DSN.rsplit("/", 1)[0] + f"/{TEST_DB}"


def _load_module():
    spec = importlib.util.spec_from_file_location("load_data", ROOT / "db" / "load_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def test_dsn():
    try:
        admin = psycopg2.connect(ADMIN_DSN, connect_timeout=3)
    except psycopg2.OperationalError:
        pytest.skip("No Postgres reachable for API tests (start it with `docker compose up -d db`)")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        cur.execute(f"CREATE DATABASE {TEST_DB}")
    _load_module().load(TEST_DSN, log=lambda *_: None)
    yield TEST_DSN
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    admin.close()


@pytest.fixture(scope="session")
def client(test_dsn):
    from backend.api import database
    from backend.api.main import app

    database._db = database.Database(test_dsn)
    with TestClient(app) as c:
        yield c
    database._db.engine.dispose()
    database._db = None


@pytest.fixture(scope="session")
def auth(client):
    from backend.api.config import settings
    r = client.post("/auth/login", data={"username": settings.admin_user, "password": settings.admin_password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def sql(test_dsn):
    """Run a statement directly against the test database (for setup the
    API deliberately has no endpoint for)."""
    conn = psycopg2.connect(test_dsn)
    conn.autocommit = True

    def run(statement, params=None):
        with conn.cursor() as cur:
            cur.execute(statement, params)
            return cur.fetchall() if cur.description else None
    yield run
    conn.close()
