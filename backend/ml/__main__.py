"""Command line for the need-model lifecycle.

    python -m backend.ml evaluate            compare the model with its baselines; saves nothing
    python -m backend.ml train               evaluate, fit on all labelled rows, save models/<version>/,
                                             register it as a candidate, write its fairness audit
    python -m backend.ml activate VERSION    make VERSION the live model (refused if its gates failed,
                                             unless --force)
    python -m backend.ml score               score + allocate applications with the active model
                                             (or --version), write model_scores
    python -m backend.ml models              list registered versions

    python -m backend.ml train-repeat        evaluate + fit the repeat-support forecaster (planning only)
    python -m backend.ml forecast            write repeat forecasts with the active repeat model
    python -m backend.ml drift               drift report for the active need model (run monthly)

Database: --dsn, else $HARDSHIP_DSN, else the local docker-compose database.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

from . import db, drift, registry, repeat
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
        # what `drift` compares later applicants against
        "reference": drift.reference_profile(model, full),
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
    print(f"'{args.version}' is now the active {row['purpose']} model" + (f" (retired '{prev}')" if prev else "") + ".")
    print("Next:  python -m backend.ml " + ("forecast" if row["purpose"] == "repeat" else "score"))


def cmd_score(args) -> None:
    row = db.get_model_version(args.dsn, args.version)
    if row["purpose"] != "need":
        sys.exit(f"'{row['model_version']}' is a {row['purpose']} model; `score` needs a need model")
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


def cmd_train_repeat(args) -> None:
    ds = load_dataset(args.dsn)
    full = ds["full"]
    labels = full[["application_id"]].merge(repeat.repeat_labels(full), on="application_id")
    obs = labels["observable"].values
    feats, y = full[obs].reset_index(drop=True), labels.loc[obs, "returned"].values
    print(f"Repeat forecast: {int(obs.sum())} applications with a full {repeat.REPEAT_HORIZON_DAYS}-day "
          f"follow-up; {y.mean():.0%} of those households applied again within the horizon")

    report = repeat.evaluate_repeat(feats, y, full.loc[obs, "area_code"].values)
    print("\n" + pd.DataFrame({k: {m: v for m, v in r.items() if m != "calibration"}
                                for k, r in report["models"].items()}).T.to_string(float_format=lambda v: f"{v:.4f}"))
    print(f"  base-rate Brier: {report['brier_base_rate']:.4f}; chosen: {report['best']} (lowest Brier)")
    print("  gates: " + ", ".join(f"{k} {'ok' if v else 'FAILED'}" for k, v in report["gates"].items() if k != "passed"))

    best = report["best"]
    model = repeat.REPEAT_CANDIDATES[best]().fit(feats, y)
    if report["models"][best]["calibrated"]:
        model.calibrate(report["oof_raw"][best], y)
    version = args.version or registry.new_version("repeat")
    metrics = {k: v for k, v in report.items() if k != "oof_raw"}
    metadata = {"trained_at": pd.Timestamp.now(tz="UTC").isoformat(), "training_rows": int(obs.sum()),
                "git_commit": registry.git_commit(), "seed": args.seed}
    path = registry.save(model, version, metadata, metrics)
    db.register_model_version(args.dsn, version, model.kind, registry.relative_to_project(path),
                              int(obs.sum()), None, metrics, purpose="repeat")
    print(f"Saved {path}; registered '{version}' as a candidate "
          f"({'gates PASSED' if report['gates']['passed'] else 'gates FAILED'}).")
    print(f"Make it live with:  python -m backend.ml activate {version}")


def cmd_forecast(args) -> None:
    row = db.get_model_version(args.dsn, args.version, purpose="repeat")
    model = registry.load(row["kind"], row["artifact_path"])
    raw = db.load_raw_tables(args.dsn)
    feats = build_features(raw["households"], raw["surveys"], raw["applications"], raw["area"])
    p = model.predict_proba(feats)
    db.write_repeat_forecasts(args.dsn, feats["application_id"], p, row["model_version"])
    print(f"Wrote {len(p)} repeat forecasts with '{row['model_version']}': "
          f"on average {p.mean():.0%} expected to apply again within a year")


def cmd_drift(args) -> None:
    row = db.get_model_version(args.dsn, purpose="need")
    if row["kind"] == "rules":
        sys.exit("The active need model is the rule-based placeholder; drift is checked for trained models.")
    metadata = registry.read_metadata(row["artifact_path"])
    if "reference" not in metadata:
        sys.exit(f"'{row['model_version']}' was trained before drift references were stored; retrain it.")
    model = registry.load(row["kind"], row["artifact_path"])

    raw = db.load_raw_tables(args.dsn)
    feats = build_features(raw["households"], raw["surveys"], raw["applications"], raw["area"])
    submitted = pd.to_datetime(feats["submitted_at"], utc=True).dt.tz_localize(None).dt.normalize()
    end = pd.Timestamp(args.end) if args.end else submitted.max()
    start = pd.Timestamp(args.start) if args.start else end - pd.Timedelta(days=args.days - 1)
    window = feats[(submitted >= start) & (submitted <= end)].reset_index(drop=True)
    if window.empty:
        sys.exit(f"No applications between {start.date()} and {end.date()}")

    groups = (window[["household_id", "area_code"]]
              .merge(raw["protected"][["household_id", "gender_head", "disability", "age_band"]],
                     on="household_id", how="left")
              .merge(raw["area"][["area_code", "urban_rural", "region"]], on="area_code", how="left")
              .drop(columns=["household_id", "area_code"]))
    # Outcomes are only known for approved/audited applicants (selective
    # labels), and only those submitted after training say anything new —
    # earlier ones were in the training set, so their coverage is in-sample.
    trained_at = pd.Timestamp(metadata["trained_at"]).tz_convert(None)
    known = (window["status"].isin(ELIGIBLE_STATUSES) & window["consumption_pc"].notna()
             & (pd.to_datetime(window["submitted_at"], utc=True).dt.tz_localize(None) > trained_at))
    truth = (metadata["poverty_line"] - window["consumption_pc"]).clip(lower=0).where(known)

    report = drift.drift_report(model, metadata["reference"], window, groups, truth)
    report_id = db.write_drift_report(args.dsn, row["model_version"], start.date(), end.date(), report)
    print(f"Drift report #{report_id} for '{row['model_version']}', {start.date()} to {end.date()} "
          f"({report['applications']} applications): {report['status'].upper()}")
    print(f"  score PSI {report['score_psi']:.3f}; SHAP top-{drift.SHAP_TOP} overlap "
          f"{report['shap_stability']['overlap']:.0%}; coverage {report['interval_coverage']}")
    for f in report["features_shifted"][:10]:
        print(f"  {f['level']:5s} {f['feature']}: PSI {f['psi']:.3f}")


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
    p = sub.add_parser("train-repeat", help="evaluate, fit and register a repeat-support forecaster")
    p.add_argument("--version", help="version name (default repeat-<utc timestamp>)")
    p.set_defaults(func=cmd_train_repeat)
    p = sub.add_parser("forecast", help="write repeat forecasts with the active repeat model")
    p.add_argument("--version", help="forecast with this version instead of the active one")
    p.set_defaults(func=cmd_forecast)
    p = sub.add_parser("drift", help="drift report for the active need model")
    p.add_argument("--start", help="window start date (default: --days before --end)")
    p.add_argument("--end", help="window end date (default: latest application)")
    p.add_argument("--days", type=int, default=30, help="window length when --start is not given")
    p.set_defaults(func=cmd_drift)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
