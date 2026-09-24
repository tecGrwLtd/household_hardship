"""Applications: submit (with a provisional need estimate), look up, list,
and appeal a deferral."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import insert, text
from sqlalchemy.engine import Connection

from ..auth import User, current_user
from ..database import get_conn, get_database
from ..filters import Filters, filters
from ..schemas import ApplicationCreate
from ..platform import log
from ..scoring import provisional

router = APIRouter(prefix="/applications", tags=["applications"])

# "Fraction of optional fields filled" (design spec) — computed when the
# client does not send it.
OPTIONAL_FIELDS = ("stated_need_amount", "days_since_hardship_onset", "referral_source",
                   "application_channel", "documentation_provided")


@router.post("", status_code=201)
def submit_application(body: ApplicationCreate, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """Record an application and return a provisional need estimate. The
    decision band comes later, from POST /cycles/{id}/allocate. A caseworker's
    applications are always attributed to them."""
    cycle = conn.execute(text("SELECT * FROM funding_cycles WHERE cycle_id = :c"), {"c": body.cycle_id}).mappings().first()
    if cycle is None:
        raise HTTPException(422, f"No funding cycle {body.cycle_id}")
    if conn.execute(text("SELECT 1 FROM households WHERE household_id = :h"), {"h": body.household_id}).first() is None:
        raise HTTPException(422, f"No household {body.household_id}")
    caseworker_id = user.caseworker_id if not user.is_admin else body.caseworker_id
    if caseworker_id is not None and conn.execute(
            text("SELECT 1 FROM caseworkers WHERE caseworker_id = :c"), {"c": caseworker_id}).first() is None:
        raise HTTPException(422, f"No caseworker {caseworker_id}")

    submitted_at = body.submitted_at or datetime.now(timezone.utc)
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=timezone.utc)
    if not cycle["period_start"] <= submitted_at.date() <= cycle["period_end"]:
        raise HTTPException(422, f"submitted_at {submitted_at.date()} is outside cycle {body.cycle_id} "
                                 f"({cycle['period_start']} to {cycle['period_end']})")

    # Repeat history is derived here, from the record — never trusted from the client.
    history = conn.execute(text("""
        SELECT count(*) AS n, max(submitted_at) AS last FROM applications
        WHERE household_id = :h AND submitted_at < :t"""), {"h": body.household_id, "t": submitted_at}).mappings().one()

    values = body.model_dump(exclude={"submitted_at"})
    values["caseworker_id"] = caseworker_id
    if values["application_completeness"] is None:
        values["application_completeness"] = round(
            sum(values[f] is not None for f in OPTIONAL_FIELDS) / len(OPTIONAL_FIELDS), 3)
    values.update(
        submitted_at=submitted_at,
        prior_applications_count=history["n"],
        days_since_last_application=(submitted_at - history["last"]).days if history["last"] else None,
        status="submitted",
    )
    t = get_database().table("applications")
    app = dict(conn.execute(insert(t).values(**values).returning(t)).mappings().one())
    log(conn, user.username, "application.submit", str(app["application_id"]),
        {"cycle_id": body.cycle_id, "need_category": body.need_category, "amount_requested": body.amount_requested})
    return {**app, "provisional_score": provisional(conn, app["application_id"])}


@router.get("")
def list_applications(f: Filters = Depends(filters), status: str | None = None,
                      q: str | None = Query(None, description="Application id, household id prefix or district"),
                      cycle_id: int | None = None, household_id: str | None = None,
                      limit: int = Query(25, le=200), offset: int = 0, conn: Connection = Depends(get_conn)):
    """Applications for the global filters, newest first, with the total and
    per-status counts (for the tabs) of the same selection before `status`
    narrows it."""
    where, params = f.where()
    extra, p2 = [], {}
    if cycle_id is not None:
        extra.append("f.cycle_id = :cycle")
        p2["cycle"] = cycle_id
    if household_id:
        extra.append("f.household_id::text = :hh")
        p2["hh"] = household_id
    if q:
        extra.append("(f.application_id::text = :q OR f.household_id::text LIKE :qp OR f.area_name ILIKE :qp)")
        p2.update(q=q.strip().lstrip("#"), qp=f"{q.strip().lower().removeprefix('hh-')}%")
    base = where + "".join(f" AND {e}" for e in extra)
    params = {**params, **p2}

    counts = conn.execute(text(f"SELECT status, count(*) AS n FROM v_application_facts f WHERE {base} GROUP BY status"),
                          params).mappings().all()
    status_where = base + (" AND f.status = :status" if status else "")
    items = conn.execute(text(f"""
        SELECT f.application_id, f.household_id, f.cycle_id, f.submitted_at, f.need_category, f.support_group,
               f.amount_requested, f.status, f.area_name, f.region, f.awarded, f.award_amount,
               c.display_name AS caseworker, f.caseworker_id
        FROM v_application_facts f LEFT JOIN caseworkers c ON c.caseworker_id = f.caseworker_id
        WHERE {status_where}
        ORDER BY f.submitted_at DESC, f.application_id DESC LIMIT :limit OFFSET :offset"""),
        {**params, "status": status, "limit": limit, "offset": offset}).mappings().all()
    status_counts = {r["status"]: r["n"] for r in counts}
    return {"total": status_counts.get(status, 0) if status else sum(status_counts.values()),
            "status_counts": status_counts, "items": items}


@router.get("/{application_id}")
def get_application(application_id: int, conn: Connection = Depends(get_conn)):
    """The application with everything a reviewer needs: its district, the
    survey it was scored on (latest on or before submission), latest score
    and explanation (or a provisional estimate), reviews, award, and the
    household's other applications."""
    app = conn.execute(text("""
        SELECT a.*, support_group(a.need_category) AS support_group, ar.area_name, ar.region,
               ar.urban_rural::text AS urban_rural, c.display_name AS caseworker
        FROM applications a JOIN households h USING (household_id) JOIN area_reference ar USING (area_code)
        LEFT JOIN caseworkers c ON c.caseworker_id = a.caseworker_id
        WHERE a.application_id = :a"""), {"a": application_id}).mappings().first()
    if app is None:
        raise HTTPException(404, f"No application {application_id}")
    survey = conn.execute(text("""
        SELECT * FROM household_surveys WHERE household_id = :h AND survey_date <= CAST(:t AS date)
        ORDER BY survey_date DESC, survey_id DESC LIMIT 1"""),
        {"h": app["household_id"], "t": app["submitted_at"]}).mappings().first()
    score = conn.execute(text("""SELECT * FROM model_scores WHERE application_id = :a
                                 ORDER BY scored_at DESC, score_id DESC LIMIT 1"""), {"a": application_id}).mappings().first()
    reviews = conn.execute(text("""SELECT r.*, c.display_name AS caseworker FROM application_reviews r
                                   LEFT JOIN caseworkers c USING (caseworker_id)
                                   WHERE application_id = :a ORDER BY reviewed_at"""), {"a": application_id}).mappings().all()
    award = conn.execute(text("SELECT * FROM awards WHERE application_id = :a"), {"a": application_id}).mappings().first()
    history = conn.execute(text("""
        SELECT a.application_id, a.submitted_at, a.need_category, support_group(a.need_category) AS support_group,
               a.amount_requested, a.status::text AS status, aw.award_amount
        FROM applications a LEFT JOIN awards aw USING (application_id)
        WHERE a.household_id = :h ORDER BY a.submitted_at"""), {"h": app["household_id"]}).mappings().all()
    forecast = conn.execute(text("""SELECT p_return_1y, model_version FROM repeat_forecasts WHERE application_id = :a
                                    ORDER BY forecast_at DESC, forecast_id DESC LIMIT 1"""), {"a": application_id}).mappings().first()
    return {**app, "survey": survey, "score": score,
            "provisional_score": None if score else provisional(conn, application_id),
            "reviews": reviews, "award": award, "history": history, "repeat_forecast": forecast}


@router.post("/{application_id}/appeal")
def appeal(application_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """The appeal route every deferral must have (design spec): puts the
    application back in front of a human in the review queue."""
    row = conn.execute(text("""UPDATE applications SET status = 'appealed'
                               WHERE application_id = :a AND status = 'deferred' RETURNING *"""),
                       {"a": application_id}).mappings().first()
    if row is None:
        exists = conn.execute(text("SELECT status FROM applications WHERE application_id = :a"),
                              {"a": application_id}).first()
        if exists is None:
            raise HTTPException(404, f"No application {application_id}")
        raise HTTPException(409, f"Only a deferred application can be appealed (this one is {exists[0]!r})")
    log(conn, user.username, "application.appeal", str(application_id))
    return row
