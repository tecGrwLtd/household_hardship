"""Monthly drift check (design spec, Evaluation > Drift): is the model still
looking at the population it was trained on, and does it still behave the
same way?

  - PSI per feature: the model's exact inputs, recent applicants vs the
    applicant population at training time
  - PSI of the score (need_mid) distribution
  - SHAP stability: do the same features still drive the scores?
  - mean score by subgroup (aggregate only, groups under 20 suppressed)
  - interval coverage on recent applications whose outcome is known

The reference profile is built once, at training, and stored in the model's
metadata.json, so a drift report always compares against what that exact
version saw. PSI reading (standard): < 0.1 stable, 0.1-0.2 watch, > 0.2 shift.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .audit import MIN_GROUP_N

PSI_WATCH, PSI_ALERT = 0.1, 0.2
SHAP_TOP = 10
EPS = 1e-4
MISSING = "__missing__"


# ---------------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------------

def _numeric_profile(values: pd.Series, bins: int = 10) -> dict:
    v = pd.to_numeric(values, errors="coerce").astype(float)
    present = v.dropna()
    edges = np.unique(np.quantile(present, np.linspace(0, 1, bins + 1))) if len(present) else np.array([])
    return {"type": "numeric", "edges": edges.tolist(), "shares": _numeric_shares(v, edges.tolist())}


def _numeric_shares(values: pd.Series, edges: list[float]) -> list[float]:
    """Shares per interior bin (open-ended at both ends) plus a final share
    for missing values."""
    v = pd.to_numeric(values, errors="coerce").astype(float)
    n = max(len(v), 1)
    inner = edges[1:-1]
    idx = np.digitize(v.dropna(), inner) if len(edges) > 1 else np.zeros(v.notna().sum(), dtype=int)
    counts = np.bincount(idx, minlength=max(len(edges) - 1, 1))
    return (counts / n).tolist() + [float(v.isna().sum() / n)]


def _categorical_profile(values: pd.Series) -> dict:
    return {"type": "categorical", "shares": _categorical_shares(values, None)}


def _categorical_shares(values: pd.Series, levels) -> dict[str, float]:
    v = values.astype(object).where(values.notna(), MISSING).astype(str)
    s = v.value_counts(normalize=True).to_dict()
    if levels is None:
        return {k: float(x) for k, x in s.items()}
    return {k: float(s.get(k, 0.0)) for k in levels} | {"__other__": float(sum(x for k, x in s.items() if k not in levels))}


def psi(expected, actual) -> float:
    e = np.clip(np.asarray(expected, dtype=float), EPS, None)
    a = np.clip(np.asarray(actual, dtype=float), EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


# ---------------------------------------------------------------------------
# Reference (at training) and report (monthly)
# ---------------------------------------------------------------------------

def reference_profile(model, features: pd.DataFrame, sample: int = 3_000, seed: int = 0) -> dict:
    """Stored with a trained need model: the distribution of each of its
    inputs and of its score over the applicant population it was trained
    among, and mean |SHAP| per feature."""
    X = model.inputs.transform(features)
    feats = {c: (_categorical_profile(X[c]) if c in model.inputs.categories else _numeric_profile(X[c]))
             for c in X.columns}
    pred = model.predict(features)
    s = features.sample(min(len(features), sample), random_state=seed)
    contrib = model.boosters["mid"].predict(model.inputs.transform(s), pred_contrib=True)[:, :-1]
    return {
        "rows": int(len(features)),
        "features": feats,
        "score": _numeric_profile(pred["need_mid"]),
        "mean_abs_shap": dict(zip(model.feature_names, np.abs(contrib).mean(axis=0).round(4).tolist())),
    }


def drift_report(model, reference: dict, features: pd.DataFrame, groups: pd.DataFrame | None = None,
                 truth: pd.Series | None = None) -> dict:
    """features: build_features() rows for the window. groups: same rows,
    audit attributes to break the score down by (aggregate only). truth:
    poverty_gap where the outcome is known (NaN elsewhere)."""
    X = model.inputs.transform(features)
    feature_psi = {}
    for c, ref in reference["features"].items():
        if c not in X.columns:
            continue
        if ref["type"] == "categorical":
            levels = list(ref["shares"])
            cur = _categorical_shares(X[c], levels)
            feature_psi[c] = psi([ref["shares"][k] for k in levels] + [0.0],
                                 [cur[k] for k in levels] + [cur["__other__"]])
        else:
            feature_psi[c] = psi(ref["shares"], _numeric_shares(X[c], ref["edges"]))

    pred = model.predict(features)
    score_psi = psi(reference["score"]["shares"], _numeric_shares(pred["need_mid"], reference["score"]["edges"]))

    contrib = model.boosters["mid"].predict(X, pred_contrib=True)[:, :-1]
    cur_shap = pd.Series(np.abs(contrib).mean(axis=0), index=model.feature_names)
    ref_shap = pd.Series(reference["mean_abs_shap"]).reindex(model.feature_names).fillna(0.0)
    top_ref = ref_shap.sort_values(ascending=False).index[:SHAP_TOP]
    top_cur = cur_shap.sort_values(ascending=False).index[:SHAP_TOP]
    shap = {
        "top_features_reference": list(top_ref),
        "top_features_now": list(top_cur),
        "overlap": len(set(top_ref) & set(top_cur)) / SHAP_TOP,
        "rank_correlation": float(ref_shap[top_ref].rank().corr(cur_shap[top_ref].rank())),
    }

    by_group = {}
    if groups is not None:
        g = groups.reset_index(drop=True).assign(need_mid=pred["need_mid"].values)
        for col in groups.columns:
            stats = g.groupby(col, observed=True)["need_mid"].agg(["count", "mean"])
            stats = stats[stats["count"] >= MIN_GROUP_N]
            by_group[col] = {str(k): {"n": int(r["count"]), "mean_need": float(r["mean"])} for k, r in stats.iterrows()}

    coverage = None
    if truth is not None:
        known = truth.notna().values
        if known.sum() >= MIN_GROUP_N:
            t = truth.values[known]
            coverage = {"n": int(known.sum()),
                        "coverage": float(np.mean((t >= pred["need_lo"].values[known]) & (t <= pred["need_hi"].values[known])))}

    shifted = sorted(((c, v) for c, v in feature_psi.items() if v > PSI_WATCH), key=lambda cv: -cv[1])
    alert = score_psi > PSI_ALERT or any(v > PSI_ALERT for _, v in shifted) or shap["overlap"] < 0.7 or (
        coverage is not None and not 0.72 <= coverage["coverage"] <= 0.88)
    watch = score_psi > PSI_WATCH or bool(shifted) or shap["overlap"] < 0.9
    return {
        "applications": int(len(features)),
        "status": "alert" if alert else ("watch" if watch else "ok"),
        "score_psi": score_psi,
        "features_shifted": [{"feature": c, "psi": v, "level": "shift" if v > PSI_ALERT else "watch"} for c, v in shifted],
        "feature_psi": feature_psi,
        "shap_stability": shap,
        "mean_need_by_group": by_group,
        "interval_coverage": coverage,
    }
