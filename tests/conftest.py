"""Tiny hand-built frames shaped like the DB tables, so the ML modules can be
tested without Postgres."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

ASSETS = ["asset_phone", "asset_radio", "asset_tv", "asset_fridge"]
SHOCKS = ["shock_job_loss_12m", "shock_eviction_12m"]


def make_survey(household_id: str, survey_date: str, **overrides) -> dict:
    row = {
        "survey_id": None, "household_id": household_id, "survey_date": pd.Timestamp(survey_date).date(),
        "household_size": 4, "children_under_5": 1, "members_over_65": 0, "rooms": 2,
        "monthly_income": 40_000.0, "income_std_12m": 8_000.0, "essential_costs": 60_000.0,
        "female_headed": False, "consumption_pc": 9_000.0, "created_at": pd.Timestamp("2026-01-01", tz="UTC"),
        **{a: False for a in ASSETS}, **{s: False for s in SHOCKS},
    }
    row.update(overrides)
    return row


def make_application(application_id: int, household_id: str, submitted_at: str, **overrides) -> dict:
    row = {
        "application_id": application_id, "household_id": household_id, "cycle_id": 1,
        "caseworker_id": 1, "submitted_at": pd.Timestamp(submitted_at, tz="UTC"),
        "amount_requested": 20_000.0, "need_category": "food", "status": "submitted",
        "created_at": pd.Timestamp("2026-01-01", tz="UTC"),
    }
    row.update(overrides)
    return row


@pytest.fixture
def area() -> pd.DataFrame:
    return pd.DataFrame([
        {"area_code": "AR001", "area_name": "Nyarugenge", "urban_rural": "urban", "region": "Kigali",
         "area_deprivation_index": 0.3},
        {"area_code": "AR002", "area_name": "Burera", "urban_rural": "rural", "region": "Northern",
         "area_deprivation_index": 0.7},
    ])


@pytest.fixture
def households() -> pd.DataFrame:
    return pd.DataFrame([
        {"household_id": "h1", "area_code": "AR001"},
        {"household_id": "h2", "area_code": "AR002"},
    ])


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(0)
