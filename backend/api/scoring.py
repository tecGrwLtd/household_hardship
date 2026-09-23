"""Glue between the API and backend/ml: build features for just the
applications a request touches, score them with the active model version,
and run a cycle's allocation. All the modelling logic stays in backend/ml."""
from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass

import numpy as np
import pandas as pd
from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from ..ml import registry
from ..ml.allocate import allocate, budget_summary
from ..ml.features import build_features

AWARDED_STATUSES = ("auto_approved", "audit_approved", "awarded")
STATUS_FOR_BAND = {
    "auto_approve": "auto_approved",
    "audit_approve": "audit_approved",
    "human_review": "in_review",
    "defer": "deferred",          # not a refusal: the applicant can appeal (POST /applications/{id}/appeal)
}


# ---------------------------------------------------------------------------
# Active model
# ---------------------------------------------------------------------------

class ModelCache:
    """Loads a version's artifact once. The active version is looked up per
    call (one indexed row), so an activation takes effect on the next request
    without a restart."""

    def __init__(self):
        self._models: dict[str, object] = {}
        self._lock = threading.Lock()

    def active(self, conn: Connection) -> tuple[dict, object]:
        row = conn.execute(text("SELECT * FROM model_versions WHERE status = 'active'")).mappings().first()
        if row is None:
            raise HTTPException(503, "No active model version. Activate one: POST /models/{version}/activate")
        row = dict(row)
        with self._lock:
            if row["model_version"] not in self._models:
                self._models[row["model_version"]] = registry.load(row["kind"], row["artifact_path"])
        return row, self._models[row["model_version"]]


models = ModelCache()


# ---------------------------------------------------------------------------
# Features for a subset of applications
# ---------------------------------------------------------------------------

def load_features(conn: Connection, *, application_ids: list[int] | None = None,
                  cycle_id: int | None = None, statuses: tuple[str, ...] | None = None) -> pd.DataFrame:
    """build_features() for only the applications asked for, pulling just
    their households' rows. Same function the training pipeline uses, so a
    score here is the score training would give."""
    where, params = [], {}
    if application_ids is not None:
        where.append("application_id IN :ids")
        params["ids"] = list(application_ids)
    if cycle_id is not None:
        where.append("cycle_id = :cycle_id")
        params["cycle_id"] = cycle_id
    if statuses is not None:
        where.append("status::text IN :statuses")
        params["statuses"] = list(statuses)
    sql = "SELECT * FROM applications" + (" WHERE " + " AND ".join(where) if where else "")
    stmt = text(sql)
    for name in ("ids", "statuses"):
        if name in params:
            stmt = stmt.bindparams(bindparam(name, expanding=True))
    apps = pd.read_sql(stmt, conn, params=params)
    if apps.empty:
        return pd.DataFrame()

    hh = {"h": [str(h) for h in apps["household_id"].unique()]}
    in_hh = bindparam("h", expanding=True)
    households = pd.read_sql(text("SELECT * FROM households WHERE household_id::text IN :h").bindparams(in_hh),
                             conn, params=hh)
    surveys = pd.read_sql(text("SELECT * FROM household_surveys WHERE household_id::text IN :h").bindparams(in_hh),
                          conn, params=hh)
    area = pd.read_sql(text("SELECT * FROM area_reference"), conn)
    return build_features(households, surveys, apps, area)


@dataclass
class Scored:
    version: str
    frame: pd.DataFrame      # application_id, household_id, cycle_id, amount_requested, need_*, drivers


def score(conn: Connection, feats: pd.DataFrame) -> Scored:
    row, model = models.active(conn)
    pred = model.predict(feats)
    frame = pd.concat([feats[["application_id", "household_id", "cycle_id", "amount_requested"]], pred], axis=1)
    frame["top_drivers"] = model.explain(feats, top_n=5)
    frame["has_survey"] = feats["survey_date"].notna().values
    return Scored(row["model_version"], frame)


def provisional(conn: Connection, application_id: int) -> dict | None:
    """Need estimate for one application before its cycle is allocated.
    It has no band yet: bands depend on who else applied and the budget."""
    feats = load_features(conn, application_ids=[application_id])
    if feats.empty:
        return None
    s = score(conn, feats)
    r = s.frame.iloc[0]
    return {
        "model_version": s.version,
        "need_lo": float(r.need_lo), "need_mid": float(r.need_mid), "need_hi": float(r.need_hi),
        "top_drivers": [{"feature": f, "contribution": v} for f, v in r.top_drivers],
        "has_survey": bool(r.has_survey),
        "note": "Provisional. The band (auto-approve / review / defer) is set when the cycle is allocated, "
                "because it depends on who else applied and on the budget left.",
    }


# ---------------------------------------------------------------------------
# Cycle allocation
# ---------------------------------------------------------------------------

def lock_cycle(conn: Connection, cycle_id: int) -> None:
    """Row-lock the cycle for the rest of the transaction, so concurrent
    allocations and approvals cannot both spend the same remaining budget."""
    if conn.execute(text("SELECT 1 FROM funding_cycles WHERE cycle_id = :c FOR UPDATE"), {"c": cycle_id}).first() is None:
        raise HTTPException(404, f"No funding cycle {cycle_id}")


def cycle_budget(conn: Connection, cycle_id: int) -> dict:
    row = conn.execute(text("""
        SELECT fc.cycle_id, fc.budget_total, fc.budget_currency,
               coalesce((SELECT sum(aw.award_amount) FROM awards aw
                         JOIN applications a ON a.application_id = aw.application_id
                         WHERE a.cycle_id = fc.cycle_id), 0) AS awarded
        FROM funding_cycles fc WHERE fc.cycle_id = :c"""), {"c": cycle_id}).mappings().first()
    if row is None:
        raise HTTPException(404, f"No funding cycle {cycle_id}")
    budget, awarded = float(row["budget_total"]), float(row["awarded"])
    return {"cycle_id": cycle_id, "budget_total": budget, "currency": row["budget_currency"],
            "awarded": awarded, "remaining": budget - awarded}


def plan_allocation(conn: Connection, cycle_id: int, rng: np.random.Generator | None = None) -> dict:
    """Rank the cycle's newly SUBMITTED applications against the budget that
    is still unspent. Nothing is written. Earlier allocations in the same
    cycle keep their decisions; late applications compete for what is left."""
    budget = cycle_budget(conn, cycle_id)
    feats = load_features(conn, cycle_id=cycle_id, statuses=("submitted",))
    if feats.empty:
        return {"budget": budget, "model_version": None, "cutoff": None, "summary": None, "applications": []}
    s = score(conn, feats)
    ranked, cutoff = allocate(s.frame.drop(columns=["top_drivers", "has_survey", "cycle_id"]),
                              max(budget["remaining"], 0.0), rng or np.random.default_rng())
    ranked = ranked.merge(s.frame[["application_id", "top_drivers", "has_survey"]], on="application_id")
    return {
        "budget": budget,
        "model_version": s.version,
        "cutoff": cutoff if math.isfinite(cutoff) else None,
        "summary": budget_summary(ranked, max(budget["remaining"], 0.0)),
        "applications": ranked,
    }


def commit_allocation(conn: Connection, cycle_id: int, plan: dict) -> None:
    """Write a plan: model_scores rows, new statuses, and awards for the
    auto- and audit-approved (their cost is committed at allocation)."""
    ranked: pd.DataFrame = plan["applications"]
    for r in ranked.itertuples():
        conn.execute(text("""
            INSERT INTO model_scores (application_id, cycle_id, model_version, need_lo, need_mid, need_hi,
                                      cutoff, band, top_shap_features)
            VALUES (:a, :c, :v, :lo, :mid, :hi, :cut, :band, CAST(:drivers AS jsonb))"""), {
            "a": int(r.application_id), "c": cycle_id, "v": plan["model_version"],
            "lo": float(r.need_lo), "mid": float(r.need_mid), "hi": float(r.need_hi),
            "cut": plan["cutoff"], "band": r.band, "drivers": json.dumps(r.top_drivers),
        })
        conn.execute(text("UPDATE applications SET status = :s WHERE application_id = :a"),
                     {"s": STATUS_FOR_BAND[r.band], "a": int(r.application_id)})
        if r.band in ("auto_approve", "audit_approve"):
            create_award(conn, int(r.application_id))


def create_award(conn: Connection, application_id: int) -> None:
    """Award the amount requested — the platform ranks and bands, it does not
    size awards (design spec: award sizing is out of scope)."""
    conn.execute(text("""
        INSERT INTO awards (application_id, household_id, award_amount, award_date, need_category)
        SELECT application_id, household_id, amount_requested, current_date, need_category
        FROM applications WHERE application_id = :a"""), {"a": application_id})


def plan_to_json(plan: dict) -> dict:
    ranked = plan["applications"]
    apps = [] if isinstance(ranked, list) else [
        {"application_id": int(r.application_id), "household_id": str(r.household_id), "band": r.band,
         "need_lo": float(r.need_lo), "need_mid": float(r.need_mid), "need_hi": float(r.need_hi),
         "amount_requested": float(r.amount_requested), "has_survey": bool(r.has_survey),
         "top_drivers": [{"feature": f, "contribution": v} for f, v in r.top_drivers]}
        for r in ranked.itertuples()
    ]
    return {"budget": plan["budget"], "model_version": plan["model_version"], "cutoff": plan["cutoff"],
            "summary": plan["summary"], "applications": apps}
