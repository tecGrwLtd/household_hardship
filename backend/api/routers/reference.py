"""Reference data the data-entry forms need: areas, caseworkers, funding
cycles, and the need categories with their dashboard support groups."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import insert, text
from sqlalchemy.engine import Connection

from ..auth import require_admin
from ..database import get_conn, get_database
from ..schemas import CycleCreate
from ..scoring import cycle_budget

router = APIRouter(tags=["reference"])


@router.get("/areas")
def list_areas(conn: Connection = Depends(get_conn)):
    return conn.execute(text("SELECT * FROM area_reference ORDER BY area_name")).mappings().all()


@router.get("/caseworkers")
def list_caseworkers(conn: Connection = Depends(get_conn)):
    return conn.execute(text("SELECT * FROM caseworkers ORDER BY display_name")).mappings().all()


@router.get("/need-categories")
def need_categories(conn: Connection = Depends(get_conn)):
    """Every need category with its dashboard support group — the grouping
    lives in the database (support_group()) so API and views agree."""
    return conn.execute(text("""
        SELECT c::text AS need_category, support_group(c) AS support_group
        FROM unnest(enum_range(NULL::need_category_enum)) AS c""")).mappings().all()


@router.get("/cycles")
def list_cycles(conn: Connection = Depends(get_conn)):
    return conn.execute(text("SELECT * FROM v_cycle_summary ORDER BY period_start DESC")).mappings().all()


@router.post("/cycles", status_code=201, dependencies=[Depends(require_admin)])
def create_cycle(body: CycleCreate, conn: Connection = Depends(get_conn)):
    if body.period_end <= body.period_start:
        raise HTTPException(422, "period_end must be after period_start")
    t = get_database().table("funding_cycles")
    return conn.execute(insert(t).values(**body.model_dump()).returning(t)).mappings().one()


@router.get("/cycles/{cycle_id}/budget")
def get_cycle_budget(cycle_id: int, conn: Connection = Depends(get_conn)):
    return cycle_budget(conn, cycle_id)
