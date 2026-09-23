"""The design spec's evaluation metrics, one function each. Everything takes
plain arrays so the same code scores the LightGBM model, the baselines and
the placeholder."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .allocate import COMMITTED_BANDS
from .audit import exclusion_error
from .features import add_derived_features
from .model import welfare_weights

__all__ = ["exclusion_error", "inclusion_error", "coverage", "weighted_pinball", "spearman", "band_shares",
           "direction_violations"]


def inclusion_error(bands, true_gap) -> float:
    """Share of committed approvals (auto + audit) that went to households
    above the poverty line (true poverty_gap == 0). Report it; never optimise
    it at the expense of exclusion error. The audit sample raises it by
    design — it approves at random."""
    bands, gap = np.asarray(bands), np.asarray(true_gap, dtype=float)
    approved = np.isin(bands, COMMITTED_BANDS)
    return float((gap[approved] <= 0).mean()) if approved.any() else float("nan")


def coverage(y, lo, hi) -> float:
    y = np.asarray(y, dtype=float)
    return float(np.mean((y >= np.asarray(lo)) & (y <= np.asarray(hi))))


def weighted_pinball(y, pred, alpha: float = 0.5) -> float:
    """Quantile loss with the training objective's welfare weights — the
    spec's 'welfare-weighted loss' metric. At alpha=0.5 it is half the
    welfare-weighted mean absolute error. Lower is better."""
    y, pred = np.asarray(y, dtype=float), np.asarray(pred, dtype=float)
    diff = y - pred
    return float(np.average(np.maximum(alpha * diff, (alpha - 1) * diff), weights=welfare_weights(y)))


def spearman(y, pred) -> float:
    """Rank agreement between predicted and true need — what allocation
    actually consumes."""
    return float(pd.Series(np.asarray(y, dtype=float)).corr(pd.Series(np.asarray(pred, dtype=float)),
                                                             method="spearman"))


def band_shares(bands) -> dict[str, float]:
    s = pd.Series(np.asarray(bands)).value_counts(normalize=True)
    return {b: float(s.get(b, 0.0)) for b in ("auto_approve", "human_review", "defer", "audit_approve")}


def direction_violations(model, features: pd.DataFrame, expected: dict[str, int], tolerance: float,
                         sample: int = 2_000, seed: int = 0) -> dict[str, float]:
    """For each raw input with a known direction, change it — numbers rise
    by half a standard deviation, booleans flip False -> True — recompute the
    derived features so the household stays coherent (more income means a
    smaller deficit), and return the share of rows whose median prediction
    moves the WRONG way by more than `tolerance`. Small wiggles below the
    tolerance are tree step-function noise, not a reversed effect."""
    f = features.sample(min(len(features), sample), random_state=seed)
    base = model.predict(f)["need_mid"].values
    out = {}
    for col, sign in expected.items():
        if col not in f.columns:
            continue
        nudged, rows = f.copy(), np.ones(len(f), dtype=bool)
        if f[col].dropna().isin([True, False]).all():
            rows = ~f[col].fillna(False).astype(bool).values
            nudged[col] = True
        else:
            step = float(pd.to_numeric(f[col], errors="coerce").std())
            if not np.isfinite(step) or step == 0:
                continue
            nudged[col] = nudged[col].astype(float) + 0.5 * step
        delta = model.predict(add_derived_features(nudged))["need_mid"].values - base
        out[col] = float(np.mean(sign * delta[rows] < -tolerance)) if rows.any() else 0.0
    return out
