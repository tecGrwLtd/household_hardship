"""Explanation (SHAP) and fairness audit — the two things that make the
allocation defensible after the fact, not just accurate on average.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import shap

# Disaggregation dimensions. ethnicity/gender_head/disability/age_band come
# from protected_attributes (audit-only, never joined into features);
# urban_rural comes from area_reference. Never a single blended fairness
# number — report every attribute, since that's where the real problems live
# (design spec, citing the Allegheny County VI-SPDAT replacement).
AUDIT_ATTRIBUTES = ["ethnicity", "gender_head", "disability", "age_band", "urban_rural"]


def explain(models: dict, X: pd.DataFrame, top_n: int = 5) -> list[list[tuple]]:
    explainer = shap.TreeExplainer(models["mid"])
    vals = explainer.shap_values(X)
    out = []
    for row in vals:
        order = np.argsort(np.abs(row))[::-1][:top_n]
        out.append([(X.columns[i], float(row[i])) for i in order])
    return out


def audit(decisions: pd.DataFrame, protected: pd.DataFrame, truth: pd.Series,
          cycle_id: int) -> pd.DataFrame:
    """decisions: application_id, household_id, band (+ anything else).
    protected: household_id + AUDIT_ATTRIBUTES columns.
    truth: true_need (consumption_pc), indexed like `decisions`.

    Exclusion error is computed among the bottom decile of TRUE need. In a
    real deployment "true need" for anyone outside the randomised audit
    sample doesn't exist — you'd substitute the audit sample's outcomes
    here. This synthetic dataset knows the ground truth for everyone, which
    is a luxury only the demo has; don't read the exact numbers as anything
    but a pipeline smoke test.
    """
    d = decisions.merge(protected, on="household_id", how="left")
    d["true_need"] = truth.values if not isinstance(truth, pd.Series) else truth.reset_index(drop=True)
    bottom_cut = d["true_need"].quantile(0.10)
    bottom = d[d["true_need"] <= bottom_cut]

    rows = []
    for col in AUDIT_ATTRIBUTES:
        if col not in bottom.columns:
            continue
        for value, grp in bottom.groupby(col, observed=True):
            rows.append({
                "cycle_id": cycle_id,
                "attribute": col,
                "group": str(value),
                "n": len(grp),
                "exclusion_error": float((grp["band"] == "defer").mean()),
            })
    res = pd.DataFrame(rows)
    if res.empty:
        return res
    res["gap_vs_best"] = res.groupby("attribute")["exclusion_error"].transform(lambda s: s - s.min())
    return res.sort_values("gap_vs_best", ascending=False).reset_index(drop=True)
