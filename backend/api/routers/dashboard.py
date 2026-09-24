"""Dashboard data. Everything under "filtered" takes the sidebar's global
filters (see filters.py) and reads v_application_facts. Protected
attributes appear only as aggregated fairness rows, never per household.
Programme-level monitoring (fairness, model health, drift, override trend)
is for admins."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..auth import require_admin
from ..database import get_conn
from ..filters import Filters, filters

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
admin_only = [Depends(require_admin)]

DATE_RANGE = "(CAST(:start AS date) IS NULL OR month >= :start) AND (CAST(:end AS date) IS NULL OR month <= :end)"


# ---------------------------------------------------------------------------
# Filter options (for the sidebar selectors)
# ---------------------------------------------------------------------------

@router.get("/filters")
def filter_options(conn: Connection = Depends(get_conn)):
    """Everything the sidebar selectors offer. Months newest first."""
    months = conn.execute(text("""SELECT DISTINCT date_trunc('month', submitted_at)::date AS month
                                  FROM applications ORDER BY 1 DESC""")).scalars().all()
    districts = conn.execute(text("""SELECT area_code, area_name, region, urban_rural::text AS urban_rural
                                     FROM area_reference ORDER BY area_name""")).mappings().all()
    groups = conn.execute(text("""SELECT DISTINCT support_group(c) AS g
                                  FROM unnest(enum_range(NULL::need_category_enum)) c ORDER BY 1""")).scalars().all()
    return {
        "months": months,
        "support_groups": groups,
        "regions": sorted({d["region"] for d in districts if d["region"]}),
        "districts": districts,
        "urban_rural": ["urban", "rural"],
    }


# ---------------------------------------------------------------------------
# Filtered figures
# ---------------------------------------------------------------------------

def _summary(conn: Connection, f: Filters) -> dict:
    where, params = f.where()
    return dict(conn.execute(text(f"""
        SELECT count(*) AS applicants,
               coalesce(sum(amount_requested), 0) AS requested,
               count(*) FILTER (WHERE awarded) AS awarded,
               coalesce(sum(award_amount), 0) AS awarded_amount,
               count(*) FILTER (WHERE helped_bucket = 'helped_within_1y') AS helped_within_1y,
               count(*) FILTER (WHERE helped_bucket = 'helped_over_1y') AS helped_over_1y,
               count(*) FILTER (WHERE is_repeat_applicant) AS repeat_applicants,
               coalesce(sum(p_return_1y), 0) AS expected_back_1y,
               count(p_return_1y) AS forecast_count
        FROM v_application_facts f WHERE {where}"""), params).mappings().one())


@router.get("/summary")
def summary(f: Filters = Depends(filters), conn: Connection = Depends(get_conn)):
    """Headline figures for the filters, plus the previous month's applicant
    count (for the change) and the month's budget. `budget_applies` is false
    when the filters show only part of the programme: the whole budget is
    then not the right comparison."""
    out = _summary(conn, f)
    out["previous_applicants"] = _summary(conn, f.previous_month())["applicants"] if f.month else None
    budget = None
    if f.month:
        budget = conn.execute(text("""SELECT sum(budget_total) FROM funding_cycles
                                      WHERE date_trunc('month', period_start) = :m"""), {"m": f.month}).scalar()
    out["budget"] = budget
    out["budget_applies"] = budget is not None and not f.narrows_population
    return out


@router.get("/support-types")
def support_types(f: Filters = Depends(filters), conn: Connection = Depends(get_conn)):
    """Per support group: applicants, who had been helped before (within or
    over a year ago), awards, and expected returns within a year."""
    where, params = f.where()
    return conn.execute(text(f"""
        SELECT support_group, count(*) AS applicants,
               count(*) FILTER (WHERE helped_bucket = 'helped_within_1y') AS helped_within_1y,
               count(*) FILTER (WHERE helped_bucket = 'helped_over_1y') AS helped_over_1y,
               count(*) FILTER (WHERE helped_bucket = 'never_helped') AS first_help,
               count(*) FILTER (WHERE awarded) AS awarded,
               coalesce(sum(award_amount), 0) AS awarded_amount,
               coalesce(sum(p_return_1y), 0) AS expected_back_1y
        FROM v_application_facts f WHERE {where}
        GROUP BY support_group ORDER BY count(*) DESC"""), params).mappings().all()


@router.get("/outcomes")
def outcomes(f: Filters = Depends(filters), conn: Connection = Depends(get_conn)):
    """What happened to the applications: counts by status. (What actually
    happened, not what the current model would band them as.)"""
    where, params = f.where()
    return conn.execute(text(f"""
        SELECT status, count(*) AS applications, coalesce(sum(award_amount), 0) AS awarded_amount
        FROM v_application_facts f WHERE {where} GROUP BY status ORDER BY count(*) DESC"""), params).mappings().all()


@router.get("/monthly")
def monthly(f: Filters = Depends(filters), months: int = Query(7, ge=1, le=36), conn: Connection = Depends(get_conn)):
    """Applicants per month and support group for the `months` months ending
    at the selected month (or the latest one). Every other filter applies."""
    where, params = f.where(month=False)
    end = f.month or conn.execute(text("SELECT max(date_trunc('month', submitted_at))::date FROM applications")).scalar()
    return conn.execute(text(f"""
        SELECT month, support_group, count(*) AS applicants, count(*) FILTER (WHERE awarded) AS awarded
        FROM v_application_facts f
        WHERE {where} AND month <= :end AND month > (CAST(:end AS date) - make_interval(months => :n))
        GROUP BY month, support_group ORDER BY month, support_group"""), {**params, "end": end, "n": months}).mappings().all()


@router.get("/districts")
def districts(f: Filters = Depends(filters), conn: Connection = Depends(get_conn)):
    """Applicants and amounts requested per district, most applicants first."""
    where, params = f.where()
    return conn.execute(text(f"""
        SELECT area_code, area_name, region, urban_rural, count(*) AS applicants,
               coalesce(sum(amount_requested), 0) AS requested, count(*) FILTER (WHERE awarded) AS awarded
        FROM v_application_facts f WHERE {where}
        GROUP BY area_code, area_name, region, urban_rural ORDER BY count(*) DESC, area_name"""), params).mappings().all()


# ---------------------------------------------------------------------------
# View-backed series (unfiltered, kept for exports and the API guide)
# ---------------------------------------------------------------------------

@router.get("/monthly-support")
def monthly_support(start: date | None = None, end: date | None = None, conn: Connection = Depends(get_conn)):
    return conn.execute(text(f"SELECT * FROM v_monthly_support WHERE {DATE_RANGE} ORDER BY month, support_group"),
                        {"start": start, "end": end}).mappings().all()


@router.get("/monthly-applications")
def monthly_applications(start: date | None = None, end: date | None = None, conn: Connection = Depends(get_conn)):
    return conn.execute(text(f"SELECT * FROM v_monthly_applications WHERE {DATE_RANGE} ORDER BY month, need_category"),
                        {"start": start, "end": end}).mappings().all()


@router.get("/repeat-support")
def repeat_support(start: date | None = None, end: date | None = None, conn: Connection = Depends(get_conn)):
    return conn.execute(text("""
        SELECT repeat_bucket, helped_bucket, count(*) AS applications
        FROM v_repeat_support
        WHERE (CAST(:start AS date) IS NULL OR submitted_at >= :start)
          AND (CAST(:end AS date) IS NULL OR submitted_at < CAST(:end AS date) + 1)
        GROUP BY 1, 2 ORDER BY 1, 2"""), {"start": start, "end": end}).mappings().all()


@router.get("/cycles")
def cycles(conn: Connection = Depends(get_conn)):
    return conn.execute(text("SELECT * FROM v_cycle_summary ORDER BY period_start")).mappings().all()


@router.get("/repeat-forecast")
def repeat_forecast(start: date | None = None, end: date | None = None, conn: Connection = Depends(get_conn)):
    """Expected vs actual returns within a year, per month and support group.
    Planning only; the forecast never touches allocation."""
    return conn.execute(text(f"SELECT * FROM v_repeat_forecast WHERE {DATE_RANGE} ORDER BY month, support_group"),
                        {"start": start, "end": end}).mappings().all()


# ---------------------------------------------------------------------------
# Programme monitoring (admin)
# ---------------------------------------------------------------------------

@router.get("/fairness", dependencies=admin_only)
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


@router.get("/model-health", dependencies=admin_only)
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


@router.get("/drift", dependencies=admin_only)
def drift(conn: Connection = Depends(get_conn)):
    """Latest drift report for the active need model, plus the status of
    recent reports. Produced monthly by `python -m backend.ml drift`."""
    version = conn.execute(text(
        "SELECT model_version FROM model_versions WHERE status = 'active' AND purpose = 'need'")).scalar()
    latest = conn.execute(text("""SELECT * FROM drift_reports WHERE model_version = :v
                                  ORDER BY created_at DESC LIMIT 1"""), {"v": version}).mappings().first()
    history = conn.execute(text("""SELECT report_id, window_start, window_end, applications, status, created_at
                                   FROM drift_reports WHERE model_version = :v
                                   ORDER BY created_at DESC LIMIT 12"""), {"v": version}).mappings().all()
    return {"model_version": version, "latest": latest, "history": history,
            "note": None if latest else "No drift report yet for this model: run `python -m backend.ml drift`."}


@router.get("/override-trend", dependencies=admin_only)
def override_trend(conn: Connection = Depends(get_conn)):
    """Per month: reviews, how often reviewers reversed the model's lean,
    and how often they approved. Below 5% overrides is flagged."""
    rows = conn.execute(text("""
        SELECT date_trunc('month', reviewed_at)::date AS month, count(*) AS reviews,
               avg(overridden::int) AS override_rate,
               avg((final_decision = 'approved')::int) AS approval_rate
        FROM application_reviews GROUP BY 1 ORDER BY 1""")).mappings().all()
    return [{**r, "override_rate_ok": float(r["override_rate"]) >= 0.05} for r in rows]
