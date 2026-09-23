"""Models the LightGBM need model must beat, plus the rule-based placeholder
that scores applications before any model has been trained.

All implement the interface described in model.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .features import feature_matrix
from .model import INTERVAL, _top_contributions, welfare_weights


class RidgePMTModel:
    """The classic proxy-means test: a regularised linear regression of the
    welfare target on household characteristics — the baseline the design
    spec's cited PMT literature compares gradient boosting against. Same
    features, target and welfare weights as the LightGBM model, so the
    comparison isolates the model class. Interval: training-residual
    quantiles around the prediction."""

    kind = "ridge_pmt"
    in_target_units = True

    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha
        self.pipeline: Pipeline | None = None
        self.resid_q = (0.0, 0.0)

    @staticmethod
    def _X(features: pd.DataFrame) -> pd.DataFrame:
        X, _ = feature_matrix(features)
        for c in X.columns:
            if not pd.api.types.is_numeric_dtype(X[c]):
                X[c] = X[c].astype(str)   # one-hot needs plain labels; NaN -> "nan" level
        return X

    def fit(self, features: pd.DataFrame, y) -> "RidgePMTModel":
        X = self._X(features)
        y = np.asarray(y, dtype=float)
        num = [c for c in X.columns if pd.api.types.is_numeric_dtype(X[c])]
        cat = [c for c in X.columns if c not in num]
        self.pipeline = Pipeline([
            ("prep", ColumnTransformer([
                ("num", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), num),
                ("cat", OneHotEncoder(handle_unknown="ignore"), cat),
            ])),
            ("ridge", Ridge(alpha=self.alpha)),
        ]).fit(X, y, ridge__sample_weight=welfare_weights(y))
        resid = y - self.pipeline.predict(X)
        self.resid_q = tuple(np.quantile(resid, INTERVAL))
        return self

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        mid = self.pipeline.predict(self._X(features))
        return pd.DataFrame({"need_lo": mid + self.resid_q[0], "need_mid": mid,
                             "need_hi": mid + self.resid_q[1]}, index=features.index)

    def explain(self, features: pd.DataFrame, top_n: int = 5):
        prep, ridge = self.pipeline.named_steps["prep"], self.pipeline.named_steps["ridge"]
        Z = prep.transform(self._X(features))
        Z = Z.toarray() if hasattr(Z, "toarray") else Z
        return _top_contributions(Z * ridge.coef_, list(prep.get_feature_names_out()), top_n)


class DeficitRankModel:
    """Rank by the monthly deficit alone (essential costs - income): the
    'usually the strongest single predictor' from the spec's derived-features
    table. If the model cannot beat this, it is not earning its complexity.
    Not in poverty_gap units, so only its ranking metrics are comparable; the
    interval is degenerate (no human-review band)."""

    kind = "deficit_rank"
    in_target_units = False

    def fit(self, features: pd.DataFrame, y) -> "DeficitRankModel":
        return self

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        d = features["monthly_deficit"].fillna(0.0).astype(float).values
        return pd.DataFrame({"need_lo": d, "need_mid": d, "need_hi": d}, index=features.index)

    def explain(self, features: pd.DataFrame, top_n: int = 5):
        return [[("monthly_deficit", float(v))] for v in features["monthly_deficit"].fillna(0.0)]


class RuleBasedModel:
    """PLACEHOLDER scorer, active until a trained model is activated, so the
    API and dashboard work end to end from day one. Every term is written
    out below — a caseworker can recompute any score by hand. Units are
    roughly RWF per person per month; only the ranking matters for
    allocation. The interval is a flat +/-25%: honest about being a guess,
    and wide enough that close cases go to human review.

    Not a model of anything. Replace it with a trained version
    (`python -m backend.ml train`, then `activate`) as soon as one passes
    its evaluation."""

    kind = "rules"
    in_target_units = False
    WIDTH = 0.25
    TERMS = {  # feature -> RWF per person per month, per unit
        "deficit_per_person": 1.0,
        "shock_count_12m": 1_500.0,
        "food_security_score": 1_000.0,
        "dependency_ratio": 2_000.0,
        "assets_owned": -800.0,
    }

    def fit(self, features: pd.DataFrame, y=None) -> "RuleBasedModel":
        return self

    def _terms(self, features: pd.DataFrame) -> pd.DataFrame:
        f = features
        assets = [c for c in f.columns if c.startswith("asset_") and c != "asset_index"]
        raw = pd.DataFrame({
            "deficit_per_person": f["monthly_deficit"].clip(lower=0) / f["household_size"].clip(lower=1),
            "shock_count_12m": f["shock_count_12m"],
            "food_security_score": f["food_security_score"],
            "dependency_ratio": f["dependency_ratio"],
            "assets_owned": f[assets].fillna(False).astype(float).sum(axis=1) if assets else 0.0,
        }, index=f.index).astype(float).fillna(0.0)
        return raw * pd.Series(self.TERMS)

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        mid = self._terms(features).sum(axis=1).clip(lower=0.0)
        return pd.DataFrame({"need_lo": mid * (1 - self.WIDTH), "need_mid": mid,
                             "need_hi": mid * (1 + self.WIDTH)}, index=features.index)

    def explain(self, features: pd.DataFrame, top_n: int = 5):
        terms = self._terms(features)
        return _top_contributions(terms.values, list(terms.columns), top_n)
