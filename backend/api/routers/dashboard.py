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
        text("SELECT model_version FROM model_versions WHERE status = 'active' AND purpose = 'need'")).scalar()
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
                                  FROM model_versions WHERE status = 'active' AND purpose = 'need'""")).mappings().first()
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


@router.get("/repeat-forecast")
def repeat_forecast(start: date | None = None, end: date | None = None, conn: Connection = Depends(get_conn)):
    """Per month and support group: how many applied, how many are expected
    to apply again within a year (sum of forecast probabilities), and — for
    months at least a year old — how many actually did. Planning only; the
    forecast never touches allocation."""
    return conn.execute(text(f"SELECT * FROM v_repeat_forecast WHERE {DATE_RANGE} ORDER BY month, support_group"),
                        {"start": start, "end": end}).mappings().all()


@router.get("/drift")
def drift(conn: Connection = Depends(get_conn)):
    """Latest drift report for the active need model (feature and score PSI,
    SHAP stability, mean need by group, coverage on new outcomes), plus the
    status of recent reports. Reports are produced monthly by
    `python -m backend.ml drift`."""
    version = conn.execute(text(
        "SELECT model_version FROM model_versions WHERE status = 'active' AND purpose = 'need'")).scalar()
    latest = conn.execute(text("""SELECT * FROM drift_reports WHERE model_version = :v
                                  ORDER BY created_at DESC LIMIT 1"""), {"v": version}).mappings().first()
    history = conn.execute(text("""SELECT report_id, window_start, window_end, applications, status, created_at
                                   FROM drift_reports WHERE model_version = :v
                                   ORDER BY created_at DESC LIMIT 12"""), {"v": version}).mappings().all()
    return {"model_version": version, "latest": latest, "history": history,
            "note": None if latest else "No drift report yet for this model: run `python -m backend.ml drift`."}


@router.get("/override-trend")
def override_trend(conn: Connection = Depends(get_conn)):
    """Per month: reviews, how often reviewers reversed the model's lean,
    and how often they approved. An override rate near 1% reads as a rubber
    stamp (design spec); below 5% is flagged."""
    rows = conn.execute(text("""
        SELECT date_trunc('month', reviewed_at)::date AS month, count(*) AS reviews,
               avg(overridden::int) AS override_rate,
               avg((final_decision = 'approved')::int) AS approval_rate
        FROM application_reviews GROUP BY 1 ORDER BY 1""")).mappings().all()
    return [{**r, "override_rate_ok": float(r["override_rate"]) >= 0.05} for r in rows]
