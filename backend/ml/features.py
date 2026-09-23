"""Feature pipeline — the runnable version of the design spec's build_features().

Kept as close to the spec's original skeleton as the real schema allows.
Key difference from the skeleton: applications don't carry area_code
directly, so we join through households first. Protected attributes are
never joined in here at all — they live in a separate table
(protected_attributes) that this module never reads, so there's no risk of
"exclude the columns after the fact" being the only thing standing between
the model and a protected attribute.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Columns that must never reach the model, beyond whatever protected_attributes
# already keeps out by simply not being joined in. urban_rural is deliberately
# excluded as a raw feature per the design spec — use it to stratify
# (train separate urban/rural models) or to audit, not as a pooled feature.
NEVER_FEATURES = [
    "urban_rural", "region", "caseworker_id", "status", "household_id", "area_code",
    "application_id", "survey_id", "cycle_id", "submitted_at", "created_at",
    "registered_at", "survey_date", "notes", "area_name",
]
# columns that slip through merges with a suffix (created_at_survey,
# updated_at, ...) or are simply never meant to reach the model regardless
# of naming — dropped by prefix rather than exact name.
NEVER_FEATURE_PREFIXES = ("created_at", "updated_at", "registered_at", "survey_date", "submitted_at")

ID_COLUMNS = ["application_id", "household_id", "area_code", "cycle_id"]


def build_features(households: pd.DataFrame, surveys: pd.DataFrame,
                    applications: pd.DataFrame, area: pd.DataFrame) -> pd.DataFrame:
    """Merge applications -> households -> surveys -> area, then compute the
    derived features called out in the design spec. Returns a DataFrame that
    still carries ID columns and `status` — callers select the final model
    matrix with `feature_matrix()` below, once they've decided which rows to
    train on.
    """
    df = (
        _survey_as_of_submission(applications, surveys)
        .merge(households[["household_id", "area_code"]], on="household_id", how="left")
        .merge(area, on="area_code", how="left", suffixes=("", "_area"))
    )
    return add_derived_features(df)


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """The spec's derived features. Strictly row-wise — each row's values
    depend on that row alone, so scoring one application gives the same
    answer as scoring it inside a batch. (asset_index is the exception that
    proves the rule: it needs loadings fitted on training data, so it lives
    in AssetIndex, owned by the model.)"""
    df = df.copy()
    df["monthly_deficit"] = df["essential_costs"] - df["monthly_income"]
    df["deficit_ratio"] = df["monthly_deficit"] / df["essential_costs"].clip(lower=1)
    df["request_closes_gap"] = (df["amount_requested"] >= df["monthly_deficit"]).astype(int)
    df["crowding"] = df["household_size"] / df["rooms"].clip(lower=1)
    df["income_volatility"] = df["income_std_12m"] / df["monthly_income"].clip(lower=1)

    shock_cols = [c for c in df.columns if c.startswith("shock_") and c != "shock_count_12m"]
    df["shock_count_12m"] = df[shock_cols].fillna(False).astype(int).sum(axis=1)

    df["dependency_ratio"] = (
        (df["children_under_5"] + df["members_over_65"]) / df["household_size"].clip(lower=1)
    )
    return df


class AssetIndex:
    """First principal component of asset ownership — the standard PMT asset
    index. Fitted ONCE on training rows and stored with the model: computing
    it on whatever rows happen to be scored would change its scale (and
    possibly its sign) from batch to batch, and make it zero for a single
    application. Oriented so that higher = owns more."""

    def __init__(self, columns: list[str] | None = None, mean: list[float] | None = None,
                 loadings: list[float] | None = None):
        self.columns, self.mean, self.loadings = columns or [], mean or [], loadings or []

    @staticmethod
    def _matrix(df: pd.DataFrame, columns: list[str]) -> np.ndarray:
        return df.reindex(columns=columns).fillna(False).astype(float).values

    def fit(self, df: pd.DataFrame) -> "AssetIndex":
        self.columns = sorted(c for c in df.columns if c.startswith("asset_") and c != "asset_index")
        x = self._matrix(df, self.columns)
        mean = x.mean(axis=0)
        xc = x - mean
        if xc.shape[0] < 2 or np.allclose(xc, 0):
            v = np.zeros(len(self.columns))
        else:
            _, _, vt = np.linalg.svd(xc, full_matrices=False)
            v = vt[0] if vt[0].sum() >= 0 else -vt[0]
        self.mean, self.loadings = mean.tolist(), v.tolist()
        return self

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        if not self.columns:
            return np.zeros(len(df))
        return (self._matrix(df, self.columns) - np.asarray(self.mean)) @ np.asarray(self.loadings)

    def to_dict(self) -> dict:
        return {"columns": self.columns, "mean": self.mean, "loadings": self.loadings}


def _survey_as_of_submission(applications: pd.DataFrame, surveys: pd.DataFrame) -> pd.DataFrame:
    """Attach to each application the household's most recent survey taken
    on or before the day it was submitted.

    A plain merge on household_id is wrong twice over: it lets an
    application be scored on a survey taken after it was submitted (label
    leakage from the future), and once a household has more than one survey
    wave it duplicates that household's applications. Applications with no
    survey yet keep NaN survey fields — LightGBM handles missing values, and
    the count is visible via `survey_date.isna()`.
    """
    def _day(s: pd.Series) -> pd.Series:
        # TIMESTAMPTZ arrives tz-aware, DATE arrives as datetime.date; compare
        # both as naive calendar days so a same-day survey counts as prior.
        s = pd.to_datetime(s, utc=True)
        return s.dt.tz_localize(None).dt.normalize().astype("datetime64[ns]")

    apps = applications.copy()
    apps["_order"] = np.arange(len(apps))
    apps["_asof"] = _day(apps["submitted_at"])
    sv = surveys.copy()
    sv["_asof"] = _day(sv["survey_date"])

    merged = pd.merge_asof(
        apps.sort_values("_asof"), sv.sort_values("_asof"),
        on="_asof", by="household_id", direction="backward", suffixes=("", "_survey"),
    )
    return merged.sort_values("_order").drop(columns=["_order", "_asof"]).reset_index(drop=True)


def add_poverty_gap(df: pd.DataFrame, poverty_line: float) -> pd.DataFrame:
    """poverty_gap = max(0, poverty_line - consumption_pc) — the design
    spec's Option A alternative target ("consumption per capita, OR the
    poverty gap below a threshold").

    Train on this, not raw consumption_pc. consumption_pc is a welfare
    measure where LOWER means needier; poverty_gap flips that so HIGHER
    always means needier, which is what allocate() assumes when it ranks
    descending and awards to the top of the ranking. Training directly on
    consumption_pc and ranking descending — which is what a first pass at
    this pipeline did — silently prioritises the LEAST needy applicants.
    That bug only surfaced by running audit() against ground truth; it
    would not have thrown an error anywhere.
    """
    df = df.copy()
    df["poverty_gap"] = (poverty_line - df["consumption_pc"]).clip(lower=0)
    return df


def feature_matrix(df: pd.DataFrame, target: str | None = None):
    """Select the final X (and optionally y) for modelling: drop IDs,
    protected-adjacent columns, and anything not meant to reach the model.
    Categorical (object) columns are cast to pandas 'category' dtype so
    LightGBM can split on them natively.
    """
    drop_cols = set(NEVER_FEATURES)
    if target:
        drop_cols.add(target)
    # ground-truth columns leak the label regardless of which one is the
    # active target — drop both whenever they aren't themselves the target
    for leaky_col in ("consumption_pc", "poverty_gap"):
        if leaky_col != target:
            drop_cols.add(leaky_col)
    drop_cols.update(c for c in df.columns if c.startswith(NEVER_FEATURE_PREFIXES))

    x = df.drop(columns=[c for c in drop_cols if c in df.columns], errors="ignore").copy()

    for col in x.columns:
        dtype = x[col].dtype
        is_stringy = (
            dtype == object
            or pd.api.types.is_string_dtype(dtype)
            or (isinstance(dtype, pd.CategoricalDtype) and dtype.categories.dtype == object)
        )
        is_boolish = dtype == "bool" or (isinstance(dtype, pd.CategoricalDtype) and dtype.categories.dtype == bool)
        if is_stringy or is_boolish:
            x[col] = x[col].astype("category")

    y = df[target] if target else None
    return x, y
