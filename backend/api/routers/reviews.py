"""The human in the loop. Everything the model could not decide safely —
the human_review band and appealed deferrals — lands here, and only a
person's decision finalises it (GDPR Art. 22 / EU AI Act Art. 14; design
spec: the automated path never issues a final refusal)."""
from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..auth import User, current_user
from ..database import get_conn
from ..filters import Filters, filters
from ..schemas import ReviewCreate
from ..platform import log
from ..scoring import create_award, cycle_budget, lock_cycle, refresh_repeat_forecasts

router = APIRouter(prefix="/reviews", tags=["reviews"])
QUEUE_STATUSES = ("in_review", "appealed")

LATEST_SCORE = """LEFT JOIN LATERAL (SELECT * FROM model_scores ms WHERE ms.application_id = a.application_id
                                    ORDER BY scored_at DESC, score_id DESC LIMIT 1) s ON true"""


@router.get("/queue")
def review_queue(f: Filters = Depends(filters), mine: bool | None = Query(None, description="Default: yes for caseworkers, no for admins"),
                 kind: str | None = Query(None, pattern="^(review|appeal)$"), cycle_id: int | None = None,
                 limit: int = Query(100, le=500), user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """Cases waiting for a person, neediest first, each with the model's lean,
    the top drivers of its score, and flags a reviewer should see (deferred
    repeatedly in the last year, never helped). `model_lean` is what the
    score alone suggests; the reviewer has full authority to decide
    otherwise. The global filters apply except the month: a worklist must
    not hide an older appeal. `counts` are for the tabs."""
    mine = (not user.is_admin) if mine is None else mine
    # `mine` is also a global-filter parameter; here it only picks the tab,
    # so the tab counts (all / mine / appeals) stay about the whole queue.
    where, params = replace(f, caseworker_id=None).where(month=False)
    base = f"f.status IN ('in_review', 'appealed') AND {where}"
    if cycle_id is not None:
        base += " AND f.cycle_id = :cycle"
        params["cycle"] = cycle_id
    params["me"] = user.caseworker_id
    counts = conn.execute(text(f"""
        SELECT count(*) AS all, count(*) FILTER (WHERE f.caseworker_id = :me) AS mine,
               count(*) FILTER (WHERE f.status = 'in_review') AS review, count(*) FILTER (WHERE f.status = 'appealed') AS appeal
        FROM v_application_facts f WHERE {base}"""), params).mappings().one()
    sel = base + (" AND f.caseworker_id = :me" if mine else "") + (
        {"review": " AND f.status = 'in_review'", "appeal": " AND f.status = 'appealed'"}.get(kind, ""))
    items = conn.execute(text(f"""
        SELECT f.application_id, f.household_id, f.cycle_id, f.status, f.need_category, f.support_group,
               f.amount_requested, f.caseworker_id, c.display_name AS caseworker, f.submitted_at,
               f.area_name, f.region, f.helped_bucket,
               s.band, s.need_lo, s.need_mid, s.need_hi, s.cutoff, s.top_shap_features, s.model_version,
               CASE WHEN s.cutoff IS NULL OR s.need_mid >= s.cutoff THEN 'approve' ELSE 'deny' END AS model_lean,
               (SELECT count(*) FROM applications p WHERE p.household_id = f.household_id
                   AND p.status = 'deferred' AND p.application_id <> f.application_id
                   AND p.submitted_at > f.submitted_at - interval '365 days') AS deferred_last_year
        FROM v_application_facts f
        LEFT JOIN caseworkers c ON c.caseworker_id = f.caseworker_id
        LEFT JOIN LATERAL (SELECT * FROM model_scores ms WHERE ms.application_id = f.application_id
                           ORDER BY scored_at DESC, score_id DESC LIMIT 1) s ON true
        WHERE {sel}
        ORDER BY s.need_mid DESC NULLS LAST, f.submitted_at LIMIT :limit"""), {**params, "limit": limit}).mappings().all()
    return {"counts": counts, "mine": mine, "items": items}


@router.post("/{application_id}")
def decide(application_id: int, body: ReviewCreate, user: User = Depends(current_user),
           conn: Connection = Depends(get_conn)):
    """Approve or deny a case in the queue. A reason is required and kept with
    the decision. A caseworker's decision is always attributed to them; an
    admin may record it for another caseworker."""
    if not (body.notes or "").strip():
        raise HTTPException(422, "A reason is required: it is kept with the decision")
    app = conn.execute(text(f"""SELECT a.*, s.band, s.need_mid, s.cutoff FROM applications a {LATEST_SCORE}
                                WHERE a.application_id = :a FOR UPDATE OF a"""),
                       {"a": application_id}).mappings().first()
    if app is None:
        raise HTTPException(404, f"No application {application_id}")
    if app["status"] not in QUEUE_STATUSES:
        raise HTTPException(409, f"Application {application_id} is {app['status']!r}, not waiting for review")
    if app["band"] is None:
        raise HTTPException(409, "Application has no model score; allocate its cycle first")

    caseworker_id = (body.caseworker_id or app["caseworker_id"]) if user.is_admin else user.caseworker_id
    if caseworker_id is None:
        raise HTTPException(422, "caseworker_id is required: this application has no assigned caseworker")

    approve = body.decision == "approve"
    if approve:
        lock_cycle(conn, app["cycle_id"])      # two approvals must not both spend the last of the budget
        budget = cycle_budget(conn, app["cycle_id"])
        if float(app["amount_requested"]) > budget["remaining"]:
            raise HTTPException(409, f"Approving would exceed the cycle budget: "
                                     f"{budget['remaining']:,.0f} {budget['currency']} left, "
                                     f"{float(app['amount_requested']):,.0f} requested")

    # Override = the reviewer reversed the model's lean. The spec reads a
    # review function with ~1% overrides as a rubber stamp (GET /dashboard/model-health).
    lean_approve = app["cutoff"] is None or app["need_mid"] >= app["cutoff"]
    review = conn.execute(text("""
        INSERT INTO application_reviews (application_id, caseworker_id, initial_band, final_decision, overridden, notes)
        VALUES (:a, :c, :band, :decision, :overridden, :notes) RETURNING *"""), {
        "a": application_id, "c": caseworker_id, "band": app["band"],
        "decision": "approved" if approve else ("appeal_denied" if app["status"] == "appealed" else "denied"),
        "overridden": approve != lean_approve, "notes": body.notes,
    }).mappings().one()

    # Denying an appeal closes the case; denying a first review defers it,
    # which keeps the appeal route open.
    new_status = "awarded" if approve else ("closed" if app["status"] == "appealed" else "deferred")
    conn.execute(text("UPDATE applications SET status = :s WHERE application_id = :a"),
                 {"s": new_status, "a": application_id})
    if approve:
        create_award(conn, application_id)
    refresh_repeat_forecasts(conn, [application_id])
    log(conn, user.username, "review.decide", str(application_id),
        {"decision": body.decision, "overridden": review["overridden"], "status": new_status})
    return {"review": review, "status": new_status}
