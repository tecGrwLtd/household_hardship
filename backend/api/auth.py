"""Single demo admin (a roadmap decision until real accounts exist):
POST /auth/login with the configured username/password returns a signed
bearer token; every other endpoint except /health requires it."""
from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel

from .config import settings

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")
ALGORITHM = "HS256"


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


@router.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends()) -> Token:
    ok_user = hmac.compare_digest(form.username, settings.admin_user)
    ok_pass = hmac.compare_digest(form.password, settings.admin_password)
    if not (ok_user and ok_pass):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password",
                            headers={"WWW-Authenticate": "Bearer"})
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.token_hours)
    token = jwt.encode({"sub": form.username, "role": "admin", "exp": expires}, settings.secret, ALGORITHM)
    return Token(access_token=token, expires_at=expires)


def current_user(token: str = Depends(oauth2)) -> str:
    try:
        return jwt.decode(token, settings.secret, algorithms=[ALGORITHM])["sub"]
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
