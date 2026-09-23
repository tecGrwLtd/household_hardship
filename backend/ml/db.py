"""Thin data-access layer: pull the tables the ML pipeline needs out of
Postgres as pandas DataFrames, and write model outputs back.

Kept deliberately separate from features.py/model.py so those stay pure
functions over DataFrames — easy to unit test without a live database.
"""
from __future__ import annotations

import json

import pandas as pd
import psycopg2


def get_conn(dsn: str):
    return psycopg2.connect(dsn)


def load_raw_tables(dsn: str) -> dict[str, pd.DataFrame]:
    conn = get_conn(dsn)
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
        return {name: pd.read_sql(sql, conn) for name, sql in tables.items()}
    finally:
        conn.close()


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
                float(r.cutoff), r.band,
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


def write_fairness_audits(dsn: str, audits: pd.DataFrame) -> None:
    conn = get_conn(dsn)
    try:
        cur = conn.cursor()
        rows = [
            (int(r.cycle_id), r.attribute, str(r.group), int(r.n), float(r.exclusion_error), float(r.gap_vs_best))
            for r in audits.itertuples()
        ]
        cur.executemany(
            """INSERT INTO fairness_audits (cycle_id, attribute, group_value, n, exclusion_error, gap_vs_best)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            rows,
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
