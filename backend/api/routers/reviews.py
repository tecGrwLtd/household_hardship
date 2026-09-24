"""The human in the loop. Everything the model could not decide safely —
the human_review band and appealed deferrals — lands here, and only a
person's decision finalises it (GDPR Art. 22 / EU AI Act Art. 14; design
spec: the automated path never issues a final refusal)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..database import get_conn
from ..schemas import ReviewCreate
from ..scoring import create_award, cycle_budget, lock_cycle, refresh_repeat_forecasts

router = APIRouter(prefix="/reviews", tags=["reviews"])
QUEUE_STATUSES = ("in_review", "appealed")

LATEST_SCORE = """LEFT JOIN LATERAL (SELECT * FROM model_scores ms WHERE ms.application_id = a.application_id
                                    ORDER BY scored_at DESC, score_id DESC LIMIT 1) s ON true"""


@router.get("/queue")
def review_queue(cycle_id: int | None = None, conn: Connection = Depends(get_conn)):
    """Cases waiting for a human, neediest first, each with the model's lean
    and the top drivers of its score. `model_lean` is what the score alone
    would suggest; the reviewer has full authority to decide otherwise."""
    return conn.execute(text(f"""
        SELECT a.application_id, a.household_id, a.cycle_id, a.status, a.need_category,
               support_group(a.need_category) AS support_group, a.amount_requested, a.caseworker_id,
               s.band, s.need_lo, s.need_mid, s.need_hi, s.cutoff, s.top_shap_features, s.model_version,
               CASE WHEN s.cutoff IS NULL OR s.need_mid >= s.cutoff THEN 'approve' ELSE 'deny' END AS model_lean
        FROM applications a {LATEST_SCORE}
        WHERE a.status::text IN ('in_review', 'appealed')
          AND (CAST(:cycle AS int) IS NULL OR a.cycle_id = :cycle)
        ORDER BY s.need_mid DESC NULLS LAST"""), {"cycle": cycle_id}).mappings().all()


@router.post("/{application_id}")
def decide(application_id: int, body: ReviewCreate, conn: Connection = Depends(get_conn)):
    app = conn.execute(text(f"""SELECT a.*, s.band, s.need_mid, s.cutoff FROM applications a {LATEST_SCORE}
                                WHERE a.application_id = :a FOR UPDATE OF a"""),
                       {"a": application_id}).mappings().first()
    if app is None:
        raise HTTPException(404, f"No application {application_id}")
    if app["status"] not in QUEUE_STATUSES:
        raise HTTPException(409, f"Application {application_id} is {app['status']!r}, not waiting for review")
    if app["band"] is None:
        raise HTTPException(409, "Application has no model score; allocate its cycle first")

    caseworker_id = body.caseworker_id or app["caseworker_id"]
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
    return {"review": review, "status": new_status}
