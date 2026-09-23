"""Fairness audit — what makes the allocation defensible after the fact,
not just accurate on average. (Per-decision explanations live on each
model's explain(); see model.py.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Disaggregation dimensions — the design spec's full list. ethnicity,
# gender_head, disability and age_band come from protected_attributes
# (audit-only, never joined into features); urban_rural and region from
# area_reference; the rest are application-record fields. Never a single
# blended fairness number — report every attribute, since that's where the
# real problems live (design spec, citing the Allegheny County VI-SPDAT
# replacement).
HOUSEHOLD_AUDIT_ATTRIBUTES = ["ethnicity", "gender_head", "disability", "age_band", "urban_rural", "region"]
APPLICATION_AUDIT_ATTRIBUTES = ["need_category", "application_channel", "referral_source"]
AUDIT_ATTRIBUTES = HOUSEHOLD_AUDIT_ATTRIBUTES + APPLICATION_AUDIT_ATTRIBUTES

BOTTOM_QUANTILE = 0.10
# Groups smaller than this are left out of the audit: with ~30 bottom-decile
# applicants per monthly cycle, a single deferral in a group of 2 reads as a
# 50-point gap. Suppressing small cells is also the usual privacy practice.
# Pool across cycles (cycle_id=None) to get groups large enough to judge.
MIN_GROUP_N = 20


def bottom_decile_mask(true_welfare) -> np.ndarray:
    """True for the worst-off 10% by true welfare (consumption_pc: lower is
    worse off)."""
    w = np.asarray(true_welfare, dtype=float)
    return w <= np.quantile(w, BOTTOM_QUANTILE)


def exclusion_error(bands, true_welfare) -> float:
    """Headline metric: share of the bottom decile of true welfare that was
    deferred. audit_approve and human_review do not count as excluded."""
    bands = np.asarray(bands)
    bottom = bottom_decile_mask(true_welfare)
    return float((bands[bottom] == "defer").mean()) if bottom.any() else float("nan")


def audit(decisions: pd.DataFrame, protected: pd.DataFrame, truth,
          cycle_id: int | None, min_group_n: int = MIN_GROUP_N) -> pd.DataFrame:
    """decisions: application_id, household_id, band, plus any of
    APPLICATION_AUDIT_ATTRIBUTES.
    protected: household_id + any of HOUSEHOLD_AUDIT_ATTRIBUTES.
    truth: true welfare (consumption_pc) per row of `decisions`, same order.
    cycle_id: the cycle audited, or None when `decisions` pools several.

    Exclusion error is computed among the bottom decile of TRUE need. In a
    real deployment "true need" for anyone outside the randomised audit
    sample doesn't exist — you'd substitute the audit sample's outcomes
    here. This synthetic dataset knows the ground truth for everyone, which
    is a luxury only the demo has; don't read the exact numbers as anything
    but a pipeline smoke test.
    """
    d = decisions.reset_index(drop=True).merge(protected, on="household_id", how="left")
    d["true_need"] = np.asarray(truth, dtype=float)
    bottom = d[bottom_decile_mask(d["true_need"])]

    rows = []
    for col in AUDIT_ATTRIBUTES:
        if col not in bottom.columns:
            continue
        for value, grp in bottom.groupby(col, observed=True):
            if len(grp) < min_group_n:
                continue
            rows.append({
                "cycle_id": cycle_id,
                "attribute": col,
                "group": str(value),
                "n": len(grp),
                "exclusion_error": float((grp["band"] == "defer").mean()),
            })
    res = pd.DataFrame(rows, columns=["cycle_id", "attribute", "group", "n", "exclusion_error"])
    if res.empty:
        return res.assign(gap_vs_best=pd.Series(dtype=float))
    res["gap_vs_best"] = res.groupby("attribute")["exclusion_error"].transform(lambda s: s - s.min())
    return res.sort_values("gap_vs_best", ascending=False).reset_index(drop=True)
