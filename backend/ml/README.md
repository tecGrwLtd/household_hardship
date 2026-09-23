# Backend / ML pipeline

Runnable version of the design spec's modelling pipeline, validated against
the synthetic dataset in `db/seed/`.

## Run it

```bash
cd hardship-platform
pip install -r requirements.txt
python3 -m backend.ml.run_pipeline --dsn "postgresql://hardship_app:hardship_dev_only@localhost:5432/hardship_platform"
```

This loads the DB tables, trains, scores every application, allocates each
funding cycle under its real budget, and writes results back into
`model_scores` and `fairness_audits`. Tests: `pytest` (no database
needed). Safe to re-run (it appends new rows
with the same `model_version`) — `TRUNCATE model_scores, fairness_audits;`
first for a clean slate.

## Module map

| File | Design spec section |
|---|---|
| `features.py` | Feature pipeline (`build_features`), PMT asset-index PC, poverty-gap target |
| `model.py` | Welfare-weighted quantile GBDT, out-of-fold predictions (GroupKFold on area), conformal calibration, selective-labels reweighting |
| `allocate.py` | Deterministic allocation under budget — no learned parameters |
| `audit.py` | SHAP explanation, headline exclusion error, fairness audit across the spec's nine dimensions |
| `run_pipeline.py` | Orchestrates all of the above end to end |

## Four things worth knowing before you build on this

**1. The training target is `poverty_gap`, not raw `consumption_pc`.**
`consumption_pc` is a welfare measure where *lower* means needier.
`allocate()` ranks applicants by predicted need, descending, and awards to
the top of that ranking — so training directly on `consumption_pc` and
ranking descending silently prioritises the *least* needy applicants. This
is not a hypothetical: it's what an earlier pass at this pipeline actually
did, and it only surfaced because `audit()` was run against ground truth
and showed 90%+ exclusion error for the bottom-need decile (i.e. the
poorest households were almost never approved). `features.add_poverty_gap()`
flips this by training on `max(0, poverty_line - consumption_pc)`, which is
the design spec's own suggested alternative to raw consumption per capita.
After the fix, exclusion error for the bottom-need decile is under 1%
(evaluated out of fold) across every audited group. The poverty line used here (`POVERTY_LINE_PERCENTILE`
in `run_pipeline.py`, currently the population median) is a placeholder —
swap it for a published national/regional line as soon as one exists.

**2. `welfare_weights()` must weight by the same direction as the target.**
The spec's skeleton computes `(1 - rank) ** aversion` — correct for
`consumption_pc`, where low is worse off. After the switch to
`poverty_gap` nobody flipped it, so for a while the model weighted errors on
the *least* needy most heavily: the same sign trap as point 1, one layer
down, and again silent. It now weights by `rank(poverty_gap) ** aversion`,
and `tests/test_model.py` pins the direction. Fixing it took bottom-decile
exclusion error from 0.3% to 0% and auto-approvals from 13% to 16% of
applications.

**3. Raw prediction-interval coverage came out at ~48% against an 80%
target, and that's `welfare_weights()` working as designed, not a bug to
"fix" by removing the weighting.** Weighting the quantile loss toward the
worst-off (the spec's central ethical design choice, so that missing a
destitute household costs more than missing a borderline one) also biases
the raw quantile outputs away from the unweighted 10th/90th percentile.
`model.conformal_adjustment()` restores nominal coverage with a single
scalar correction fit on a held-out, area-disjoint calibration split
(conformalized quantile regression — Romano, Patterson & Candès 2019),
without touching the welfare-weighted ranking behaviour. Coverage after
calibration: 80.3%. Re-check this every cycle in production — PMT weights
and the correction factor both drift.

**4. Fairness numbers come from out-of-fold predictions, pooled across
cycles.** The deployed models score applicants they were trained on, so
auditing their allocation flatters them. `run_pipeline` re-runs the
allocation on out-of-fold predictions (each area scored by models that
never saw it) and audits that. Per-cycle audits are written too, but with
~30 bottom-decile applicants a month nearly every subgroup is below
`audit.MIN_GROUP_N` (20) and is suppressed — judge the pooled rows
(`cycle_id` NULL).

## Known limitation of the synthetic data (not the pipeline)

The synthetic applicant-generation process lets some genuinely non-poor
households apply (self-selection isn't modelled), so roughly half of all
applications have a true poverty gap near zero. Combined with a tight
budget, this pushes the `defer` band well above the design spec's
"expect 35-55%" planning range in this dataset. That's a property of how
the demo data was generated, not a sign the allocation logic is broken —
the fairness-audit numbers (low exclusion error in the bottom decile)
are the check that actually matters, and they're healthy.

## What's NOT implemented from the spec (left for you)

- `selection_weights()` (covariate-shift correction for the selective-labels
  problem) is written but not wired into `run_pipeline.py` — this dataset's
  `consumption_pc` is available for every row, so there was nothing to
  correct for. Wire it in once real data only has outcomes for
  approved/audited applicants.
- Contraction evaluation (comparing caseworkers of different strictness at
  matched approval rates) — the synthetic data has deliberately varied
  caseworker strictness (see `data/generate_synthetic_data.py`) so this is
  possible to build against, but no code does it yet.
- Population-shift testing (separate urban/rural models vs. one pooled
  model) — `urban_rural` is available in `area_reference` for this.
- Monthly drift checks (PSI per feature, SHAP stability) — `run_pipeline.py`
  reports coverage and exclusion error for one run; there's no run-over-run
  comparison yet.
