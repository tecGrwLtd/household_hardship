#!/usr/bin/env python3
"""
End-to-end run of the design spec's pipeline against the (synthetic) database:

  1. Load households/surveys/area/applications/protected/awards from Postgres
  2. Build features (features.py)
  3. Train on ELIGIBLE rows only (auto_approved / audit_approved / awarded) —
     mirroring the real selective-labels constraint even though this
     synthetic set technically has consumption_pc for every row
  4. Cross-validate (GroupKFold on area_code) for honest eval metrics
  5. Fit the deployed models on all eligible rows
  6. For each funding cycle: allocate() under that cycle's real budget,
     explain() the top SHAP drivers, write model_scores back to Postgres
  7. audit() fairness across the full population (since we have ground
     truth) and write fairness_audits back to Postgres

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
    cross_validate_need_model, fit_need_model, predict, INTERVAL, conformal_adjustment,
)
from .allocate import allocate
from .audit import audit, explain

MODEL_VERSION = "lgbm-v1-synthetic"
ELIGIBLE_STATUSES = {"auto_approved", "audit_approved", "awarded"}
TARGET = "poverty_gap"  # NOT consumption_pc — see features.add_poverty_gap()
POVERTY_LINE_PERCENTILE = 0.5  # placeholder: a real deployment uses a published
                                # national/regional poverty line, not a data-derived one


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

    poverty_line = float(full["consumption_pc"].quantile(POVERTY_LINE_PERCENTILE))
    full = add_poverty_gap(full, poverty_line)
    print(f"Poverty line set at the {POVERTY_LINE_PERCENTILE:.0%} percentile of "
          f"consumption_pc = {poverty_line:,.0f} RWF/capita/month (placeholder — "
          f"replace with a published poverty line once one is agreed)")

    eligible = full[full["status"].isin(ELIGIBLE_STATUSES)].copy()
    print(f"{len(full)} total applications, {len(eligible)} eligible for training "
          f"(status in {sorted(ELIGIBLE_STATUSES)})")

    X_elig, y_elig = feature_matrix(eligible, target=TARGET)
    groups = eligible["area_code"]

    print("Cross-validating (GroupKFold on area_code, 5 folds) ...")
    oof = cross_validate_need_model(X_elig, y_elig.values, groups)
    raw_coverage = float(np.mean((y_elig.values >= oof["lo"]) & (y_elig.values <= oof["hi"])))
    rmse_mid = float(np.sqrt(np.nanmean((oof["mid"] - y_elig.values) ** 2)))
    target_coverage = INTERVAL[1] - INTERVAL[0]
    print(f"  RAW interval coverage: {raw_coverage:.3f} (target ~{target_coverage:.2f})")
    print(f"  median-model RMSE (out-of-fold): {rmse_mid:,.0f}")
    if abs(raw_coverage - target_coverage) > 0.05:
        print("  -> raw coverage is off target. This is welfare_weights() at work: "
              "reweighting the quantile loss toward the worst-off shifts what "
              "quantile 'lo'/'hi' actually estimate. Applying conformal calibration "
              "below to fix coverage without abandoning the welfare weighting.")

    # --- conformal calibration: hold out a group-disjoint slice purely to
    # measure and correct interval width, then refit on everything for the
    # models that actually go into production. ---
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=args.seed)
    train_idx, calib_idx = next(splitter.split(X_elig, y_elig, groups=groups))
    calib_models = fit_need_model(X_elig.iloc[train_idx], y_elig.values[train_idx])
    calib_preds = predict(calib_models, X_elig.iloc[calib_idx])
    q_hat = conformal_adjustment(
        calib_preds["need_lo"].values, calib_preds["need_hi"].values, y_elig.values[calib_idx],
    )
    calibrated_coverage = float(np.mean(
        (y_elig.values[calib_idx] >= calib_preds["need_lo"].values - q_hat)
        & (y_elig.values[calib_idx] <= calib_preds["need_hi"].values + q_hat)
    ))
    print(f"  conformal adjustment q_hat = {q_hat:,.0f}; calibration-split coverage "
          f"after adjustment: {calibrated_coverage:.3f}")

    print("Fitting deployed models on all eligible rows ...")
    models = fit_need_model(X_elig, y_elig.values)

    print("Scoring every application (not just eligible ones) ...")
    X_all, _ = feature_matrix(full, target=TARGET)
    preds = predict(models, X_all, conformal_adjustment=q_hat)
    scored = pd.concat([full[["application_id", "household_id", "area_code", "cycle_id",
                               "amount_requested", "status"]].reset_index(drop=True),
                         preds.reset_index(drop=True)], axis=1)

    shap_values = explain(models, X_all, top_n=5)

    print("Allocating per funding cycle and writing model_scores ...")
    all_scored_rows = []
    for cycle_id, cycle_df in scored.groupby("cycle_id"):
        budget_row = raw["funding_cycles"].loc[raw["funding_cycles"]["cycle_id"] == cycle_id]
        if budget_row.empty:
            continue
        budget = float(budget_row["budget_total"].iloc[0])
        ranked, cutoff = allocate(
            cycle_df[["application_id", "household_id", "need_lo", "need_mid", "need_hi", "amount_requested"]],
            budget, rng,
        )
        ranked["cutoff"] = cutoff
        ranked["cycle_id"] = cycle_id
        # attach top shap features by position in the original `scored` frame
        idx_map = {app_id: i for i, app_id in enumerate(scored["application_id"])}
        ranked["top_shap_features"] = ranked["application_id"].map(
            lambda a: shap_values[idx_map[a]]
        )
        all_scored_rows.append(ranked)

    final_scores = pd.concat(all_scored_rows, ignore_index=True)
    db.write_model_scores(args.dsn, final_scores, MODEL_VERSION)
    print(f"  wrote {len(final_scores)} rows to model_scores")

    print("Band distribution:")
    print(final_scores["band"].value_counts())

    print("Running fairness audit (full population, ground-truth consumption_pc) ...")
    protected = raw["protected"].merge(
        raw["households"][["household_id", "area_code"]], on="household_id", how="left"
    ).merge(raw["area"][["area_code", "urban_rural"]], on="area_code", how="left")

    audit_frames = []
    truth_by_app = full.set_index("application_id")["consumption_pc"]
    for cycle_id, cycle_scores in final_scores.groupby("cycle_id"):
        truth = cycle_scores["application_id"].map(truth_by_app)
        result = audit(cycle_scores[["application_id", "household_id", "band"]], protected, truth, cycle_id)
        if not result.empty:
            audit_frames.append(result)

    if audit_frames:
        all_audits = pd.concat(audit_frames, ignore_index=True)
        db.write_fairness_audits(args.dsn, all_audits)
        print(f"  wrote {len(all_audits)} rows to fairness_audits")

        overall = (
            all_audits.groupby("attribute")
            .apply(lambda g: pd.Series({
                "max_gap": g["gap_vs_best"].max(),
                "worst_group": g.loc[g["gap_vs_best"].idxmax(), "group"],
            }))
        )
        print("\nLargest exclusion-error gap per attribute (bottom-decile true need):")
        print(overall)

    print("\nDone. Re-run v_monthly_applications in the DB to see the band columns populate.")


if __name__ == "__main__":
    main()
