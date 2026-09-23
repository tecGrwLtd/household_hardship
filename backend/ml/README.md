# Backend / ML pipeline

Runnable version of the design spec's modelling pipeline, validated against
the synthetic dataset in `db/seed/`.

## Run it

```bash
cd hardship-platform
pip install -r requirements.txt

python -m backend.ml evaluate            # model vs baselines, nothing saved
python -m backend.ml train               # evaluate + fit + save models/<version>/ + register a candidate
python -m backend.ml activate <version>  # make it live (refused if its gates failed, unless --force)
python -m backend.ml score               # score + allocate with the live model, write model_scores
python -m backend.ml models              # list registered versions
```

Database: `--dsn`, else `$HARDSHIP_DSN`, else the local docker-compose
database. Tests: `pytest` (no database needed).

A fresh database starts with the **rule-based placeholder** (`rules-v0`)
active, so `score` — and anything built on `model_scores` — works before any
model is trained. `train` then `activate` replaces it.

## Model lifecycle

```
evaluate ──► train ──► models/<version>/            model_versions table
               │        ├ lo.txt, mid.txt, hi.txt     candidate ──activate──► active ──► retired
               │        ├ metadata.json  (features, asset-index loadings, conformal q,
               │        │                 poverty line, data hash, git commit, params)
               │        └ metrics.json   (full evaluation report + gates)
               └──► fairness_audits (tagged with the version)
score ──► model_scores (tagged with the version)
```

Trained artifacts are not committed (`models/` is git-ignored); rebuild
them with `train`.

## Module map

| File | What it does |
|---|---|
| `features.py` | Point-in-time survey join, the spec's derived features (row-wise), `AssetIndex` (PMT asset index, fitted once and stored), poverty-gap target |
| `model.py` | `LGBMNeedModel`, welfare weights, out-of-fold prediction, cross-conformal calibration |
| `baselines.py` | `RidgePMTModel` and `DeficitRankModel` (what the model must beat), `RuleBasedModel` (the placeholder) |
| `allocate.py` | Deterministic allocation under budget — no learned parameters |
| `metrics.py` | The spec's evaluation metrics, plus the expected-direction check |
| `evaluate.py` | Side-by-side out-of-fold evaluation of all candidates, activation gates |
| `audit.py` | Disaggregated fairness audit across the spec's nine dimensions |
| `registry.py` | Model artifacts on disk |
| `db.py` | Postgres reads/writes, model registry |
| `__main__.py` | The CLI above |

Every model implements one interface — `fit(features, y)`,
`predict(features) → need_lo / need_mid / need_hi`, `explain(features)` —
so evaluation, scoring and the API never care which one they hold.

## The model

| | Choice | Why |
|---|---|---|
| Target | `poverty_gap = max(0, poverty_line − consumption_pc)` | Higher = needier (see 1 below) |
| Interval | LightGBM quantile models at the 10th / 90th percentile, widened by a cross-conformal scalar | Sizes the human-review band; 80% coverage |
| Central estimate (`need_mid`, ranks applicants) | LightGBM Huber regression with monotone constraints | See 4 below |
| Weights | `welfare_weights()`: errors on the needier cost more | The spec's central ethical choice |
| Validation | GroupKFold on `area_code` | Households cluster by area |
| Explanations | Exact TreeSHAP contributions of the central estimate (LightGBM `pred_contrib`) | Per-decision drivers, stored in `model_scores.top_shap_features` |

### Evaluation (synthetic data, out of fold)

| | LightGBM | Ridge PMT | Deficit only | Placeholder |
|---|---|---|---|---|
| Exclusion error, bottom decile (headline) | **0.0%** | 0.3% | 60% | 42% |
| Inclusion error | **10%** | 20% | 31% | 41% |
| Rank correlation among the poor | **0.955** | 0.923 | 0.404 | 0.275 |
| Welfare-weighted pinball loss | **214** | 364 | – | – |
| Interval coverage (target 78–82%) | 80.0% | 80.5% | – | – |
| Bands: auto / review / defer | 16 / 17 / 64% | 8 / 31 / 58% | 14 / 0 / 82% | 5 / 25 / 68% |
| Wrong-direction responses (max) | 0.4% | – | – | – |

Synthetic data is generated from a clean formula, so these numbers are a
pipeline check, not a forecast of real-world accuracy.

### Activation gates (`evaluate.gates`)

A version is activated without `--force` only if it: covers 78–82%; has
exclusion error no worse than either baseline; ranks the poor better than
both; beats ridge on welfare-weighted loss; and moves the wrong way for
under 5% of households when income, costs, food insecurity or a job-loss
shock change. The spec leaves the headline exclusion-error and subgroup-gap
thresholds to be agreed before launch — add them to `gates()` then.

## Things worth knowing before you build on this

**1. The training target is `poverty_gap`, not raw `consumption_pc`.**
`consumption_pc` is a welfare measure where *lower* means needier.
`allocate()` ranks applicants by predicted need, descending, and awards to
the top of that ranking — so training directly on `consumption_pc` and
ranking descending silently prioritises the *least* needy applicants. An
earlier pass at this pipeline did exactly that; it surfaced only because
`audit()` was run against ground truth. The poverty line
(`POVERTY_LINE_PERCENTILE` in `__main__.py`, currently the median) is a
placeholder — swap in a published national/regional line.

**2. `welfare_weights()` must weight by the same direction as the target.**
The spec's skeleton computes `(1 - rank) ** aversion` — correct for
`consumption_pc`. After the switch to `poverty_gap` nobody flipped it, so
the model weighted errors on the *least* needy most heavily: the same sign
trap one layer down. It now weights by `rank(poverty_gap) ** aversion`, and
`tests/test_model.py` pins the direction.

**3. Raw interval coverage is ~48% against an 80% target, and that's the
welfare weighting working, not a bug.** Weighting the quantile loss toward
the worst-off shifts what the lo/hi models estimate.
`model.cross_conformal()` restores coverage with one scalar fitted on the
out-of-fold predictions of all 30 areas, and reports an honest coverage in
which each fold is widened by a scalar fitted on the other folds. Re-check
every cycle in production.

**4. The central estimate is monotone-constrained — and that is why it is a
Huber model, not the 50th-percentile quantile model.** Unconstrained, the
median model gave a *higher* need score to 11% of households when their
income rose and a *lower* one to 13% when they lost a job — tree noise that
swung between 0% and 26% with the random seed. LightGBM refuses monotone
constraints with the quantile and L1 objectives, so the central estimate
uses the Huber objective (median-like beyond 0.2 standard deviations of the
target) with constraints on income, costs, deficit, precarity, food
insecurity and shocks (`MONOTONE_CONSTRAINTS`). It ranks the poor better
than the unconstrained median did (0.955 vs 0.947). The lo/hi interval
models stay unconstrained quantile models; they only size the review band.
`metrics.direction_violations()` checks the directions hold end to end.

**5. `asset_index` is fitted once and stored with the model.** It is a
principal component; recomputing it on whatever rows are being scored
changed its scale batch to batch and made it zero for a single application.
`features.AssetIndex` now holds training-time loadings, saved in
`metadata.json`, and a single application scores exactly as it does inside
a batch (tested).

**6. Fairness numbers come from out-of-fold predictions, pooled across
cycles.** With ~30 bottom-decile applicants a month, nearly every subgroup
is below `audit.MIN_GROUP_N` (20) per cycle and is suppressed — judge the
pooled rows (`cycle_id` NULL).

**7. Ranking is judged among households below the poverty line.** Above it,
every true gap is 0, so rank correlation there measures nothing about who
should be funded first (`spearman_all` is still reported).

## Known limitation of the synthetic data (not the pipeline)

The synthetic applicant-generation process lets some genuinely non-poor
households apply (self-selection isn't modelled), so half of all
applications have a true poverty gap of zero. Combined with a tight budget,
this pushes the `defer` band above the design spec's "expect 35–55%"
planning range. That's a property of the demo data, not the allocation
logic.

## What's NOT implemented from the spec (left for later phases)

- `selection_weights()` (covariate-shift correction for selective labels) is
  written but not wired in — this dataset has outcomes for every row. Wire it
  in once real data only has outcomes for approved/audited applicants.
- Contraction evaluation (caseworkers of different strictness at matched
  approval rates) — the synthetic data varies caseworker strictness on
  purpose, but no code does it yet.
- Separate urban/rural models vs one pooled model.
- Monthly drift checks (PSI per feature, SHAP stability) — Phase 3.
