"""The signed-in person's own workload — the caseworker home page."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..auth import User, current_user
from ..database import get_conn

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/summary")
def my_summary(month: date | None = Query(None, description="Any day in the month; default the latest cycle's"),
               user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """Open cases waiting for this caseworker, their applications and awards
    for the month, the cycle's closing date, and their own review record
    (the override rate appears after 10 reviews, when it starts to mean
    something). Admins without a caseworker link get empty figures."""
    cycle = conn.execute(text("""
        SELECT cycle_id, period_start, period_end FROM funding_cycles
        WHERE CAST(:m AS date) IS NULL OR date_trunc('month', period_start) = date_trunc('month', CAST(:m AS date))
        ORDER BY period_start DESC LIMIT 1"""), {"m": month}).mappings().first()
    me = user.caseworker_id
    open_cases = conn.execute(text("""
        SELECT count(*) AS open, count(*) FILTER (WHERE status = 'appealed') AS appeals, min(submitted_at) AS oldest
        FROM applications WHERE caseworker_id = :me AND status IN ('in_review', 'appealed')"""), {"me": me}).mappings().one()
    mine = conn.execute(text("""
        SELECT count(*) AS applications, count(*) FILTER (WHERE awarded) AS helped,
               coalesce(sum(award_amount), 0) AS awarded_amount
        FROM v_application_facts WHERE caseworker_id = :me AND cycle_id = :c"""),
        {"me": me, "c": cycle["cycle_id"] if cycle else None}).mappings().one()
    groups = conn.execute(text("""
        SELECT support_group, count(*) AS applicants FROM v_application_facts
        WHERE caseworker_id = :me AND cycle_id = :c GROUP BY 1 ORDER BY 2 DESC"""),
        {"me": me, "c": cycle["cycle_id"] if cycle else None}).mappings().all()
    reviews = conn.execute(text("""SELECT count(*) AS reviews, avg(overridden::int) AS override_rate
                                   FROM application_reviews WHERE caseworker_id = :me"""), {"me": me}).mappings().one()
    return {
        "user": {"display_name": user.display_name, "role": user.role, "caseworker_id": me},
        "cycle": cycle,
        "open_cases": open_cases,
        "this_cycle": {**mine, "by_support_group": groups},
        "reviews": {"reviews": reviews["reviews"],
                    "override_rate": reviews["override_rate"] if reviews["reviews"] >= 10 else None},
    }
