"""Request bodies. Enum values mirror the Postgres enums in db/schema.sql.
Responses are the database rows themselves (see routers), so the API never
invents fields the schema does not have."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NeedCategory = Literal["rent_arrears", "medical", "utilities", "food", "funeral", "childcare", "education", "other"]
Channel = Literal["online", "phone", "in_person", "caseworker_submitted"]
Tenure = Literal["owned", "mortgaged", "private_rent", "social", "informal", "temporary"]
Employment = Literal["formal", "informal", "self_employed", "unemployed", "unable_to_work"]
Education = Literal["none", "primary", "secondary", "vocational", "tertiary"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HouseholdCreate(Strict):
    area_code: str
    notes: str | None = None


class SurveyCreate(Strict):
    """One survey wave. Every application is scored on the latest survey
    taken on or before its submission date, so record a new wave when a
    household's circumstances change rather than editing the old one."""
    survey_date: date = Field(default_factory=date.today)
    household_size: int = Field(gt=0)
    children_under_5: int = Field(0, ge=0)
    members_over_65: int = Field(0, ge=0)
    female_headed: bool | None = None
    single_caregiver: bool | None = None
    education_head: Education | None = None
    literacy_head: bool | None = None
    roof_material: str | None = None
    wall_material: str | None = None
    floor_material: str | None = None
    rooms: int | None = Field(None, ge=0)
    tenure: Tenure | None = None
    water_source: str | None = None
    sanitation_type: str | None = None
    electricity: bool | None = None
    cooking_fuel: str | None = None
    asset_phone: bool = False
    asset_radio: bool = False
    asset_tv: bool = False
    asset_fridge: bool = False
    asset_washing_machine: bool = False
    asset_bicycle: bool = False
    asset_motorcycle: bool = False
    asset_car: bool = False
    livestock_count: int | None = Field(None, ge=0)
    land_area: float | None = Field(None, ge=0)
    employment_type: Employment | None = None
    earners_count: int | None = Field(None, ge=0)
    hours_worked: float | None = Field(None, ge=0)
    monthly_income: float | None = Field(None, ge=0)
    income_std_12m: float | None = Field(None, ge=0)
    income_seasonality: float | None = Field(None, ge=0)
    essential_costs: float | None = Field(None, ge=0)
    food_security_score: int | None = Field(None, ge=0, description="FIES/HFIAS as published")
    chronic_illness: bool | None = None
    disability_in_household: bool | None = None
    dependents_requiring_care: int = Field(0, ge=0)
    shock_bereavement_12m: bool = False
    shock_serious_illness_12m: bool = False
    shock_job_loss_12m: bool = False
    shock_eviction_12m: bool = False
    shock_displacement_12m: bool = False
    shock_disaster_12m: bool = False
    shock_crop_failure_12m: bool = False
    consumption_pc: float | None = Field(
        None, ge=0, description="Outcome measure. Record only for approved or audit-sample households "
                                "(the selective-labels design); it is the training target, never a model input.")


class ProtectedAttributes(Strict):
    """AUDIT ONLY. Stored apart from everything the model reads and only ever
    reported in aggregate (fairness endpoints)."""
    ethnicity: str | None = None
    gender_head: str | None = None
    disability: str | None = None
    age_band: str | None = None
    religion: str | None = None
    nationality: str | None = None
    immigration_status: str | None = None


class ApplicationCreate(Strict):
    """prior_applications_count and days_since_last_application are derived
    from the household's history on the server, never taken from the client."""
    household_id: UUID
    cycle_id: int
    caseworker_id: int | None = None
    amount_requested: float = Field(gt=0)
    need_category: NeedCategory
    stated_need_amount: float | None = Field(None, ge=0)
    days_since_hardship_onset: int | None = Field(None, ge=0)
    referral_source: str | None = None
    application_channel: Channel | None = None
    application_completeness: float | None = Field(None, ge=0, le=1)
    documentation_provided: bool | None = None
    submitted_at: datetime | None = None


class CycleCreate(Strict):
    period_start: date
    period_end: date
    budget_total: float = Field(gt=0)
    budget_currency: str = "RWF"
    notes: str | None = None


class ReviewCreate(Strict):
    """A caseworker's decision on an application in the review queue
    (human_review band, or an appealed deferral)."""
    decision: Literal["approve", "deny"]
    caseworker_id: int | None = Field(None, description="Defaults to the application's assigned caseworker")
    notes: str | None = None


class ActivateRequest(Strict):
    force: bool = False
    reason: str | None = Field(None, description="Required with force: why the gates are being overridden")
