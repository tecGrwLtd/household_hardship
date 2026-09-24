"""The sidebar's global filters, as one FastAPI dependency. Every filtered
endpoint turns them into the same WHERE clause on v_application_facts
(db/views.sql), so a number means the same thing on every page."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from fastapi import Depends, Query

from .auth import User, current_user

SupportGroup = Literal["financial", "health", "education", "bereavement"]


@dataclass(frozen=True)
class Filters:
    month: date | None = None            # first day of the month
    support_group: str | None = None
    region: str | None = None
    area_code: str | None = None
    urban_rural: str | None = None
    caseworker_id: int | None = None     # set by "mine"

    def where(self, alias: str = "f", month: bool = True) -> tuple[str, dict]:
        """SQL condition + params. month=False drops the month (for trends
        that span several months but keep every other filter)."""
        parts, params = ["true"], {}
        for field in ("support_group", "region", "area_code", "urban_rural", "caseworker_id"):
            value = getattr(self, field)
            if value is not None:
                parts.append(f"{alias}.{field} = :f_{field}")
                params[f"f_{field}"] = value
        if month and self.month is not None:
            parts.append(f"{alias}.month = :f_month")
            params["f_month"] = self.month
        return " AND ".join(parts), params

    def previous_month(self) -> "Filters":
        m = self.month
        return replace(self, month=date(m.year - 1, 12, 1) if m.month == 1 else date(m.year, m.month - 1, 1))

    @property
    def narrows_population(self) -> bool:
        """True when only part of the programme is shown — then a cycle's
        whole budget is not the right denominator."""
        return any(v is not None for v in (self.support_group, self.region, self.area_code,
                                           self.urban_rural, self.caseworker_id))


def filters(
    month: date | None = Query(None, description="Any day in the month, e.g. 2026-08-01"),
    support_group: SupportGroup | None = None,
    region: str | None = None,
    area_code: str | None = Query(None, description="District code, e.g. AR007"),
    urban_rural: Literal["urban", "rural"] | None = None,
    mine: bool = Query(False, description="Only the signed-in caseworker's applications"),
    user: User = Depends(current_user),
) -> Filters:
    return Filters(
        month=month.replace(day=1) if month else None,
        support_group=support_group, region=region, area_code=area_code, urban_rural=urban_rural,
        caseworker_id=user.caseworker_id if mine else None,
    )
