"""Honest, side-by-side evaluation of the need model against its baselines
and the placeholder.

Every candidate goes through the same steps: out-of-fold predictions for
every application (each area scored by a model that never saw it), a
conformal widening for models whose intervals are in target units, then the
real allocation, cycle by cycle, under each cycle's budget. The metrics
describe that allocation — the thing the programme actually does — not a
regression score in isolation.

Ground truth: this synthetic dataset has consumption_pc for every
application, so exclusion/inclusion error and rank correlation are measured
on everyone. A real deployment only knows outcomes for the randomised audit
sample; restrict these metrics to that sample then. Coverage and pinball loss
are already measured on labelled (approved/audited) rows only.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from .allocate import allocate_all_cycles
from .audit import APPLICATION_AUDIT_ATTRIBUTES, audit
from .baselines import DeficitRankModel, RidgePMTModel, RuleBasedModel
from .metrics import (band_shares, coverage, direction_violations, exclusion_error, inclusion_error,
                      spearman, weighted_pinball)
from .model import EXPECTED_DIRECTION, LGBMNeedModel, cross_conformal, out_of_fold_predict

PRIMARY = "lgbm"
CANDIDATES: dict[str, Callable[[], object]] = {
    PRIMARY: LGBMNeedModel,
    "ridge_pmt": RidgePMTModel,
    "deficit_rank": DeficitRankModel,
    "rules_placeholder": RuleBasedModel,
}

# Gates a version must pass before `activate` will switch it on without
# --force. Coverage is the spec's 78-82%; the rest are "beat the baselines".
COVERAGE_RANGE = (0.78, 0.82)
DIRECTION_VIOLATION_LIMIT = 0.05   # max share of rows moving materially the wrong way, per input
DIRECTION_TOLERANCE = 0.02         # "materially" = more than 2% of the target's standard deviation
BASELINES = ("ridge_pmt", "deficit_rank")


def evaluate(full: pd.DataFrame, target: str, labelled, funding_cycles: pd.DataFrame,
             protected: pd.DataFrame, seed: int, candidates: dict | None = None) -> dict:
    """full: build_features() output plus the target column. labelled: mask
    of rows the models may train on. protected: household_id + household
    audit attributes (incl. urban_rural, region).

    Returns {"models": {name: metrics}, "gates": {...}, "fairness": DataFrame,
    "conformal": {name: q_hat}, "primary_model": the primary model fitted on
    ALL labelled rows with its conformal adjustment set — the deployable one}."""
    candidates = candidates or CANDIDATES
    labelled = np.asarray(labelled, dtype=bool)
    y = full[target].astype(float).values
    groups = full["area_code"].values
    ids = full[["application_id", "household_id", "cycle_id", "amount_requested",
                *APPLICATION_AUDIT_ATTRIBUTES]].reset_index(drop=True)
    true_welfare = full["consumption_pc"].astype(float).values
    true_gap = y

    models, conformal, fairness, primary = {}, {}, None, None
    for name, make in candidates.items():
        in_units = make().in_target_units
        oof = out_of_fold_predict(make, full, y, labelled, groups).reset_index(drop=True)
        q, honest_cov = cross_conformal(oof, y, labelled) if in_units else (0.0, float("nan"))
        oof["need_lo"] -= q
        oof["need_hi"] += q

        # same seed per model, so every candidate gets the same audit-sample draws
        alloc, _ = allocate_all_cycles(pd.concat([ids, oof], axis=1), funding_cycles,
                                       np.random.default_rng(seed))
        pos = pd.Series(np.arange(len(ids)), index=ids["application_id"])
        row = pos[alloc["application_id"]].values   # alloc row -> position in full
        poor = true_gap > 0

        m = {
            "exclusion_error_bottom_decile": exclusion_error(alloc["band"], true_welfare[row]),
            "inclusion_error": inclusion_error(alloc["band"], true_gap[row]),
            # Ranking quality among households below the poverty line: above
            # it every true gap is 0, so rank order there is all ties and
            # carries no information about who should be funded first.
            "spearman_poor": spearman(true_gap[poor], oof["need_mid"][poor]),
            "spearman_all": spearman(true_gap, oof["need_mid"]),
            "bands": band_shares(alloc["band"]),
        }
        if in_units:
            m.update({
                "coverage": honest_cov,
                "coverage_in_sample_q": coverage(y[labelled], oof["need_lo"][labelled], oof["need_hi"][labelled]),
                "weighted_pinball_mid": weighted_pinball(y[labelled], oof["need_mid"][labelled]),
                "conformal_q": q,
            })
        models[name] = m
        conformal[name] = q

        if name == PRIMARY:
            fairness = _fairness(alloc, protected, true_welfare[row])
            primary = make().fit(full[labelled], y[labelled])
            primary.conformal = q
            m["direction_violations"] = direction_violations(
                primary, full, EXPECTED_DIRECTION, tolerance=DIRECTION_TOLERANCE * float(np.std(y[labelled])),
                seed=seed)

    return {"models": models, "gates": gates(models, fairness), "fairness": fairness, "conformal": conformal,
            "primary_model": primary}


def _fairness(alloc: pd.DataFrame, protected: pd.DataFrame, truth: np.ndarray) -> pd.DataFrame:
    """Pooled across all cycles (cycle_id None — the numbers to judge) and per
    cycle (trend only; most groups fall under audit.MIN_GROUP_N)."""
    cols = ["application_id", "household_id", "band", *APPLICATION_AUDIT_ATTRIBUTES]
    truth = pd.Series(truth, index=alloc.index)
    frames = [audit(alloc[cols], protected, truth, cycle_id=None)]
    for cycle_id, part in alloc.groupby("cycle_id"):
        frames.append(audit(part[cols], protected, truth[part.index], cycle_id))
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def gates(models: dict, fairness: pd.DataFrame | None) -> dict:
    """Pass/fail checks for the primary model. The spec leaves the headline
    thresholds to be agreed before launch; until then 'beats every baseline'
    and the coverage range are what is enforced."""
    p = models[PRIMARY]
    out = {
        "coverage_in_range": COVERAGE_RANGE[0] <= p["coverage"] <= COVERAGE_RANGE[1],
        **{f"exclusion_error_not_worse_than_{b}":
           p["exclusion_error_bottom_decile"] <= models[b]["exclusion_error_bottom_decile"]
           for b in BASELINES if b in models},
        **{f"spearman_poor_beats_{b}": p["spearman_poor"] > models[b]["spearman_poor"]
           for b in BASELINES if b in models},
    }
    if "direction_violations" in p:
        out["expected_directions_hold"] = all(v <= DIRECTION_VIOLATION_LIMIT
                                              for v in p["direction_violations"].values())
    if "ridge_pmt" in models:
        out["pinball_beats_ridge_pmt"] = p["weighted_pinball_mid"] < models["ridge_pmt"]["weighted_pinball_mid"]
    if fairness is not None and len(fairness):
        pooled = fairness[fairness["cycle_id"].isna()]
        out["max_subgroup_gap"] = float(pooled["gap_vs_best"].max()) if len(pooled) else None
    out["passed"] = all(v for k, v in out.items() if isinstance(v, bool))
    return out


def format_report(report: dict) -> str:
    rows = []
    for name, m in report["models"].items():
        rows.append({
            "model": name,
            "excl_err": m["exclusion_error_bottom_decile"],
            "incl_err": m["inclusion_error"],
            "spearman_poor": m["spearman_poor"],
            "pinball": m.get("weighted_pinball_mid", np.nan),
            "coverage": m.get("coverage", np.nan),
            "auto": m["bands"]["auto_approve"],
            "review": m["bands"]["human_review"],
            "defer": m["bands"]["defer"],
        })
    table = pd.DataFrame(rows).set_index("model").to_string(float_format=lambda v: f"{v:,.3f}")
    g = report["gates"]
    checks = "\n".join(f"  [{'x' if v else ' '}] {k}" for k, v in g.items() if isinstance(v, bool) and k != "passed")
    gap = g.get("max_subgroup_gap")
    dv = report["models"][PRIMARY].get("direction_violations", {})
    dirs = ", ".join(f"{k} {v:.1%}" for k, v in dv.items())
    return (f"{table}\n\nWrong-direction responses ({PRIMARY}, limit {DIRECTION_VIOLATION_LIMIT:.0%}): {dirs}\n"
            f"\nGates for '{PRIMARY}':\n{checks}\n"
            f"  max pooled subgroup exclusion-error gap: {gap if gap is None else f'{gap:.3f}'}\n"
            f"  => {'PASSED' if g['passed'] else 'FAILED'}")
