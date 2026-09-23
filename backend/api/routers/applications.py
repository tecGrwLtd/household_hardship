"""Applications: submit (with a provisional need estimate), look up, list,
and appeal a deferral."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import insert, text
from sqlalchemy.engine import Connection

from ..database import get_conn, get_database
from ..schemas import ApplicationCreate
from ..scoring import provisional

router = APIRouter(prefix="/applications", tags=["applications"])

# "Fraction of optional fields filled" (design spec) — computed when the
# client does not send it.
OPTIONAL_FIELDS = ("stated_need_amount", "days_since_hardship_onset", "referral_source",
                   "application_channel", "documentation_provided")


@router.post("", status_code=201)
def submit_application(body: ApplicationCreate, conn: Connection = Depends(get_conn)):
    """Record an application and return a provisional need estimate. The
    decision band comes later, from POST /cycles/{id}/allocate."""
    cycle = conn.execute(text("SELECT * FROM funding_cycles WHERE cycle_id = :c"), {"c": body.cycle_id}).mappings().first()
    if cycle is None:
        raise HTTPException(422, f"No funding cycle {body.cycle_id}")
    if conn.execute(text("SELECT 1 FROM households WHERE household_id = :h"), {"h": body.household_id}).first() is None:
        raise HTTPException(422, f"No household {body.household_id}")
    if body.caseworker_id is not None and conn.execute(
            text("SELECT 1 FROM caseworkers WHERE caseworker_id = :c"), {"c": body.caseworker_id}).first() is None:
        raise HTTPException(422, f"No caseworker {body.caseworker_id}")

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
    return {**app, "provisional_score": provisional(conn, app["application_id"])}


@router.get("")
def list_applications(cycle_id: int | None = None, status: str | None = None,
                      household_id: str | None = None, limit: int = Query(50, le=500), offset: int = 0,
                      conn: Connection = Depends(get_conn)):
    return conn.execute(text("""
        SELECT a.application_id, a.household_id, a.cycle_id, a.submitted_at, a.need_category,
               support_group(a.need_category) AS support_group, a.amount_requested, a.status,
               s.band, s.need_mid, s.model_version
        FROM applications a
        LEFT JOIN LATERAL (SELECT band, need_mid, model_version FROM model_scores ms
                           WHERE ms.application_id = a.application_id
                           ORDER BY scored_at DESC, score_id DESC LIMIT 1) s ON true
        WHERE (CAST(:cycle AS int) IS NULL OR a.cycle_id = :cycle)
          AND (CAST(:status AS text) IS NULL OR a.status::text = :status)
          AND (CAST(:hh AS text) IS NULL OR a.household_id::text = :hh)
        ORDER BY a.submitted_at DESC LIMIT :limit OFFSET :offset"""),
        {"cycle": cycle_id, "status": status, "hh": household_id, "limit": limit, "offset": offset}).mappings().all()


@router.get("/{application_id}")
def get_application(application_id: int, conn: Connection = Depends(get_conn)):
    """The application with its latest score and explanation (or a
    provisional estimate if its cycle has not been allocated yet), reviews
    and award."""
    app = conn.execute(text("SELECT *, support_group(need_category) AS support_group FROM applications "
                            "WHERE application_id = :a"), {"a": application_id}).mappings().first()
    if app is None:
        raise HTTPException(404, f"No application {application_id}")
    score = conn.execute(text("""SELECT * FROM model_scores WHERE application_id = :a
                                 ORDER BY scored_at DESC, score_id DESC LIMIT 1"""), {"a": application_id}).mappings().first()
    reviews = conn.execute(text("SELECT * FROM application_reviews WHERE application_id = :a ORDER BY reviewed_at"),
                           {"a": application_id}).mappings().all()
    award = conn.execute(text("SELECT * FROM awards WHERE application_id = :a"), {"a": application_id}).mappings().first()
    return {**app, "score": score, "provisional_score": None if score else provisional(conn, application_id),
            "reviews": reviews, "award": award}


@router.post("/{application_id}/appeal")
def appeal(application_id: int, conn: Connection = Depends(get_conn)):
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
    return row
