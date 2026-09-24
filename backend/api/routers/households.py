"""Household data entry: the household, its survey waves, and its
audit-only protected attributes (write-only here — they are never returned
by any per-household endpoint)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import insert, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from ..database import get_conn, get_database
from ..schemas import HouseholdCreate, ProtectedAttributes, SurveyCreate

router = APIRouter(prefix="/households", tags=["households"])


def _require_household(conn: Connection, household_id: UUID) -> None:
    if conn.execute(text("SELECT 1 FROM households WHERE household_id = :h"), {"h": household_id}).first() is None:
        raise HTTPException(404, f"No household {household_id}")


@router.post("", status_code=201)
def create_household(body: HouseholdCreate, conn: Connection = Depends(get_conn)):
    if conn.execute(text("SELECT 1 FROM area_reference WHERE area_code = :a"), {"a": body.area_code}).first() is None:
        raise HTTPException(422, f"Unknown area_code {body.area_code!r} (see GET /areas)")
    t = get_database().table("households")
    return conn.execute(insert(t).values(**body.model_dump()).returning(t)).mappings().one()


@router.get("")
def list_households(area_code: str | None = None, region: str | None = None,
                    q: str | None = Query(None, description="Household id prefix or district"),
                    limit: int = Query(50, le=500), offset: int = 0, conn: Connection = Depends(get_conn)):
    """Households newest first, with their latest survey, household size,
    applications and awards. Filters by district / region (the sidebar's)
    and a search."""
    where = """(CAST(:area AS text) IS NULL OR h.area_code = :area)
               AND (CAST(:region AS text) IS NULL OR a.region = :region)
               AND (CAST(:qp AS text) IS NULL OR h.household_id::text LIKE :qp OR a.area_name ILIKE :qp)"""
    qp = f"{q.strip().lower().removeprefix('hh-')}%" if q else None
    params = {"area": area_code, "region": region, "qp": qp}
    total = conn.execute(text(f"SELECT count(*) FROM households h JOIN area_reference a USING (area_code) WHERE {where}"),
                         params).scalar()
    items = conn.execute(text(f"""
        SELECT h.household_id, h.area_code, h.registered_at, a.area_name, a.region, a.urban_rural::text AS urban_rural,
               s.survey_date AS last_survey, s.household_size,
               (SELECT count(*) FROM applications ap WHERE ap.household_id = h.household_id) AS applications_count,
               (SELECT count(*) FROM awards aw WHERE aw.household_id = h.household_id) AS awards_count
        FROM households h JOIN area_reference a USING (area_code)
        LEFT JOIN LATERAL (SELECT survey_date, household_size FROM household_surveys s
                           WHERE s.household_id = h.household_id ORDER BY survey_date DESC LIMIT 1) s ON true
        WHERE {where}
        ORDER BY h.registered_at DESC, h.household_id LIMIT :limit OFFSET :offset"""),
        {**params, "limit": limit, "offset": offset}).mappings().all()
    return {"total": total, "items": items}


@router.get("/{household_id}")
def get_household(household_id: UUID, conn: Connection = Depends(get_conn)):
    """The household, its survey waves (newest first) and its applications.
    Protected attributes are deliberately absent — only whether the
    audit-only questions were answered."""
    h = conn.execute(text("""SELECT h.*, a.area_name, a.urban_rural, a.region,
                                    EXISTS (SELECT 1 FROM protected_attributes p WHERE p.household_id = h.household_id)
                                        AS audit_questions_answered
                             FROM households h
                             JOIN area_reference a USING (area_code) WHERE household_id = :h"""),
                     {"h": household_id}).mappings().first()
    if h is None:
        raise HTTPException(404, f"No household {household_id}")
    surveys = conn.execute(text("SELECT * FROM household_surveys WHERE household_id = :h ORDER BY survey_date DESC"),
                           {"h": household_id}).mappings().all()
    apps = conn.execute(text("""SELECT application_id, cycle_id, submitted_at, need_category, amount_requested, status
                                FROM applications WHERE household_id = :h ORDER BY submitted_at DESC"""),
                        {"h": household_id}).mappings().all()
    return {**h, "surveys": surveys, "applications": apps}


@router.post("/{household_id}/surveys", status_code=201)
def add_survey(household_id: UUID, body: SurveyCreate, conn: Connection = Depends(get_conn)):
    _require_household(conn, household_id)
    t = get_database().table("household_surveys")
    return conn.execute(insert(t).values(household_id=household_id, **body.model_dump()).returning(t)).mappings().one()


@router.put("/{household_id}/protected-attributes", status_code=204)
def set_protected_attributes(household_id: UUID, body: ProtectedAttributes, conn: Connection = Depends(get_conn)):
    """AUDIT ONLY: used to measure disparate impact, never read by the model,
    and only ever reported in aggregate (GET /dashboard/fairness)."""
    _require_household(conn, household_id)
    t = get_database().table("protected_attributes")
    values = {"household_id": household_id, **body.model_dump()}
    conn.execute(pg_insert(t).values(**values).on_conflict_do_update(
        index_elements=["household_id"], set_=body.model_dump()))
