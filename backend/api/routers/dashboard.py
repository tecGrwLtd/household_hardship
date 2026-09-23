"""Read-only endpoints for the dashboard — thin wrappers over the SQL views
in db/views.sql, plus fairness and model-health summaries. Protected
attributes appear only as aggregated fairness rows, never per household."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..database import get_conn

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

DATE_RANGE = "(CAST(:start AS date) IS NULL OR month >= :start) AND (CAST(:end AS date) IS NULL OR month <= :end)"


@router.get("/monthly-support")
def monthly_support(start: date | None = None, end: date | None = None, conn: Connection = Depends(get_conn)):
    """The kickoff-call dashboard: per month and support group (education /
    health / financial / bereavement) — applicants, repeat beneficiaries
    helped within or over a year ago, and awards."""
    return conn.execute(text(f"SELECT * FROM v_monthly_support WHERE {DATE_RANGE} ORDER BY month, support_group"),
                        {"start": start, "end": end}).mappings().all()


@router.get("/monthly-applications")
def monthly_applications(start: date | None = None, end: date | None = None, conn: Connection = Depends(get_conn)):
    """Per month and need category, with the current decision-band mix."""
    return conn.execute(text(f"SELECT * FROM v_monthly_applications WHERE {DATE_RANGE} ORDER BY month, need_category"),
                        {"start": start, "end": end}).mappings().all()


@router.get("/repeat-support")
def repeat_support(start: date | None = None, end: date | None = None, conn: Connection = Depends(get_conn)):
    """Totals for the repeat-support cards: first-time vs repeat applicants,
    and never helped vs helped within / over a year ago."""
    return conn.execute(text("""
        SELECT repeat_bucket, helped_bucket, count(*) AS applications
        FROM v_repeat_support
        WHERE (CAST(:start AS date) IS NULL OR submitted_at >= :start)
          AND (CAST(:end AS date) IS NULL OR submitted_at < CAST(:end AS date) + 1)
        GROUP BY 1, 2 ORDER BY 1, 2"""), {"start": start, "end": end}).mappings().all()


@router.get("/cycles")
def cycles(conn: Connection = Depends(get_conn)):
    return conn.execute(text("SELECT * FROM v_cycle_summary ORDER BY period_start")).mappings().all()


@router.get("/fairness")
def fairness(model_version: str | None = None, conn: Connection = Depends(get_conn)):
    """Exclusion error among the worst-off, by group, for a model version
    (default: the active one) — pooled across cycles, from its out-of-fold
    evaluation. Groups under 20 people are suppressed at source."""
    version = model_version or conn.execute(
        text("SELECT model_version FROM model_versions WHERE status = 'active'")).scalar()
    rows = conn.execute(text("""
        SELECT attribute, group_value, n, exclusion_error, gap_vs_best
        FROM fairness_audits WHERE model_version = :v AND cycle_id IS NULL
        ORDER BY attribute, gap_vs_best DESC"""), {"v": version}).mappings().all()
    return {"model_version": version, "rows": rows,
            "note": None if rows else "No audit for this version (the rule-based placeholder has none; "
                                      "trained versions get one from `python -m backend.ml train`)."}


@router.get("/model-health")
def model_health(conn: Connection = Depends(get_conn)):
    """Active model, its evaluation headline and gates, the band mix of the
    applications it has scored, and the reviewers' override rate — which the
    spec says must stay above ~5% for human review to count as real."""
    active = conn.execute(text("""SELECT model_version, kind, activated_at, trained_at, training_rows,
                                         metrics->'gates' AS gates,
                                         metrics->'models'->'lgbm' AS evaluation
                                  FROM model_versions WHERE status = 'active'""")).mappings().first()
    bands = conn.execute(text("""
        SELECT band, count(*) AS n FROM (
            SELECT DISTINCT ON (application_id) band FROM model_scores
            WHERE model_version = :v ORDER BY application_id, scored_at DESC) latest
        GROUP BY band"""), {"v": active["model_version"] if active else None}).mappings().all()
    reviews = conn.execute(text("""
        SELECT count(*) AS reviews, avg(overridden::int) AS override_rate,
               count(*) FILTER (WHERE final_decision = 'approved') AS approved
        FROM application_reviews""")).mappings().one()
    rate = reviews["override_rate"]
    return {
        "active_model": active,
        "bands": {b["band"]: b["n"] for b in bands},
        "reviews": {**reviews, "override_rate_ok": None if rate is None else float(rate) >= 0.05},
    }
