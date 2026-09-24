"""Model management. Training stays on the command line
(`python -m backend.ml train`, which takes a while and needs the whole
dataset); the API lists versions and switches the active one."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..auth import User, require_admin
from ..platform import get_setting, log
from ...ml import registry
from ...ml.baselines import RuleBasedModel
from ...ml.features import build_features
from ..database import get_conn, get_database
from ..scoring import models as model_cache
from ..schemas import ActivateRequest

router = APIRouter(prefix="/models", tags=["models"], dependencies=[Depends(require_admin)])

SUMMARY = """model_version, purpose, kind, status, artifact_path, trained_at, activated_at, training_rows, notes,
             (metrics->'gates'->>'passed')::boolean AS gates_passed"""


@router.get("")
def list_versions(conn: Connection = Depends(get_conn)):
    return conn.execute(text(f"SELECT {SUMMARY} FROM model_versions ORDER BY created_at")).mappings().all()


@router.get("/active")
def active_version(purpose: Literal["need", "repeat"] = "need", conn: Connection = Depends(get_conn)):
    row = conn.execute(text(f"SELECT {SUMMARY} FROM model_versions WHERE status = 'active' AND purpose = :p"),
                       {"p": purpose}).mappings().first()
    if row is None:
        raise HTTPException(404, f"No active {purpose} model")
    return row


# ---------------------------------------------------------------------------
# Model cards and the what-if simulator (the showcase)
# ---------------------------------------------------------------------------

CARD_METADATA = ("kind", "purpose", "params", "interval", "conformal", "poverty_line", "poverty_line_percentile",
                 "eligible_statuses", "training_rows", "data_hash", "git_commit", "trained_at", "seed",
                 "horizon_days", "monotone_constraints")


@router.get("/{version}/card")
def model_card(version: str, conn: Connection = Depends(get_conn)):
    """Everything worth showing about one version: what it is, what it was
    trained on and with which settings, how it was judged (metrics, gates,
    baselines, fairness, calibration) and what drives it (feature importance)."""
    row = conn.execute(text("SELECT * FROM model_versions WHERE model_version = :v"), {"v": version}).mappings().first()
    if row is None:
        raise HTTPException(404, f"No model version {version!r}")
    card = {"version": dict(row), "metadata": {}, "features": None, "importance": [], "terms": None}
    if row["kind"] == RuleBasedModel.kind:
        card["terms"] = [{"feature": k, "weight": v} for k, v in RuleBasedModel.TERMS.items()]
        return card
    try:
        meta = registry.read_metadata(row["artifact_path"])
    except (OSError, ValueError, TypeError):
        card["metadata_missing"] = True
        return card
    card["metadata"] = {k: meta[k] for k in CARD_METADATA if k in meta}
    card["features"] = meta.get("feature_names") or meta.get("features")
    if "reference" in meta:           # need model: mean |SHAP| over the applicant population
        shap = meta["reference"].get("mean_abs_shap", {})
        card["importance"] = [{"feature": k, "value": v} for k, v in sorted(shap.items(), key=lambda kv: -kv[1])[:15]]
    elif meta.get("kind") == "repeat_history":
        card["importance"] = [{"feature": f, "value": c} for f, c in zip(meta["features"], meta["coef"])]
    elif meta.get("kind") == "repeat_lgbm":
        model = registry.load(row["kind"], row["artifact_path"])
        gain = model.booster.feature_importance(importance_type="gain")
        names = model.booster.feature_name()
        top = sorted(zip(names, gain), key=lambda kv: -kv[1])[:15]
        total = float(sum(gain)) or 1.0
        card["importance"] = [{"feature": k, "value": float(v) / total} for k, v in top]
    return card


class WhatIf(BaseModel):
    """An imagined household and application, for showing how the model
    reasons. Nothing is saved."""
    model_config = ConfigDict(extra="forbid")
    version: str | None = Field(None, description="Default: the active need model")
    area_code: str
    need_category: str
    amount_requested: float = Field(gt=0)
    household_size: int = Field(gt=0)
    children_under_5: int = Field(0, ge=0)
    members_over_65: int = Field(0, ge=0)
    monthly_income: float = Field(ge=0)
    essential_costs: float = Field(ge=0)
    income_std_12m: float | None = Field(None, ge=0)
    food_security_score: int | None = Field(None, ge=0, le=8)
    rooms: int | None = Field(None, ge=0)
    employment_type: str | None = None
    female_headed: bool = False
    disability_in_household: bool = False
    chronic_illness: bool = False
    shocks: list[str] = Field(default_factory=list, description="e.g. ['job_loss', 'bereavement'] (bereavement = a death in the family)")
    assets: list[str] = Field(default_factory=list, description="e.g. ['phone', 'radio']")
    prior_applications_count: int = Field(0, ge=0)
    days_since_last_application: int | None = Field(None, ge=0)


@router.post("/what-if")
def what_if(body: WhatIf, conn: Connection = Depends(get_conn)):
    """Score an imagined household with a need model (default the active one)
    and say where it would have landed in the latest allocated cycle."""
    if body.version:
        row = conn.execute(text("SELECT * FROM model_versions WHERE model_version = :v"), {"v": body.version}).mappings().first()
        if row is None or row["purpose"] != "need":
            raise HTTPException(404, f"No need model {body.version!r}")
        model = registry.load(row["kind"], row["artifact_path"])
        row = dict(row)
    else:
        row, model = model_cache.active(conn)
    if conn.execute(text("SELECT 1 FROM area_reference WHERE area_code = :a"), {"a": body.area_code}).first() is None:
        raise HTTPException(422, f"Unknown area_code {body.area_code!r}")

    now = datetime.now(timezone.utc)
    survey = {c: None for c in get_database().table("household_surveys").columns.keys()}
    survey.update(survey_id=0, household_id="what-if", survey_date=date.today(), created_at=now,
                  household_size=body.household_size, children_under_5=body.children_under_5,
                  members_over_65=body.members_over_65, monthly_income=body.monthly_income,
                  essential_costs=body.essential_costs, income_std_12m=body.income_std_12m,
                  food_security_score=body.food_security_score, rooms=body.rooms, employment_type=body.employment_type,
                  female_headed=body.female_headed, disability_in_household=body.disability_in_household,
                  chronic_illness=body.chronic_illness, dependents_requiring_care=0)
    for k in survey:
        if k.startswith("shock_") and k.endswith("_12m"):
            survey[k] = k[len("shock_"):-len("_12m")] in body.shocks
        elif k.startswith("asset_"):
            survey[k] = k[len("asset_"):] in body.assets
    application = {
        "application_id": 0, "household_id": "what-if", "cycle_id": None, "caseworker_id": None,
        "submitted_at": now, "amount_requested": body.amount_requested, "need_category": body.need_category,
        "stated_need_amount": body.amount_requested, "days_since_hardship_onset": None,
        "prior_applications_count": body.prior_applications_count, "days_since_last_application": body.days_since_last_application,
        "referral_source": "self", "application_channel": "in_person", "application_completeness": 0.6,
        "documentation_provided": True, "status": "submitted", "created_at": now,
    }
    area = pd.read_sql(text("SELECT * FROM area_reference"), conn)
    feats = build_features(pd.DataFrame([{"household_id": "what-if", "area_code": body.area_code}]),
                           pd.DataFrame([survey]), pd.DataFrame([application]), area)
    pred = model.predict(feats).iloc[0]
    drivers = model.explain(feats, top_n=6)[0]

    # Where it would have landed: the latest cycle this model allocated.
    latest = conn.execute(text("""
        SELECT cycle_id, cutoff FROM model_scores WHERE model_version = :v AND cutoff IS NOT NULL
        ORDER BY scored_at DESC, cycle_id DESC LIMIT 1"""), {"v": row["model_version"]}).mappings().first()
    context = None
    if latest:
        mids = np.array(conn.execute(text("""
            SELECT DISTINCT ON (application_id) need_mid FROM model_scores
            WHERE model_version = :v AND cycle_id = :c ORDER BY application_id, scored_at DESC"""),
            {"v": row["model_version"], "c": latest["cycle_id"]}).scalars().all(), dtype=float)
        cutoff = float(latest["cutoff"])
        band = "auto_approve" if pred["need_lo"] > cutoff else "defer" if pred["need_hi"] < cutoff else "human_review"
        period = conn.execute(text("SELECT period_start FROM funding_cycles WHERE cycle_id = :c"), {"c": latest["cycle_id"]}).scalar()
        context = {"cycle_id": latest["cycle_id"], "period_start": period, "cutoff": cutoff, "likely_band": band,
                   "applicants": int(len(mids)), "needier_than_share": float((mids < pred["need_mid"]).mean()) if len(mids) else None}
    return {"model_version": row["model_version"], "kind": row["kind"],
            "need_lo": float(pred["need_lo"]), "need_mid": float(pred["need_mid"]), "need_hi": float(pred["need_hi"]),
            "drivers": [{"feature": f, "contribution": v} for f, v in drivers], "context": context}


@router.get("/{version}")
def get_version(version: str, conn: Connection = Depends(get_conn)):
    """One version with its full evaluation report (metrics + gates)."""
    row = conn.execute(text("SELECT * FROM model_versions WHERE model_version = :v"), {"v": version}).mappings().first()
    if row is None:
        raise HTTPException(404, f"No model version {version!r}")
    return row


@router.post("/{version}/activate")
def activate(version: str, body: ActivateRequest | None = None, admin: User = Depends(require_admin),
             conn: Connection = Depends(get_conn)):
    """Make `version` the live model for its purpose (need or repeat); the
    previous one for that purpose is retired.
    Refused if the version's evaluation gates failed, unless `force` is set
    with a `reason` — which is recorded on the version. Applications already
    allocated keep their scores; new ones use this version."""
    body = body or ActivateRequest()
    row = conn.execute(text("SELECT * FROM model_versions WHERE model_version = :v FOR UPDATE"),
                       {"v": version}).mappings().first()
    if row is None:
        raise HTTPException(404, f"No model version {version!r}")
    gates = dict((row["metrics"] or {}).get("gates", {}))
    if row["purpose"] == "need" and row["kind"] != "rules":
        gates.update(launch_thresholds(conn, row["metrics"] or {}))
        gates["passed"] = all(v for k, v in gates.items() if isinstance(v, bool) and k != "passed")
    if row["kind"] != "rules" and not gates.get("passed"):
        if not body.force:
            failed = [k for k, v in gates.items() if v is False]
            raise HTTPException(409, f"Evaluation gates failed: {failed}. Send force=true with a reason to override.")
        if not body.reason:
            raise HTTPException(422, "force=true needs a reason; it is recorded on the model version")

    prev = conn.execute(text("""UPDATE model_versions SET status = 'retired'
                                WHERE status = 'active' AND purpose = :p AND model_version <> :v
                                RETURNING model_version"""), {"v": version, "p": row["purpose"]}).scalar()
    note = f"Activated despite failed gates: {body.reason}" if body.force and body.reason else None
    conn.execute(text("""UPDATE model_versions SET status = 'active', activated_at = now(),
                                notes = CASE WHEN CAST(:note AS text) IS NULL THEN notes
                                             ELSE concat_ws(E'\\n', notes, CAST(:note AS text)) END
                         WHERE model_version = :v"""), {"v": version, "note": note})
    log(conn, admin.username, "model.activate", version,
        {"retired": prev, "purpose": row["purpose"], "forced": bool(body.force), "reason": body.reason})
    return {"active": version, "retired": prev}


def launch_thresholds(conn: Connection, metrics: dict) -> dict:
    """The admin-set launch thresholds (platform settings) as extra gates.
    Unset thresholds add nothing — the spec leaves them to be agreed."""
    out = {}
    max_excl = get_setting(conn, "max_exclusion_error")
    if max_excl is not None:
        excl = ((metrics.get("models") or {}).get("lgbm") or {}).get("exclusion_error_bottom_decile")
        out["within_max_exclusion_error"] = excl is not None and excl <= float(max_excl)
    max_gap = get_setting(conn, "max_subgroup_gap")
    if max_gap is not None:
        gap = (metrics.get("gates") or {}).get("max_subgroup_gap")
        out["within_max_subgroup_gap"] = gap is not None and gap <= float(max_gap)
    return out
