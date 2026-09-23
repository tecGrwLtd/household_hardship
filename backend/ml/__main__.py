"""Command line for the need-model lifecycle.

    python -m backend.ml evaluate            compare the model with its baselines; saves nothing
    python -m backend.ml train               evaluate, fit on all labelled rows, save models/<version>/,
                                             register it as a candidate, write its fairness audit
    python -m backend.ml activate VERSION    make VERSION the live model (refused if its gates failed,
                                             unless --force)
    python -m backend.ml score               score + allocate applications with the active model
                                             (or --version), write model_scores
    python -m backend.ml models              list registered versions

Database: --dsn, else $HARDSHIP_DSN, else the local docker-compose database.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

from . import db, registry
from .allocate import allocate_all_cycles
from .evaluate import PRIMARY, evaluate, format_report
from .features import add_poverty_gap, build_features, feature_matrix
from .model import INTERVAL

DEFAULT_DSN = "postgresql://hardship_app:hardship_dev_only@localhost:5432/hardship_platform"
TARGET = "poverty_gap"  # NOT consumption_pc — see features.add_poverty_gap()
ELIGIBLE_STATUSES = ("auto_approved", "audit_approved", "awarded")
POVERTY_LINE_PERCENTILE = 0.5  # placeholder: a real deployment uses a published
                                # national/regional poverty line, not a data-derived one


def load_dataset(dsn: str) -> dict:
    raw = db.load_raw_tables(dsn)
    full = build_features(raw["households"], raw["surveys"], raw["applications"], raw["area"])
    poverty_line = float(full["consumption_pc"].quantile(POVERTY_LINE_PERCENTILE))
    full = add_poverty_gap(full, poverty_line)
    # Selective labels: outcomes exist only for approved/audited applicants in
    # a real deployment, so only those rows are trained on — even though this
    # synthetic set has consumption_pc for everyone.
    labelled = full["status"].isin(ELIGIBLE_STATUSES).values
    protected = (raw["protected"]
                 .merge(raw["households"][["household_id", "area_code"]], on="household_id", how="left")
                 .merge(raw["area"][["area_code", "urban_rural", "region"]], on="area_code", how="left"))
    n_no_survey = int(full["survey_date"].isna().sum())
    print(f"{len(full)} applications, {int(labelled.sum())} labelled for training "
          f"(status in {list(ELIGIBLE_STATUSES)}); {n_no_survey} without a survey on or before submission")
    print(f"Poverty line: {poverty_line:,.0f} RWF/person/month ({POVERTY_LINE_PERCENTILE:.0%} percentile "
          f"of consumption_pc — placeholder until a published line is agreed)")
    return {"raw": raw, "full": full, "labelled": labelled, "protected": protected, "poverty_line": poverty_line}


def cmd_evaluate(args) -> dict:
    ds = load_dataset(args.dsn)
    print("Evaluating out of fold (GroupKFold on area_code) — this fits every candidate 5+1 times ...")
    report = evaluate(ds["full"], TARGET, ds["labelled"], ds["raw"]["funding_cycles"], ds["protected"], args.seed)
    print("\n" + format_report(report))
    return {"ds": ds, "report": report}


def cmd_train(args) -> None:
    ev = cmd_evaluate(args)
    ds, report = ev["ds"], ev["report"]
    full, labelled = ds["full"], ds["labelled"]

    model = report["primary_model"]   # fitted on all labelled rows during evaluation

    X, y = feature_matrix(full[labelled], target=TARGET)
    version = args.version or registry.new_version("lgbm")
    fairness = report["fairness"]
    metrics = {
        "models": report["models"],
        "gates": report["gates"],
        "fairness_pooled": fairness[fairness["cycle_id"].isna()].drop(columns="cycle_id").to_dict("records")
        if len(fairness) else [],
    }
    metadata = {
        "target": TARGET,
        "interval": list(INTERVAL),
        "poverty_line": ds["poverty_line"],
        "poverty_line_percentile": POVERTY_LINE_PERCENTILE,
        "eligible_statuses": list(ELIGIBLE_STATUSES),
        "training_rows": int(labelled.sum()),
        "data_hash": registry.data_hash(X, y),
        "trained_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "git_commit": registry.git_commit(),
        "seed": args.seed,
    }
    path = registry.save(model, version, metadata, metrics)
    print(f"Saved {path}")

    db.register_model_version(args.dsn, version, model.kind, registry.relative_to_project(path),
                              metadata["training_rows"], metadata["data_hash"], metrics)
    if len(fairness):
        db.write_fairness_audits(args.dsn, fairness, version)
    print(f"Registered '{version}' as a candidate ({'gates PASSED' if report['gates']['passed'] else 'gates FAILED'}).")
    print(f"Make it live with:  python -m backend.ml activate {version}")


def cmd_activate(args) -> None:
    row = db.get_model_version(args.dsn, args.version)
    gates = (row.get("metrics") or {}).get("gates", {})
    if row["kind"] != "rules" and not gates.get("passed") and not args.force:
        failed = [k for k, v in gates.items() if v is False]
        sys.exit(f"Refusing to activate '{args.version}': evaluation gates failed {failed}. "
                 f"Use --force to override (and record why).")
    prev = db.activate_model_version(args.dsn, args.version)
    print(f"'{args.version}' is now active" + (f" (retired '{prev}')" if prev else "") + ".")
    print("Re-score applications with:  python -m backend.ml score")


def cmd_score(args) -> None:
    row = db.get_model_version(args.dsn, args.version)
    model = registry.load(row["kind"], row["artifact_path"])
    version = row["model_version"]
    print(f"Scoring with '{version}' ({row['kind']}, {row['status']})")

    raw = db.load_raw_tables(args.dsn)
    feats = build_features(raw["households"], raw["surveys"], raw["applications"], raw["area"])
    if args.cycle is not None:
        feats = feats[feats["cycle_id"] == args.cycle].reset_index(drop=True)
        if feats.empty:
            sys.exit(f"No applications in cycle {args.cycle}")

    scored = pd.concat([feats[["application_id", "household_id", "cycle_id", "amount_requested"]],
                        model.predict(feats)], axis=1)
    scored["top_shap_features"] = model.explain(feats, top_n=5)
    ranked, summaries = allocate_all_cycles(scored, raw["funding_cycles"], np.random.default_rng(args.seed))
    db.write_model_scores(args.dsn, ranked, version)

    print(f"Wrote {len(ranked)} rows to model_scores across {ranked['cycle_id'].nunique()} cycle(s)")
    print(ranked["band"].value_counts(normalize=True).rename("share").to_string(float_format=lambda v: f"{v:.0%}"))
    s = pd.DataFrame(summaries)
    print(f"Committed (auto + audit approvals): {s['committed'].sum() / s['budget'].sum():.0%} of budget; "
          f"{int((s['remaining'] < 0).sum())} of {len(s)} cycles overshoot through the mandatory audit sample")


def cmd_models(args) -> None:
    print(db.list_model_versions(args.dsn).to_string(index=False))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m backend.ml", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=os.environ.get("HARDSHIP_DSN", DEFAULT_DSN))
    ap.add_argument("--seed", type=int, default=7)
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("evaluate", help="compare the model with its baselines").set_defaults(func=cmd_evaluate)
    p = sub.add_parser("train", help="evaluate, fit, save and register a candidate")
    p.add_argument("--version", help="version name (default lgbm-<utc timestamp>)")
    p.set_defaults(func=cmd_train)
    p = sub.add_parser("activate", help="make a version the live model")
    p.add_argument("version")
    p.add_argument("--force", action="store_true", help="activate even if evaluation gates failed")
    p.set_defaults(func=cmd_activate)
    p = sub.add_parser("score", help="score and allocate applications, write model_scores")
    p.add_argument("--version", help="score with this version instead of the active one")
    p.add_argument("--cycle", type=int, help="only this funding cycle")
    p.set_defaults(func=cmd_score)
    sub.add_parser("models", help="list registered versions").set_defaults(func=cmd_models)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
