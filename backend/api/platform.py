"""Platform settings (changed from the admin console) and the activity log.

Each setting is read where it takes effect: the random audit rate by
allocation, the launch thresholds by model activation, the override floor by
monitoring, the poverty line by the next training run. Every change an admin
makes — and every allocation, activation and review decision — is written to
audit_log with who did it.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..ml.model import RANDOM_AUDIT_RATE

# key -> (validator returning an error message or None, default)
def _fraction(lo: float, hi: float, nullable: bool = False):
    def check(v: Any) -> str | None:
        if v is None:
            return None if nullable else "a value is required"
        if not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
            return f"must be a number between {lo} and {hi}"
        return None
    return check


def _positive_or_null(v: Any) -> str | None:
    if v is None:
        return None
    return None if isinstance(v, (int, float)) and v > 0 else "must be a positive number, or empty"


SETTINGS: dict[str, tuple] = {
    # The spec: "Approve a random 3-5% of below-cutoff applicants every cycle."
    "random_audit_rate": (_fraction(0.03, 0.05), RANDOM_AUDIT_RATE),
    "override_rate_floor": (_fraction(0.0, 0.5), 0.05),
    "max_exclusion_error": (_fraction(0.0, 1.0, nullable=True), None),
    "max_subgroup_gap": (_fraction(0.0, 1.0, nullable=True), None),
    "poverty_line": (_positive_or_null, None),
}


def get_setting(conn: Connection, key: str) -> Any:
    row = conn.execute(text("SELECT value FROM platform_settings WHERE key = :k"), {"k": key}).first()
    return SETTINGS[key][1] if row is None else row[0]


def set_setting(conn: Connection, key: str, value: Any, username: str) -> dict:
    if key not in SETTINGS:
        raise HTTPException(404, f"No setting {key!r}")
    error = SETTINGS[key][0](value)
    if error:
        raise HTTPException(422, f"{key} {error}")
    old = get_setting(conn, key)
    row = conn.execute(text("""
        UPDATE platform_settings SET value = CAST(:v AS jsonb), updated_by = :u, updated_at = now()
        WHERE key = :k RETURNING key, value, description, updated_by, updated_at"""),
        {"k": key, "v": json.dumps(value), "u": username}).mappings().one()
    log(conn, username, "setting.update", key, {"from": old, "to": value})
    return dict(row)


def log(conn: Connection, username: str | None, action: str, target: str | None = None, details: dict | None = None) -> None:
    conn.execute(text("INSERT INTO audit_log (username, action, target, details) VALUES (:u, :a, :t, CAST(:d AS jsonb))"),
                 {"u": username, "a": action, "t": target, "d": json.dumps(details or {}, default=str)})
