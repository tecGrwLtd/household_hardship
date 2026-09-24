"""The admin console's back office: accounts, caseworkers, platform
settings, the activity log and system status. Admin only."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..auth import User, require_admin
from ..config import DEV_SECRET, settings
from ..database import get_conn
from ..platform import SETTINGS, log, set_setting

router = APIRouter(prefix="/admin", tags=["admin console"], dependencies=[Depends(require_admin)])

USER_COLUMNS = "u.user_id, u.username, u.display_name, u.role::text AS role, u.caseworker_id, u.active, u.created_at, c.display_name AS caseworker"


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-z0-9._-]+$")
    display_name: str = Field(min_length=1)
    password: str = Field(min_length=10)
    role: Literal["admin", "caseworker"]
    caseworker_id: int | None = None


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    role: Literal["admin", "caseworker"] | None = None
    caseworker_id: int | None = None
    active: bool | None = None
    password: str | None = Field(None, min_length=10)


class CaseworkerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    region: str | None = None
    active: bool | None = None


class SettingIn(BaseModel):
    value: Any = None


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

@router.get("/users")
def list_users(conn: Connection = Depends(get_conn)):
    return conn.execute(text(f"""SELECT {USER_COLUMNS} FROM app_users u
                                 LEFT JOIN caseworkers c ON c.caseworker_id = u.caseworker_id
                                 ORDER BY u.active DESC, u.role, u.display_name""")).mappings().all()


@router.post("/users", status_code=201)
def create_user(body: UserCreate, admin: User = Depends(require_admin), conn: Connection = Depends(get_conn)):
    if body.role == "caseworker" and body.caseworker_id is None:
        raise HTTPException(422, "A caseworker account must be linked to a caseworker")
    if conn.execute(text("SELECT 1 FROM app_users WHERE username = :u"), {"u": body.username}).first():
        raise HTTPException(409, f"Username {body.username!r} is taken")
    row = conn.execute(text("""
        INSERT INTO app_users (username, display_name, password_hash, role, caseworker_id)
        VALUES (:u, :n, crypt(:p, gen_salt('bf', 10)), :r, :c) RETURNING user_id"""),
        {"u": body.username, "n": body.display_name, "p": body.password, "r": body.role, "c": body.caseworker_id}).one()
    log(conn, admin.username, "user.create", body.username, {"role": body.role, "caseworker_id": body.caseworker_id})
    return {"user_id": row[0], "username": body.username}


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(require_admin), conn: Connection = Depends(get_conn)):
    current = conn.execute(text("SELECT * FROM app_users WHERE user_id = :i"), {"i": user_id}).mappings().first()
    if current is None:
        raise HTTPException(404, f"No user {user_id}")
    changes = body.model_dump(exclude_unset=True)
    removing_admin = current["role"] == "admin" and (changes.get("active") is False or changes.get("role") == "caseworker")
    if removing_admin:
        if user_id == admin.user_id:
            raise HTTPException(409, "You cannot deactivate or demote your own account")
        others = conn.execute(text("SELECT count(*) FROM app_users WHERE role = 'admin' AND active AND user_id <> :i"),
                              {"i": user_id}).scalar()
        if not others:
            raise HTTPException(409, "This is the last active admin")
    role = changes.get("role", current["role"])
    caseworker_id = changes.get("caseworker_id", current["caseworker_id"])
    if role == "caseworker" and caseworker_id is None:
        raise HTTPException(422, "A caseworker account must be linked to a caseworker")
    sets, params = [], {"i": user_id}
    for field in ("display_name", "role", "caseworker_id", "active"):
        if field in changes:
            sets.append(f"{field} = :{field}")
            params[field] = changes[field]
    if changes.get("password"):
        sets.append("password_hash = crypt(:password, gen_salt('bf', 10))")
        params["password"] = changes["password"]
    if sets:
        conn.execute(text(f"UPDATE app_users SET {', '.join(sets)} WHERE user_id = :i"), params)
        details = {k: v for k, v in changes.items() if k != "password"}
        if "password" in changes:
            details["password"] = "reset"
        log(conn, admin.username, "user.update", current["username"], details)
    return conn.execute(text(f"""SELECT {USER_COLUMNS} FROM app_users u LEFT JOIN caseworkers c USING (caseworker_id)
                                 WHERE u.user_id = :i"""), {"i": user_id}).mappings().one()


# ---------------------------------------------------------------------------
# Caseworkers (people who handle applications)
# ---------------------------------------------------------------------------

@router.get("/caseworkers")
def caseworkers(conn: Connection = Depends(get_conn)):
    """Caseworkers with their workload and review record."""
    return conn.execute(text("""
        SELECT c.caseworker_id, c.display_name, c.region, c.active,
               (SELECT count(*) FROM applications a WHERE a.caseworker_id = c.caseworker_id) AS applications,
               (SELECT count(*) FROM applications a WHERE a.caseworker_id = c.caseworker_id
                    AND a.status IN ('in_review', 'appealed')) AS open_cases,
               (SELECT count(*) FROM application_reviews r WHERE r.caseworker_id = c.caseworker_id) AS reviews,
               (SELECT avg(overridden::int) FROM application_reviews r WHERE r.caseworker_id = c.caseworker_id) AS override_rate,
               (SELECT string_agg(username, ', ') FROM app_users u WHERE u.caseworker_id = c.caseworker_id) AS accounts
        FROM caseworkers c ORDER BY c.active DESC, c.display_name""")).mappings().all()


@router.post("/caseworkers", status_code=201)
def create_caseworker(body: CaseworkerIn, admin: User = Depends(require_admin), conn: Connection = Depends(get_conn)):
    if not body.display_name:
        raise HTTPException(422, "display_name is required")
    row = conn.execute(text("""INSERT INTO caseworkers (display_name, region, active)
                               VALUES (:n, :r, coalesce(:a, true)) RETURNING *"""),
                       {"n": body.display_name, "r": body.region, "a": body.active}).mappings().one()
    log(conn, admin.username, "caseworker.create", body.display_name, {"region": body.region})
    return row


@router.patch("/caseworkers/{caseworker_id}")
def update_caseworker(caseworker_id: int, body: CaseworkerIn, admin: User = Depends(require_admin), conn: Connection = Depends(get_conn)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "Nothing to change")
    row = conn.execute(text(f"""UPDATE caseworkers SET {', '.join(f'{k} = :{k}' for k in changes)}
                               WHERE caseworker_id = :i RETURNING *"""), {**changes, "i": caseworker_id}).mappings().first()
    if row is None:
        raise HTTPException(404, f"No caseworker {caseworker_id}")
    log(conn, admin.username, "caseworker.update", row["display_name"], changes)
    return row


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@router.get("/settings")
def list_settings(conn: Connection = Depends(get_conn)):
    rows = conn.execute(text("SELECT * FROM platform_settings ORDER BY key")).mappings().all()
    return [r for r in rows if r["key"] in SETTINGS]


@router.put("/settings/{key}")
def update_setting(key: str, body: SettingIn, admin: User = Depends(require_admin), conn: Connection = Depends(get_conn)):
    return set_setting(conn, key, body.value, admin.username)


# ---------------------------------------------------------------------------
# Activity log and system status
# ---------------------------------------------------------------------------

@router.get("/audit-log")
def audit_log(action: str | None = Query(None, description="Prefix, e.g. 'user.' or 'model.activate'"),
              limit: int = Query(50, le=500), offset: int = 0, conn: Connection = Depends(get_conn)):
    where, params = "true", {"limit": limit, "offset": offset}
    if action:
        where, params["a"] = "action LIKE :a", f"{action}%"
    total = conn.execute(text(f"SELECT count(*) FROM audit_log WHERE {where}"), params).scalar()
    items = conn.execute(text(f"SELECT * FROM audit_log WHERE {where} ORDER BY at DESC, log_id DESC LIMIT :limit OFFSET :offset"),
                         params).mappings().all()
    return {"total": total, "items": items}


@router.get("/system")
def system(conn: Connection = Depends(get_conn)):
    """What is running and how much is in it — for the console's overview."""
    counts = conn.execute(text("""
        SELECT (SELECT count(*) FROM households) AS households,
               (SELECT count(*) FROM household_surveys) AS surveys,
               (SELECT count(*) FROM applications) AS applications,
               (SELECT count(*) FROM awards) AS awards,
               (SELECT count(*) FROM application_reviews) AS reviews,
               (SELECT count(*) FROM funding_cycles) AS funding_cycles,
               (SELECT count(*) FROM model_versions) AS model_versions,
               (SELECT count(*) FROM app_users WHERE active) AS active_users,
               (SELECT count(*) FROM caseworkers WHERE active) AS active_caseworkers,
               (SELECT count(*) FROM audit_log) AS audit_entries""")).mappings().one()
    active = conn.execute(text("SELECT purpose, model_version, kind, activated_at FROM model_versions WHERE status = 'active'")).mappings().all()
    db = conn.execute(text("SELECT version() AS version, pg_size_pretty(pg_database_size(current_database())) AS size")).mappings().one()
    last = conn.execute(text("""SELECT action, max(at) AS at FROM audit_log
                                WHERE action IN ('cycle.allocate', 'model.activate', 'setting.update') GROUP BY action""")).mappings().all()
    drift = conn.execute(text("SELECT status, window_end, created_at FROM drift_reports ORDER BY created_at DESC LIMIT 1")).mappings().first()
    return {
        "api_version": "0.3.0",
        "database": {"version": db["version"].split(",")[0], "size": db["size"]},
        "counts": counts,
        "active_models": active,
        "last": {r["action"]: r["at"] for r in last},
        "last_drift_check": drift,
        "security": {
            "development_secret": settings.secret == DEV_SECRET,
            "default_admin_password": settings.admin_password == "admin-dev-only",
            "token_hours": settings.token_hours,
        },
    }
