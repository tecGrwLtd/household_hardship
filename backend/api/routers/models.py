"""Model management. Training stays on the command line
(`python -m backend.ml train`, which takes a while and needs the whole
dataset); the API lists versions and switches the active one."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..auth import require_admin
from ..database import get_conn
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


@router.get("/{version}")
def get_version(version: str, conn: Connection = Depends(get_conn)):
    """One version with its full evaluation report (metrics + gates)."""
    row = conn.execute(text("SELECT * FROM model_versions WHERE model_version = :v"), {"v": version}).mappings().first()
    if row is None:
        raise HTTPException(404, f"No model version {version!r}")
    return row


@router.post("/{version}/activate")
def activate(version: str, body: ActivateRequest | None = None, conn: Connection = Depends(get_conn)):
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
    gates = (row["metrics"] or {}).get("gates", {})
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
    return {"active": version, "retired": prev}
