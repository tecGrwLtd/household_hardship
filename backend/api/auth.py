"""Accounts and roles.

Two roles (docs/ROADMAP.md): `admin` — the programme manager, everything
including funding cycles, allocation and models — and `caseworker` — data
entry and their own review queue, with decisions attributed to their
caseworkers row. POST /auth/login checks the password in Postgres (bcrypt
via pgcrypto) and returns a signed bearer token carrying the role; every
other endpoint except /health requires it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .config import settings
from .database import get_conn

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")
ALGORITHM = "HS256"


@dataclass(frozen=True)
class User:
    user_id: int
    username: str
    display_name: str
    role: str                      # 'admin' | 'caseworker'
    caseworker_id: int | None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: dict


@router.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), conn: Connection = Depends(get_conn)) -> Token:
    row = conn.execute(text("""
        SELECT user_id, username, display_name, role::text AS role, caseworker_id FROM app_users
        WHERE username = :u AND active AND password_hash = crypt(:p, password_hash)"""),
        {"u": form.username, "p": form.password}).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password",
                            headers={"WWW-Authenticate": "Bearer"})
    conn.execute(text("INSERT INTO audit_log (username, action) VALUES (:u, 'auth.login')"), {"u": row["username"]})
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.token_hours)
    claims = {"sub": row["username"], "uid": row["user_id"], "name": row["display_name"],
              "role": row["role"], "cw": row["caseworker_id"], "exp": expires}
    return Token(access_token=jwt.encode(claims, settings.secret, ALGORITHM), expires_at=expires,
                 user={k: row[k] for k in ("user_id", "username", "display_name", "role", "caseworker_id")})


def current_user(token: str = Depends(oauth2)) -> User:
    try:
        c = jwt.decode(token, settings.secret, algorithms=[ALGORITHM])
        return User(user_id=c["uid"], username=c["sub"], display_name=c["name"], role=c["role"], caseworker_id=c["cw"])
    except (jwt.PyJWTError, KeyError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Programme manager (admin) only")
    return user


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10)


@router.post("/password", status_code=204)
def change_password(body: PasswordChange, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    ok = conn.execute(text("""UPDATE app_users SET password_hash = crypt(:new, gen_salt('bf', 10))
                              WHERE user_id = :i AND password_hash = crypt(:old, password_hash) RETURNING 1"""),
                      {"i": user.user_id, "old": body.current_password, "new": body.new_password}).first()
    if ok is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Current password is wrong")
    conn.execute(text("INSERT INTO audit_log (username, action, target) VALUES (:u, 'user.password', :u)"), {"u": user.username})


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return {"user_id": user.user_id, "username": user.username, "display_name": user.display_name,
            "role": user.role, "caseworker_id": user.caseworker_id}


def bootstrap_admin(engine: Engine) -> None:
    """A fresh database has no accounts: create the admin from
    HARDSHIP_ADMIN_USER / HARDSHIP_ADMIN_PASSWORD so someone can log in.
    Does nothing once any admin exists."""
    with engine.begin() as conn:
        if conn.execute(text("SELECT 1 FROM app_users WHERE role = 'admin' LIMIT 1")).first() is None:
            conn.execute(text("""
                INSERT INTO app_users (username, display_name, password_hash, role)
                VALUES (:u, 'Programme manager', crypt(:p, gen_salt('bf', 10)), 'admin')
                ON CONFLICT (username) DO NOTHING"""), {"u": settings.admin_user, "p": settings.admin_password})
