import numpy as np
import pandas as pd

from backend.ml.features import NEVER_FEATURES, add_poverty_gap, build_features, feature_matrix
from tests.conftest import make_application, make_survey


def test_each_application_gets_the_survey_in_force_when_submitted(households, area):
    surveys = pd.DataFrame([
        make_survey("h1", "2025-01-01", monthly_income=10_000.0),
        make_survey("h1", "2025-06-01", monthly_income=50_000.0),
        make_survey("h2", "2025-01-01", monthly_income=30_000.0),
    ])
    apps = pd.DataFrame([
        make_application(1, "h1", "2025-07-15"),   # after both waves -> June wave
        make_application(2, "h1", "2025-03-10"),   # between waves   -> January wave
        make_application(3, "h1", "2024-12-01"),   # before any wave -> no survey
        make_application(4, "h2", "2025-01-01T09:00:00"),  # same day as survey -> counts as prior
    ])

    df = build_features(households, surveys, apps, area)

    assert list(df["application_id"]) == [1, 2, 3, 4], "row count and order must be preserved"
    assert list(df["monthly_income"][:2]) == [50_000.0, 10_000.0]
    assert np.isnan(df["monthly_income"].iloc[2])
    assert df["monthly_income"].iloc[3] == 30_000.0


def test_derived_features(households, area):
    surveys = pd.DataFrame([make_survey("h1", "2025-01-01", shock_job_loss_12m=True, shock_eviction_12m=True)])
    apps = pd.DataFrame([make_application(1, "h1", "2025-02-01", amount_requested=25_000.0)])

    row = build_features(households, surveys, apps, area).iloc[0]

    assert row["monthly_deficit"] == 20_000.0
    assert row["deficit_ratio"] == 20_000.0 / 60_000.0
    assert row["request_closes_gap"] == 1
    assert row["crowding"] == 2.0
    assert row["shock_count_12m"] == 2
    assert row["dependency_ratio"] == 0.25


def test_poverty_gap_is_higher_for_the_worse_off_and_never_negative():
    df = pd.DataFrame({"consumption_pc": [2_000.0, 8_000.0, 20_000.0]})
    gap = add_poverty_gap(df, poverty_line=10_000.0)["poverty_gap"]
    assert list(gap) == [8_000.0, 2_000.0, 0.0]


def test_feature_matrix_keeps_ids_labels_and_audit_fields_out(households, area):
    surveys = pd.DataFrame([make_survey("h1", "2025-01-01"), make_survey("h2", "2025-01-01")])
    apps = pd.DataFrame([make_application(1, "h1", "2025-02-01"), make_application(2, "h2", "2025-02-01")])
    df = add_poverty_gap(build_features(households, surveys, apps, area), poverty_line=10_000.0)

    X, y = feature_matrix(df, target="poverty_gap")

    leaked = set(X.columns) & (set(NEVER_FEATURES) | {"consumption_pc", "poverty_gap"})
    assert not leaked
    assert not any(c.startswith(("created_at", "survey_date", "submitted_at")) for c in X.columns)
    assert "female_headed" in X.columns, "the spec makes female_headed a model input (and audit dimension)"
    assert y.name == "poverty_gap"
