"""Need model — quantile GBDT trained with welfare-weighted loss and a
group split on area (not a random split: households cluster by area, and a
random split leaks neighbours across folds and inflates CV scores).

This is the runnable version of the design spec's modelling pipeline
section. Function names and defaults are kept identical to the spec so the
two documents stay easy to cross-reference.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.model_selection import GroupKFold

INTERVAL = (0.10, 0.90)      # 80% prediction interval
RANDOM_AUDIT_RATE = 0.04     # below-cutoff applicants approved at random, per cycle

LGBM_PARAMS = dict(
    n_estimators=600, learning_rate=0.05, num_leaves=31,
    min_child_samples=40, subsample=0.8, colsample_bytree=0.8,
)


def welfare_weights(y: np.ndarray, aversion: float = 1.5) -> np.ndarray:
    """Errors on the worst-off cost more. aversion=0 -> uniform weighting.
    This, not plain AUC/RMSE, is why the headline metric in evaluation.py
    is exclusion error in the bottom decile rather than an aggregate score."""
    pct = pd.Series(y).rank(pct=True).values
    return (1.0 - pct) ** aversion + 0.1


def cross_validate_need_model(X: pd.DataFrame, y: np.ndarray, groups: pd.Series,
                               n_splits: int = 5) -> dict[str, np.ndarray]:
    """Honest out-of-fold predictions via GroupKFold on area_code. Use this
    for evaluation (coverage, welfare-weighted loss) — never train the
    models you'll actually deploy on in-sample predictions.
    """
    cv = GroupKFold(n_splits=n_splits)
    oof = {name: np.full(len(y), np.nan) for name in ("lo", "mid", "hi")}

    for train_idx, val_idx in cv.split(X, y, groups=groups):
        w_train = welfare_weights(y[train_idx])
        for name, alpha in (("lo", INTERVAL[0]), ("mid", 0.5), ("hi", INTERVAL[1])):
            model = lgb.LGBMRegressor(objective="quantile", alpha=alpha, verbosity=-1, **LGBM_PARAMS)
            model.fit(X.iloc[train_idx], y[train_idx], sample_weight=w_train)
            oof[name][val_idx] = model.predict(X.iloc[val_idx])

    return oof


def fit_need_model(X: pd.DataFrame, y: np.ndarray) -> dict[str, lgb.LGBMRegressor]:
    """Fit the three quantile models (lo/mid/hi) on ALL eligible rows.
    These are the models you deploy — cross_validate_need_model() above is
    for measuring how well they'll generalise, not for producing them.
    """
    w = welfare_weights(y)
    models = {}
    for name, alpha in (("lo", INTERVAL[0]), ("mid", 0.5), ("hi", INTERVAL[1])):
        models[name] = lgb.LGBMRegressor(
            objective="quantile", alpha=alpha, verbosity=-1, **LGBM_PARAMS,
        ).fit(X, y, sample_weight=w)
    return models


def predict(models: dict[str, lgb.LGBMRegressor], X: pd.DataFrame,
            conformal_adjustment: float = 0.0) -> pd.DataFrame:
    return pd.DataFrame({
        "need_lo": models["lo"].predict(X) - conformal_adjustment,
        "need_mid": models["mid"].predict(X),
        "need_hi": models["hi"].predict(X) + conformal_adjustment,
    }, index=X.index)


def conformal_adjustment(lo: np.ndarray, hi: np.ndarray, y: np.ndarray,
                          target_coverage: float = INTERVAL[1] - INTERVAL[0]) -> float:
    """Conformalized-quantile-regression correction (Romano, Patterson & Candès
    2019). Fixes exactly the failure mode this pipeline hits in practice:
    welfare_weights() reweights the quantile loss toward the worst-off, which
    is the whole point of the training objective, but it also means the raw
    'lo'/'hi' outputs are no longer calibrated to the unweighted 10th/90th
    percentile of y — nominal 80% coverage measured directly came out at
    ~54% on this dataset. Rather than drop the welfare weighting (it's the
    spec's central ethical design choice) this widens the interval by a
    single scalar, fit on a held-out calibration split, so coverage is
    restored without touching how cases get ranked.

    Call this ONCE on a calibration split disjoint from both training and
    final scoring, then apply the returned scalar via predict()'s
    conformal_adjustment argument for every subsequent prediction.
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
