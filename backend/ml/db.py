"""Thin data-access layer: pull the tables the ML pipeline needs out of
Postgres as pandas DataFrames, and write model outputs back.

Kept deliberately separate from features.py/model.py so those stay pure
functions over DataFrames — easy to unit test without a live database.
"""
from __future__ import annotations

import json
import math

import pandas as pd
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from sqlalchemy import create_engine


def get_conn(dsn: str):
    return psycopg2.connect(dsn)


def load_raw_tables(dsn: str) -> dict[str, pd.DataFrame]:
    # pandas only supports SQLAlchemy connectables for read_sql; a raw
    # psycopg2 connection works but warns on every call.
    engine = create_engine(dsn)
    try:
        tables = {
            "households": "SELECT * FROM households",
            "surveys": "SELECT * FROM household_surveys",
            "area": "SELECT * FROM area_reference",
            "applications": "SELECT * FROM applications",
            "protected": "SELECT * FROM protected_attributes",
            "awards": "SELECT * FROM awards",
            "funding_cycles": "SELECT * FROM funding_cycles",
        }
        with engine.connect() as conn:
            return {name: pd.read_sql(sql, conn) for name, sql in tables.items()}
    finally:
        engine.dispose()


def write_model_scores(dsn: str, scores: pd.DataFrame, model_version: str) -> None:
    """scores columns: application_id, cycle_id, need_lo, need_mid, need_hi,
    cutoff, band, top_shap_features (list of (feature, value) tuples)."""
    conn = get_conn(dsn)
    try:
        cur = conn.cursor()
        rows = [
            (
                int(r.application_id), int(r.cycle_id), model_version,
                float(r.need_lo), float(r.need_mid), float(r.need_hi),
                float(r.cutoff) if math.isfinite(r.cutoff) else None, r.band,
                json.dumps(r.top_shap_features) if r.top_shap_features is not None else None,
            )
            for r in scores.itertuples()
        ]
        cur.executemany(
            """INSERT INTO model_scores
               (application_id, cycle_id, model_version, need_lo, need_mid, need_hi, cutoff, band, top_shap_features)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            rows,
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def write_fairness_audits(dsn: str, audits: pd.DataFrame, model_version: str) -> None:
    conn = get_conn(dsn)
    try:
        cur = conn.cursor()
        rows = [
            (None if pd.isna(r.cycle_id) else int(r.cycle_id), model_version, r.attribute, str(r.group),
             int(r.n), float(r.exclusion_error), float(r.gap_vs_best))
            for r in audits.itertuples()
        ]
        cur.executemany(
            """INSERT INTO fairness_audits
               (cycle_id, model_version, attribute, group_value, n, exclusion_error, gap_vs_best)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            rows,
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Model registry (model_versions table)
# ---------------------------------------------------------------------------

def register_model_version(dsn: str, version: str, kind: str, artifact_path: str | None,
                           training_rows: int | None, data_hash: str | None, metrics: dict,
                           notes: str | None = None) -> None:
    """Record a newly trained version as a candidate. It scores nothing
    until activate_model_version() switches it on."""
    with get_conn(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO model_versions
               (model_version, kind, status, artifact_path, trained_at, training_rows, data_hash, metrics, notes)
               VALUES (%s, %s, 'candidate', %s, now(), %s, %s, %s, %s)""",
            (version, kind, artifact_path, training_rows, data_hash, Json(metrics), notes),
        )
    conn.close()


def activate_model_version(dsn: str, version: str) -> str | None:
    """Make `version` the one live model; the previously active one is
    retired. Returns the previous version. One transaction, so there is never
    a moment with zero or two active models."""
    with get_conn(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM model_versions WHERE model_version = %s", (version,))
        if cur.fetchone() is None:
            raise LookupError(f"no model version {version!r}")
        cur.execute("""UPDATE model_versions SET status = 'retired'
                       WHERE status = 'active' AND model_version <> %s RETURNING model_version""", (version,))
        prev = cur.fetchone()
        cur.execute("""UPDATE model_versions SET status = 'active', activated_at = now()
                       WHERE model_version = %s""", (version,))
    conn.close()
    return prev[0] if prev else None


def get_model_version(dsn: str, version: str | None = None) -> dict:
    """One registry row — the active version when `version` is None."""
    with get_conn(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        if version is None:
            cur.execute("SELECT * FROM model_versions WHERE status = 'active'")
        else:
            cur.execute("SELECT * FROM model_versions WHERE model_version = %s", (version,))
        row = cur.fetchone()
    conn.close()
    if row is None:
        raise LookupError(f"no model version {version!r}" if version else "no active model version")
    return dict(row)


def list_model_versions(dsn: str) -> pd.DataFrame:
    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            return pd.read_sql(
                """SELECT model_version, kind, status, trained_at, activated_at, training_rows,
                          (metrics->'gates'->>'passed')::boolean AS gates_passed
                   FROM model_versions ORDER BY created_at""", conn)
    finally:
        engine.dispose()
