#!/usr/bin/env python3
"""
End-to-end run of the design spec's pipeline against the (synthetic) database:

  1. Load households/surveys/area/applications/protected/awards from Postgres
  2. Build features (features.py) — each application joined to the survey in
     force when it was submitted
  3. Train on ELIGIBLE rows only (auto_approved / audit_approved / awarded) —
     mirroring the real selective-labels constraint even though this
     synthetic set technically has consumption_pc for every row
  4. Evaluate honestly: out-of-fold predictions for every application
     (GroupKFold on area_code), conformal calibration, then allocate each
     cycle on those out-of-fold scores and audit() the result — so the
     fairness numbers describe applicants the model never trained on
  5. Fit the deployed models on all eligible rows
  6. For each funding cycle: allocate() under that cycle's real budget with
     the deployed models, explain() the top SHAP drivers, write model_scores
     back to Postgres
  7. Write the step-4 fairness audit to fairness_audits, pooled across all
     cycles (cycle_id NULL) and per cycle

Usage:
    python3 -m backend.ml.run_pipeline --dsn "postgresql://hardship_app:hardship_dev_only@localhost:5432/hardship_platform"
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit

from . import db
from .features import build_features, feature_matrix, add_poverty_gap
from .model import (
    out_of_fold_predict, fit_need_model, predict, INTERVAL, conformal_adjustment,
)
from .allocate import allocate, budget_summary
from .audit import APPLICATION_AUDIT_ATTRIBUTES, MIN_GROUP_N, audit, exclusion_error, explain

MODEL_VERSION = "lgbm-v1-synthetic"
ELIGIBLE_STATUSES = {"auto_approved", "audit_approved", "awarded"}
TARGET = "poverty_gap"  # NOT consumption_pc — see features.add_poverty_gap()
POVERTY_LINE_PERCENTILE = 0.5  # placeholder: a real deployment uses a published
                                # national/regional poverty line, not a data-derived one
SCORE_COLUMNS = ["application_id", "household_id", "need_lo", "need_mid", "need_hi", "amount_requested"]


def allocate_all_cycles(scored: pd.DataFrame, funding_cycles: pd.DataFrame,
                        rng: np.random.Generator) -> tuple[pd.DataFrame, list[dict]]:
    """Run allocate() per cycle under that cycle's budget. `scored` needs
    SCORE_COLUMNS + cycle_id; any extra columns ride along onto the result."""
    budgets = funding_cycles.set_index("cycle_id")["budget_total"].astype(float)
    frames, summaries = [], []
    for cycle_id, cycle_df in scored.groupby("cycle_id"):
        if cycle_id not in budgets.index:
            continue
        ranked, cutoff = allocate(cycle_df[SCORE_COLUMNS], budgets[cycle_id], rng)
        extra = cycle_df.drop(columns=SCORE_COLUMNS[1:]).set_index("application_id")
        ranked = ranked.join(extra, on="application_id")
        ranked["cutoff"] = cutoff
        frames.append(ranked)
        summaries.append({"cycle_id": cycle_id, **budget_summary(ranked, budgets[cycle_id])})
    return pd.concat(frames, ignore_index=True), summaries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    print("Loading tables from Postgres ...")
    raw = db.load_raw_tables(args.dsn)

    print("Building features ...")
    full = build_features(raw["households"], raw["surveys"], raw["applications"], raw["area"])
    n_no_survey = int(full["survey_date"].isna().sum())
    if n_no_survey:
        print(f"  {n_no_survey} applications have no survey on or before submission "
              f"(scored with missing survey fields)")

    poverty_line = float(full["consumption_pc"].quantile(POVERTY_LINE_PERCENTILE))
    full = add_poverty_gap(full, poverty_line)
    print(f"Poverty line set at the {POVERTY_LINE_PERCENTILE:.0%} percentile of "
          f"consumption_pc = {poverty_line:,.0f} RWF/capita/month (placeholder - "
          f"replace with a published poverty line once one is agreed)")

    eligible_mask = full["status"].isin(ELIGIBLE_STATUSES).values
    eligible = full[eligible_mask].copy()
    print(f"{len(full)} total applications, {len(eligible)} eligible for training "
          f"(status in {sorted(ELIGIBLE_STATUSES)})")

    X_all, y_all = feature_matrix(full, target=TARGET)
    X_elig, y_elig = X_all[eligible_mask], y_all[eligible_mask]
    groups = eligible["area_code"]

    # --- honest evaluation -------------------------------------------------
    print("Out-of-fold predictions for every application (GroupKFold on area_code, 5 folds) ...")
    oof = out_of_fold_predict(X_all, y_all, eligible_mask, full["area_code"])
    y_e = y_elig.values
    oof_e = oof[eligible_mask]
    raw_coverage = float(np.mean((y_e >= oof_e["need_lo"]) & (y_e <= oof_e["need_hi"])))
    rmse_mid = float(np.sqrt(np.mean((oof_e["need_mid"] - y_e) ** 2)))
    target_coverage = INTERVAL[1] - INTERVAL[0]
    print(f"  RAW interval coverage (eligible rows): {raw_coverage:.3f} (target ~{target_coverage:.2f})")
    print(f"  median-model RMSE (out-of-fold, eligible rows): {rmse_mid:,.0f}")
    if abs(raw_coverage - target_coverage) > 0.05:
        print("  -> raw coverage is off target. This is welfare_weights() at work: "
              "reweighting the quantile loss toward the worst-off shifts what "
              "quantile 'lo'/'hi' actually estimate. Applying conformal calibration "
              "below to fix coverage without abandoning the welfare weighting.")

    # conformal calibration: hold out a group-disjoint slice purely to
    # measure and correct interval width.
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=args.seed)
    train_idx, calib_idx = next(splitter.split(X_elig, y_elig, groups=groups))
    calib_models = fit_need_model(X_elig.iloc[train_idx], y_e[train_idx])
    calib_preds = predict(calib_models, X_elig.iloc[calib_idx])
    q_hat = conformal_adjustment(
        calib_preds["need_lo"].values, calib_preds["need_hi"].values, y_e[calib_idx],
    )
    calibrated_coverage = float(np.mean(
        (y_e[calib_idx] >= calib_preds["need_lo"].values - q_hat)
        & (y_e[calib_idx] <= calib_preds["need_hi"].values + q_hat)
    ))
    print(f"  conformal adjustment q_hat = {q_hat:,.0f}; calibration-split coverage "
          f"after adjustment: {calibrated_coverage:.3f}")

    ids = full[["application_id", "household_id", "cycle_id", "amount_requested",
                *APPLICATION_AUDIT_ATTRIBUTES]].reset_index(drop=True)
    oof_cal = oof.reset_index(drop=True).assign(
        need_lo=lambda d: d["need_lo"] - q_hat, need_hi=lambda d: d["need_hi"] + q_hat,
    )
    eval_alloc, _ = allocate_all_cycles(pd.concat([ids, oof_cal], axis=1), raw["funding_cycles"], rng)
    truth_by_app = full.set_index("application_id")["consumption_pc"]
    eval_truth = eval_alloc["application_id"].map(truth_by_app)
    print(f"  EVALUATION (out-of-fold) exclusion error, bottom decile of true need, "
          f"all cycles pooled: {exclusion_error(eval_alloc['band'], eval_truth):.3f}")
    print("  evaluation band shares: "
          + ", ".join(f"{b} {s:.0%}" for b, s in eval_alloc["band"].value_counts(normalize=True).items()))

    # --- deployed model ----------------------------------------------------
    print("Fitting deployed models on all eligible rows ...")
    models = fit_need_model(X_elig, y_e)

    print("Scoring every application (not just eligible ones) ...")
    preds = predict(models, X_all, conformal_adjustment=q_hat).reset_index(drop=True)
    shap_values = explain(models, X_all, top_n=5)
    scored = pd.concat([ids[["application_id", "household_id", "cycle_id", "amount_requested"]], preds], axis=1)
    scored["top_shap_features"] = shap_values

    print("Allocating per funding cycle and writing model_scores ...")
    final_scores, summaries = allocate_all_cycles(scored, raw["funding_cycles"], rng)
    db.write_model_scores(args.dsn, final_scores, MODEL_VERSION)
    print(f"  wrote {len(final_scores)} rows to model_scores")

    print("Band distribution:")
    print(final_scores["band"].value_counts())
    s = pd.DataFrame(summaries)
    print(f"Budget: committed (auto + audit approvals) is {s['committed'].sum() / s['budget'].sum():.0%} "
          f"of total budget; {int((s['remaining'] < 0).sum())} of {len(s)} cycles overshoot "
          f"through the mandatory audit sample")

    # --- fairness audit on the out-of-fold evaluation ----------------------
    print("Running fairness audit on the out-of-fold evaluation allocation ...")
    protected = raw["protected"].merge(
        raw["households"][["household_id", "area_code"]], on="household_id", how="left"
    ).merge(raw["area"][["area_code", "urban_rural", "region"]], on="area_code", how="left")

    # Pooled across all cycles first: that is the number to judge. Per-cycle
    # rows are kept for trend charts, but most groups fall under MIN_GROUP_N.
    audit_cols = ["application_id", "household_id", "band", *APPLICATION_AUDIT_ATTRIBUTES]
    audit_frames = [audit(eval_alloc[audit_cols], protected, eval_truth, cycle_id=None)]
    for cycle_id, cycle_alloc in eval_alloc.groupby("cycle_id"):
        audit_frames.append(audit(cycle_alloc[audit_cols], protected, eval_truth[cycle_alloc.index], cycle_id))
    audit_frames = [f for f in audit_frames if not f.empty]

    if audit_frames:
        all_audits = pd.concat(audit_frames, ignore_index=True)
        db.write_fairness_audits(args.dsn, all_audits)
        n_pooled = int(all_audits["cycle_id"].isna().sum())
        print(f"  wrote {len(all_audits)} rows to fairness_audits ({n_pooled} pooled, "
              f"{len(all_audits) - n_pooled} per cycle; groups under {MIN_GROUP_N} suppressed)")

        pooled = all_audits[all_audits["cycle_id"].isna()]
        worst = pooled.loc[pooled.groupby("attribute")["gap_vs_best"].idxmax()]
        print("\nLargest exclusion-error gap per attribute, all cycles pooled (bottom-decile true need):")
        print(worst.set_index("attribute")[["gap_vs_best", "group", "exclusion_error", "n"]]
              .sort_values("gap_vs_best", ascending=False).to_string())

    print("\nDone. Re-run v_monthly_applications in the DB to see the band columns populate.")


if __name__ == "__main__":
    main()
