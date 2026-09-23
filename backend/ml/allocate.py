"""Deterministic allocation layer — turns need predictions into decisions
under a fixed budget. Contains NO learned parameters, on purpose: the
ethical choices (auto-approval is the safe direction, the random audit
sample is written into the function itself rather than left as an optional
optimisation) live in code you can read, not in model weights.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .model import RANDOM_AUDIT_RATE


def allocate(scores: pd.DataFrame, budget: float, rng: np.random.Generator) -> tuple[pd.DataFrame, float]:
    """scores: application_id, household_id, need_lo, need_mid, need_hi, amount_requested.
    Returns (ranked_with_band, cutoff).

    Band logic:
      - lo > cutoff             -> auto_approve  (safe direction: wrong here just
                                                    costs a bit of budget headroom)
      - hi < cutoff             -> defer          (the adverse decision — this is
                                                    where GDPR Art.22 / human review bites)
      - interval straddles cutoff -> human_review

    A random RANDOM_AUDIT_RATE share of the deferred group is flipped to
    audit_approve — the only source of unbiased future labels for retraining.
    This line is not optional and must not be turned off as a cost-saving
    measure; see the design spec's selective-labels section.
    """
    ranked = scores.sort_values("need_mid", ascending=False).reset_index(drop=True).copy()
    ranked["cum_cost"] = ranked["amount_requested"].cumsum()

    cutoff_idx = int((ranked["cum_cost"] <= budget).sum())
    cutoff_idx = min(cutoff_idx, len(ranked) - 1)
    cutoff = float(ranked["need_mid"].iloc[cutoff_idx]) if len(ranked) else 0.0

    ranked["band"] = np.select(
        [ranked["need_lo"] > cutoff, ranked["need_hi"] < cutoff],
        ["auto_approve", "defer"],
        default="human_review",
    )

    deferred_idx = ranked.index[ranked["band"] == "defer"]
    n_audit = int(round(len(deferred_idx) * RANDOM_AUDIT_RATE))
    if n_audit:
        picked = rng.choice(deferred_idx, size=n_audit, replace=False)
        ranked.loc[picked, "band"] = "audit_approve"

    return ranked, cutoff
