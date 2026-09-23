"""Need model — quantile GBDT trained with welfare-weighted loss and a
group split on area (not a random split: households cluster by area, and a
random split leaks neighbours across folds and inflates CV scores).

This is the runnable version of the design spec's modelling pipeline
section. Constant names and defaults are kept identical to the spec so the
two documents stay easy to cross-reference.

Every need model (this one, the baselines, the rule-based placeholder)
implements the same small interface, so evaluation, scoring and the API
never care which one they hold:

    fit(features, y) -> self        features = features.build_features() output
    predict(features) -> DataFrame  need_lo, need_mid, need_hi (higher = needier)
    explain(features, top_n)        per-row [(feature, contribution), ...]
    kind                            registry key
    in_target_units                 True if predictions are poverty_gap RWF
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.model_selection import GroupKFold

from .features import AssetIndex, feature_matrix

INTERVAL = (0.10, 0.90)      # 80% prediction interval
RANDOM_AUDIT_RATE = 0.04     # below-cutoff applicants approved at random, per cycle
QUANTILES = {"lo": INTERVAL[0], "mid": 0.5, "hi": INTERVAL[1]}
PRED_COLUMNS = ["need_lo", "need_mid", "need_hi"]

LGBM_PARAMS = dict(
    n_estimators=600, learning_rate=0.05, num_leaves=31,
    min_child_samples=40, subsample=0.8, colsample_bytree=0.8,
)

# Directions that are not in doubt: need can only fall as income rises, and
# only rise with essential costs, the deficit, income precarity, food
# insecurity and shocks. Deliberately short — a direction that is merely
# plausible is a bias, not a safeguard. (asset_index is left out; its link to
# need runs through income in this data.)
#
# Why it is enforced: unconstrained, the median model gave a HIGHER need score
# for 11% of households when their income rose and a LOWER one for 13% when
# they lost a job — tree noise that flipped from 0% to 26% between random
# seeds. A caseworker cannot defend that.
#
# How: LightGBM refuses monotone_constraints with the quantile and L1
# objectives, so the central estimate ("mid", used for ranking and
# explanations) is a Huber regression — median-like for errors beyond
# HUBER_DELTA_SD target standard deviations, and constrainable. The lo/hi
# interval models stay quantile (unconstrained); they only size the
# human-review band. Booleans enter as 0/1 so the shock flags can be
# constrained too.
MONOTONE_CONSTRAINTS = {
    "monthly_income": -1,
    "essential_costs": +1,
    "monthly_deficit": +1,
    "deficit_ratio": +1,
    "income_volatility": +1,
    "food_security_score": +1,
    "shock_count_12m": +1,
}
MONOTONE_PREFIXES = {"shock_": +1}   # every shock_*_12m flag
HUBER_DELTA_SD = 0.2

# Raw inputs the evaluation perturbs to CHECK the directions hold end to end
# (metrics.direction_violations recomputes the derived features after each
# change, so it also catches effects routed through unconstrained features).
EXPECTED_DIRECTION = {
    "monthly_income": -1,
    "essential_costs": +1,
    "food_security_score": +1,
    "shock_job_loss_12m": +1,     # boolean: flipped False -> True
}


def welfare_weights(need: np.ndarray, aversion: float = 1.5) -> np.ndarray:
    """Errors on the worst-off cost more. aversion=0 -> uniform weighting.
    This, not plain AUC/RMSE, is why the headline metric is exclusion error
    in the bottom decile rather than an aggregate score.

    `need` is the training target, where HIGHER means worse off (poverty_gap).
    The spec's skeleton wrote this for consumption_pc, where lower is worse
    off, as (1 - pct) ** aversion. Once the target became poverty_gap that
    silently gave the heaviest weight to the least needy — the same sign trap
    as the target itself (see features.add_poverty_gap)."""
    pct = pd.Series(need).rank(pct=True).values
    return pct ** aversion + 0.1


class LGBMNeedModel:
    """Three LightGBM models on the welfare-weighted poverty_gap target:
    lo / hi quantile models (10th / 90th percentile) for the interval, and a
    monotone-constrained Huber model for the central estimate that ranking
    and explanations use (see MONOTONE_CONSTRAINTS). Plus a scalar conformal
    widening of the interval (cross_conformal), stored with the model."""

    kind = "lgbm_quantile"
    in_target_units = True

    def __init__(self, params: dict | None = None):
        self.params = {**LGBM_PARAMS, **(params or {})}
        self.boosters: dict[str, lgb.Booster] = {}
        self.feature_names: list[str] = []
        self.asset_index = AssetIndex()
        self.categories: dict[str, list[str]] = {}   # categorical feature -> training levels
        self.conformal = 0.0

    def fit(self, features: pd.DataFrame, y) -> "LGBMNeedModel":
        self.asset_index = AssetIndex().fit(features)
        X = self._matrix(features)
        y = np.asarray(y, dtype=float)
        self.feature_names = list(X.columns)
        self.categories = {c: [str(v) for v in X[c].cat.categories]
                           for c in X.columns if isinstance(X[c].dtype, pd.CategoricalDtype)}
        X = self._conform(X)
        w = welfare_weights(y)
        for name, alpha in QUANTILES.items():
            if name == "mid":
                m = lgb.LGBMRegressor(objective="huber", alpha=max(HUBER_DELTA_SD * float(np.std(y)), 1.0),
                                      monotone_constraints=self.constraints(), verbosity=-1, **self.params)
            else:
                m = lgb.LGBMRegressor(objective="quantile", alpha=alpha, verbosity=-1, **self.params)
            self.boosters[name] = m.fit(X, y, sample_weight=w).booster_
        return self

    def constraints(self) -> list[int]:
        def direction(col: str) -> int:
            if col in MONOTONE_CONSTRAINTS:
                return MONOTONE_CONSTRAINTS[col]
            return next((d for p, d in MONOTONE_PREFIXES.items() if col.startswith(p)), 0)
        return [direction(c) for c in self.feature_names]

    def _X(self, features: pd.DataFrame) -> pd.DataFrame:
        return self._conform(self._matrix(features))

    def _matrix(self, features: pd.DataFrame) -> pd.DataFrame:
        X, _ = feature_matrix(features)
        for c in X.columns:   # booleans as 0/1: constrainable, and no category remapping
            if isinstance(X[c].dtype, pd.CategoricalDtype) and _is_boolean(X[c]):
                X[c] = X[c].astype(object).astype(float)
        X["asset_index"] = self.asset_index.transform(features)
        return X

    def _conform(self, X: pd.DataFrame) -> pd.DataFrame:
        """Force the training-time layout: same columns in the same order,
        categoricals with exactly their training levels, everything else
        numeric. Data entered through the API can leave a field empty for a
        whole batch, which would otherwise arrive untyped and change which
        columns LightGBM sees as categorical."""
        X = X.reindex(columns=self.feature_names)
        for c in self.feature_names:
            if c in self.categories:
                values = X[c].astype(object).where(X[c].notna(), None)
                X[c] = pd.Categorical(values.map(lambda v: None if v is None else str(v)),
                                      categories=self.categories[c])
            elif not pd.api.types.is_numeric_dtype(X[c]):
                X[c] = pd.to_numeric(X[c].astype(object), errors="coerce").astype(float)
        return X

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        X = self._X(features)
        return pd.DataFrame({
            "need_lo": self.boosters["lo"].predict(X) - self.conformal,
            "need_mid": self.boosters["mid"].predict(X),
            "need_hi": self.boosters["hi"].predict(X) + self.conformal,
        }, index=features.index)

    def explain(self, features: pd.DataFrame, top_n: int = 5) -> list[list[tuple[str, float]]]:
        """Top SHAP drivers of the central estimate per row. LightGBM's own
        pred_contrib is exact TreeSHAP — same numbers as the shap package,
        no extra dependency."""
        contrib = self.boosters["mid"].predict(self._X(features), pred_contrib=True)[:, :-1]
        return _top_contributions(contrib, self.feature_names, top_n)

    def save(self, directory: Path) -> dict:
        directory.mkdir(parents=True, exist_ok=True)
        for name, booster in self.boosters.items():
            booster.save_model(str(directory / f"{name}.txt"))
        return {"kind": self.kind, "params": self.params,
                "feature_names": self.feature_names, "categories": self.categories, "conformal": self.conformal,
                "monotone_constraints": {c: d for c, d in zip(self.feature_names, self.constraints()) if d},
                "asset_index": self.asset_index.to_dict()}

    @classmethod
    def load(cls, directory: Path, metadata: dict) -> "LGBMNeedModel":
        m = cls(params=metadata.get("params"))
        m.boosters = {name: lgb.Booster(model_file=str(directory / f"{name}.txt")) for name in QUANTILES}
        m.feature_names = metadata["feature_names"]
        m.asset_index = AssetIndex(**metadata["asset_index"])
        m.categories = metadata["categories"]
        m.conformal = float(metadata.get("conformal", 0.0))
        return m


def _is_boolean(s: pd.Series) -> bool:
    values = s.dropna().unique()
    return len(values) > 0 and all(isinstance(v, (bool, np.bool_)) for v in values)


def _top_contributions(contrib: np.ndarray, names: list[str], top_n: int) -> list[list[tuple[str, float]]]:
    out = []
    for row in contrib:
        order = np.argsort(np.abs(row))[::-1][:top_n]
        out.append([(names[i], float(row[i])) for i in order])
    return out


def out_of_fold_predict(make_model: Callable[[], object], features: pd.DataFrame, y,
                        labelled, groups, n_splits: int = 5) -> pd.DataFrame:
    """Honest predictions for EVERY row via GroupKFold on area_code: each
    area is predicted by a model trained only on labelled rows from other
    areas. Use this for evaluation — coverage, and the fairness audit, which
    must see the allocation the model would produce on applicants it never
    trained on. Never deploy these models.

    labelled: boolean mask of rows whose target may be trained on (the
    selective-labels constraint — approved/audited applicants only). Every
    row, labelled or not, gets a prediction.
    """
    labelled = np.asarray(labelled, dtype=bool)
    y = np.asarray(y, dtype=float)
    oof = pd.DataFrame(np.nan, index=features.index, columns=PRED_COLUMNS)
    oof["fold"] = -1

    for k, (train_idx, val_idx) in enumerate(GroupKFold(n_splits=n_splits).split(features, groups=groups)):
        fit_idx = train_idx[labelled[train_idx]]
        model = make_model().fit(features.iloc[fit_idx], y[fit_idx])
        oof.iloc[val_idx, :3] = model.predict(features.iloc[val_idx])[PRED_COLUMNS].values
        oof.iloc[val_idx, 3] = k

    return oof


def cross_conformal(oof: pd.DataFrame, y, labelled) -> tuple[float, float]:
    """Conformal widening from the out-of-fold predictions themselves
    (cross-conformal): q_hat from the interval misses of every labelled row,
    across all areas — far steadier than one held-out split of a handful of
    areas. Returns (q_hat, honest coverage), where the coverage estimate
    widens each fold by a q computed from the OTHER folds only.
    oof: out_of_fold_predict() output (with its `fold` column)."""
    lab = np.asarray(labelled, dtype=bool)
    lo, hi = oof["need_lo"].values[lab], oof["need_hi"].values[lab]
    yy, fold = np.asarray(y, dtype=float)[lab], oof["fold"].values[lab]
    hits = []
    for k in np.unique(fold):
        test = fold == k
        q_k = conformal_adjustment(lo[~test], hi[~test], yy[~test])
        hits.append((yy[test] >= lo[test] - q_k) & (yy[test] <= hi[test] + q_k))
    return conformal_adjustment(lo, hi, yy), float(np.concatenate(hits).mean())

def conformal_adjustment(lo: np.ndarray, hi: np.ndarray, y: np.ndarray,
                          target_coverage: float = INTERVAL[1] - INTERVAL[0]) -> float:
    """Conformalized-quantile-regression correction (Romano, Patterson & Candès
    2019). Fixes exactly the failure mode this pipeline hits in practice:
    welfare_weights() reweights the quantile loss toward the worst-off, which
    is the whole point of the training objective, but it also means the raw
    'lo'/'hi' outputs are no longer calibrated to the unweighted 10th/90th
    percentile of y — nominal 80% coverage measured directly came out at
    ~48% on this dataset. Rather than drop the welfare weighting (it's the
    spec's central ethical design choice) this widens the interval by a
    single scalar, fit on a held-out calibration split, so coverage is
    restored without touching how cases get ranked.

    Fit on predictions the model did not train on (cross_conformal() uses
    the out-of-fold ones), and store the result on the model
    (LGBMNeedModel.conformal).
    """
    scores = np.maximum(lo - y, y - hi)
    n = len(scores)
    level = min(1.0, np.ceil((n + 1) * target_coverage) / n)
    q = float(np.quantile(scores, level))
    return max(q, 0.0)


def selection_weights(observed: pd.DataFrame, all_apps: pd.DataFrame,
                       feature_cols: list[str]) -> np.ndarray:
    """Covariate-shift correction for the selective-labels problem: outcomes
    (consumption_pc) exist only for approved/audited applicants in a real
    deployment. This is a stopgap, not a fix — the randomised audit sample
    (RANDOM_AUDIT_RATE in allocate.py) is the real fix. Do not use this to
    impute rejected cases as negative outcomes; some of them would have been
    positive.
    """
    obs = observed[feature_cols].copy()
    obs["_lab"] = 1
    alt = all_apps[feature_cols].copy()
    alt["_lab"] = 0
    pooled = pd.concat([obs, alt], ignore_index=True)
    for col in feature_cols:
        if pooled[col].dtype.name == "category" or pooled[col].dtype == object:
            pooled[col] = pooled[col].astype("category")

    clf = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, verbosity=-1)
    clf.fit(pooled[feature_cols], pooled["_lab"])

    p = clf.predict_proba(obs[feature_cols])[:, 1].clip(0.05, 0.95)
    return (1 - p) / p
