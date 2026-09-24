"""Allocation of a funding cycle: preview (dry run) and allocate (commit)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.engine import Connection

from ..auth import require_admin
from ..database import get_conn
from ..scoring import commit_allocation, lock_cycle, plan_allocation, plan_to_json

router = APIRouter(prefix="/cycles", tags=["allocation"], dependencies=[Depends(require_admin)])


@router.get("/{cycle_id}/preview")
def preview(cycle_id: int, conn: Connection = Depends(get_conn)):
    """What allocating now would do to the cycle's newly submitted
    applications — bands, cutoff, budget split. Writes nothing. The audit
    sample is random, so which deferred applications it picks differs
    between a preview and the real allocation."""
    return plan_to_json(plan_allocation(conn, cycle_id))


@router.post("/{cycle_id}/allocate")
def allocate_cycle(cycle_id: int, conn: Connection = Depends(get_conn)):
    """Score and band every SUBMITTED application in the cycle against the
    budget still unspent, then: auto- and audit-approved get an award now;
    human_review go to the review queue (GET /reviews/queue); defer are
    deferred with an appeal route. Applications already decided keep their
    decisions, so this can be re-run for late applications."""
    lock_cycle(conn, cycle_id)   # one allocation or approval at a time per cycle
    plan = plan_allocation(conn, cycle_id)
    if plan["model_version"] is not None:
        commit_allocation(conn, cycle_id, plan)
    return plan_to_json(plan)
