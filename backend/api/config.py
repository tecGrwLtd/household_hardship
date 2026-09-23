"""Settings, all from the environment so the same code runs on a laptop and
in the container. Defaults are for local development only."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

DEV_SECRET = "dev-only-secret-change-me-before-any-real-deployment"


@dataclass(frozen=True)
class Settings:
    dsn: str = field(default_factory=lambda: os.environ.get(
        "HARDSHIP_DSN", "postgresql://hardship_app:hardship_dev_only@localhost:5432/hardship_platform"))
    # Single demo admin until the frontend and real accounts exist (roadmap decision).
    admin_user: str = field(default_factory=lambda: os.environ.get("HARDSHIP_ADMIN_USER", "admin"))
    admin_password: str = field(default_factory=lambda: os.environ.get("HARDSHIP_ADMIN_PASSWORD", "admin-dev-only"))
    secret: str = field(default_factory=lambda: os.environ.get("HARDSHIP_SECRET", DEV_SECRET))
    token_hours: int = field(default_factory=lambda: int(os.environ.get("HARDSHIP_TOKEN_HOURS", "12")))
    cors_origins: tuple[str, ...] = field(default_factory=lambda: tuple(
        o.strip() for o in os.environ.get("HARDSHIP_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
        if o.strip()))


settings = Settings()
