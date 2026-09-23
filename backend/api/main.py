"""Household Hardship Platform API.

Run locally:   uvicorn backend.api.main:app --port 8080 --reload
Docs:          http://localhost:8080/docs
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from . import auth
from .config import DEV_SECRET, settings
from .database import get_database
from .routers import applications, cycles, dashboard, households, models, reference, reviews

app = FastAPI(
    title="Household Hardship Platform API",
    version="0.2.0",
    description=(
        "Data entry, cycle allocation, human review and dashboard data for the household hardship "
        "allocation model. Log in at POST /auth/login (single demo admin for now) and send the token "
        "as `Authorization: Bearer <token>`."
    ),
)
app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(auth.router)
protected = [Depends(auth.current_user)]
for r in (reference.router, households.router, applications.router, cycles.router,
          reviews.router, dashboard.router, models.router):
    app.include_router(r, dependencies=protected)


@app.get("/health", tags=["health"])
def health():
    """Unauthenticated liveness + database check, for Docker and uptime probes."""
    with get_database().engine.connect() as conn:
        active = conn.execute(text("SELECT model_version FROM model_versions WHERE status = 'active'")).scalar()
    return {"status": "ok", "active_model": active,
            "warning": "using the development secret; set HARDSHIP_SECRET" if settings.secret == DEV_SECRET else None}
