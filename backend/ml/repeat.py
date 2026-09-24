"""Repeat-support forecast: the probability that a household applies again
within a year of an application — the kickoff call's "how many of them will
need help again, within one year or more than one year".

FOR PLANNING ONLY. It powers dashboard forecasts (expected returns per month
and support group) and must never feed allocation: ranking people down
because they are likely to need help again would punish exactly the
households the programme exists for, and reintroduce past decisions (the
spec keeps prior_award_received out of the need model for that reason).
That separation is also why these models may use `was_helped` (whether the
application was funded), which the need model never sees.

Two candidates are evaluated on the same folds and the better-calibrated
one (lower Brier score) is kept:
  - LGBMRepeatModel: survey + application features, heavily regularised.
  - HistoryRepeatModel: logistic regression on application history and
    whether it was funded — four numbers.
Isotonic calibration is applied only where it lowers the held-out Brier
score: a logistic regression is usually calibrated already, and a step
function fitted on top of it overfits (on the synthetic data it took the
history model from 0.146 to 0.158). On the synthetic data the history model
wins (0.146 vs 0.147): whether a household comes back is driven by its
history, and 60-odd survey features on ~2,300 labelled rows add little.
Real data may differ, which is why the choice is made by evaluation.

Labels need a full year of follow-up, so only applications submitted at
least REPEAT_HORIZON_DAYS before the latest application on record are
trained or evaluated on; newer ones are only forecast.
"""
from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GroupKFold

from .features import ModelInputs

REPEAT_HORIZON_DAYS = 365
HELPED_STATUSES = ("auto_approved", "audit_approved", "awarded")
REPEAT_LGBM_PARAMS = dict(n_estimators=300, learning_rate=0.03, num_leaves=4, min_child_samples=100,
                          reg_lambda=10.0, colsample_bytree=0.8)
ECE_LIMIT = 0.05


def add_was_helped(features: pd.DataFrame) -> pd.DataFrame:
    return features.assign(was_helped=features["status"].astype(str).isin(HELPED_STATUSES).astype(float))


def repeat_labels(apps: pd.DataFrame, horizon_days: int = REPEAT_HORIZON_DAYS) -> pd.DataFrame:
    """Per application: did the same household apply again within the
    horizon, and is that knowable yet? apps: application_id, household_id,
    submitted_at. Returns application_id, returned, observable, days_to_next."""
    a = apps[["application_id", "household_id", "submitted_at"]].copy()
    a["submitted_at"] = pd.to_datetime(a["submitted_at"], utc=True)
    a = a.sort_values(["household_id", "submitted_at", "application_id"])
    nxt = a.groupby("household_id")["submitted_at"].shift(-1)
    a["days_to_next"] = (nxt - a["submitted_at"]).dt.total_seconds() / 86_400
    as_of = a["submitted_at"].max()
    a["observable"] = a["submitted_at"] <= as_of - pd.Timedelta(days=horizon_days)
    a["returned"] = a["days_to_next"] <= horizon_days
    return a[["application_id", "returned", "observable", "days_to_next"]]


class _Calibrated:
    """Optional isotonic calibration on top of the raw score, so that "30%"
    means about 30 in 100 households come back. Left empty (identity) when
    evaluation showed it does not help."""
    iso_x: list[float]
    iso_y: list[float]
    purpose = "repeat"

    def calibrate(self, raw, y):
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(raw, np.asarray(y, dtype=float))
        self.iso_x, self.iso_y = iso.X_thresholds_.tolist(), iso.y_thresholds_.tolist()
        return self

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        raw = self.predict_raw(features)
        return np.interp(raw, self.iso_x, self.iso_y) if self.iso_x else raw


class LGBMRepeatModel(_Calibrated):
    kind = "repeat_lgbm"

    def __init__(self, params: dict | None = None):
        self.params = {**REPEAT_LGBM_PARAMS, **(params or {})}
        self.inputs = ModelInputs()
        self.booster: lgb.Booster | None = None
        self.iso_x, self.iso_y = [], []

    def fit(self, features: pd.DataFrame, y) -> "LGBMRepeatModel":
        f = add_was_helped(features)
        X = self.inputs.fit(f).transform(f)
        self.booster = lgb.LGBMClassifier(verbosity=-1, **self.params).fit(X, np.asarray(y, dtype=int)).booster_
        return self

    def predict_raw(self, features: pd.DataFrame) -> np.ndarray:
        return self.booster.predict(self.inputs.transform(add_was_helped(features)))

    def save(self, directory: Path) -> dict:
        directory.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(directory / "repeat.txt"))
        return {"kind": self.kind, "purpose": self.purpose, "params": self.params, "horizon_days": REPEAT_HORIZON_DAYS,
                "iso_x": self.iso_x, "iso_y": self.iso_y, **self.inputs.to_dict()}

    @classmethod
    def load(cls, directory: Path, metadata: dict) -> "LGBMRepeatModel":
        m = cls(params=metadata.get("params"))
        m.booster = lgb.Booster(model_file=str(directory / "repeat.txt"))
        m.inputs = ModelInputs(metadata["feature_names"], metadata["categories"], metadata["asset_index"])
        m.iso_x, m.iso_y = metadata["iso_x"], metadata["iso_y"]
        return m


class HistoryRepeatModel(_Calibrated):
    """Logistic regression on four numbers: prior applications, whether this
    is the first, years since the last one, and whether it was funded."""
    kind = "repeat_history"
    FEATURES = ["prior_applications_count", "first_application", "years_since_last", "was_helped"]

    def __init__(self):
        self.coef: list[float] = []
        self.intercept = 0.0
        self.iso_x, self.iso_y = [], []

    @staticmethod
    def matrix(features: pd.DataFrame) -> np.ndarray:
        f = add_was_helped(features)
        days = pd.to_numeric(f["days_since_last_application"], errors="coerce").astype(float)
        return np.column_stack([
            pd.to_numeric(f["prior_applications_count"], errors="coerce").fillna(0).astype(float),
            days.isna().astype(float), days.fillna(0.0) / 365.0, f["was_helped"].astype(float),
        ])

    def fit(self, features: pd.DataFrame, y) -> "HistoryRepeatModel":
        lr = LogisticRegression(max_iter=1000).fit(self.matrix(features), np.asarray(y, dtype=int))
        self.coef, self.intercept = lr.coef_[0].tolist(), float(lr.intercept_[0])
        return self

    def predict_raw(self, features: pd.DataFrame) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-(self.matrix(features) @ np.asarray(self.coef) + self.intercept)))

    def save(self, directory: Path) -> dict:
        directory.mkdir(parents=True, exist_ok=True)
        return {"kind": self.kind, "purpose": self.purpose, "horizon_days": REPEAT_HORIZON_DAYS,
                "features": self.FEATURES, "coef": self.coef, "intercept": self.intercept,
                "iso_x": self.iso_x, "iso_y": self.iso_y}

    @classmethod
    def load(cls, directory: Path, metadata: dict) -> "HistoryRepeatModel":
        m = cls()
        m.coef, m.intercept = metadata["coef"], metadata["intercept"]
        m.iso_x, m.iso_y = metadata["iso_x"], metadata["iso_y"]
        return m


REPEAT_CANDIDATES = {"repeat_lgbm": LGBMRepeatModel, "repeat_history": HistoryRepeatModel}


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def expected_calibration_error(p, y, bins: int = 10) -> tuple[float, list[dict]]:
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    table, ece = [], 0.0
    for b in range(bins):
        m = idx == b
        if not m.any():
            continue
        table.append({"bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}", "n": int(m.sum()),
                      "predicted": float(p[m].mean()), "observed": float(y[m].mean())})
        ece += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(ece), table


def evaluate_repeat(features: pd.DataFrame, y, groups, n_splits: int = 5, candidates: dict | None = None) -> dict:
    """Every candidate: out-of-fold raw scores (GroupKFold on area), then
    cross-fitted isotonic calibration — each fold calibrated on the others —
    so reported calibration is honest. features/y/groups: observable rows.

    Each candidate is scored raw and isotonic-calibrated; calibration is
    kept only if it lowers the held-out Brier score.

    Returns {"models": {name: metrics incl. "calibrated"}, "best": name
    (lowest Brier), "gates": {...} for the best, "oof_raw": {name: raw}}."""
    candidates = candidates or REPEAT_CANDIDATES
    y = np.asarray(y, dtype=int)
    folds = list(GroupKFold(n_splits=n_splits).split(features, groups=groups))
    base = y.mean()
    models, oof_raw = {}, {}
    for name, make in candidates.items():
        raw = np.zeros(len(y))
        for tr, te in folds:
            raw[te] = make().fit(features.iloc[tr], y[tr]).predict_raw(features.iloc[te])
        cal = np.zeros(len(y))
        for _, te in folds:
            other = np.setdiff1d(np.arange(len(y)), te)
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(raw[other], y[other])
            cal[te] = iso.predict(raw[te])
        use_cal = np.mean((cal - y) ** 2) < np.mean((raw - y) ** 2)
        p = cal if use_cal else raw
        ece, table = expected_calibration_error(p, y)
        models[name] = {
            "brier": float(np.mean((p - y) ** 2)),
            "brier_raw": float(np.mean((raw - y) ** 2)),
            "calibrated": bool(use_cal),
            "pr_auc": float(average_precision_score(y, p)),
            "roc_auc": float(roc_auc_score(y, p)),
            "ece": ece,
            "calibration": table,
        }
        oof_raw[name] = raw

    best = min(models, key=lambda n: models[n]["brier"])
    b = models[best]
    brier_base = float(np.mean((base - y) ** 2))
    gates = {"beats_base_rate": b["brier"] < brier_base, "calibrated": b["ece"] <= ECE_LIMIT}
    gates["passed"] = all(gates.values())
    return {"rows": int(len(y)), "base_rate": float(base), "brier_base_rate": brier_base,
            "models": models, "best": best, "gates": gates, "oof_raw": oof_raw}
