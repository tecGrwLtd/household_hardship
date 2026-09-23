# Roadmap

Plan to take the platform from "database + runnable pipeline" to a working
backend the dashboard can sit on. Checked against the design spec
(`Household hardship allocation model — design spec.docx`) and the
Greenhouses kickoff call (22 Sep 2026).

What the client asked for, in short: a simple LightGBM + GroupKFold model on
dummy data, a dashboard showing applicants per month split into
education / health / financial support, how many are repeat beneficiaries and
whether they came back within or after a year — and "do not make it too
complicated, all I want to show is that it works".

## Decisions (agreed 23 Sep 2026)

| Topic | Decision |
|---|---|
| Backend stack | FastAPI + SQLAlchemy + PostgreSQL (Python, same as the ML code) |
| Dashboard grouping | education → **Education**; medical → **Health**; rent_arrears, utilities, food, childcare, other → **Financial**; funeral → **Bereavement** |
| Repeat-support prediction model | Phase 3. v1 dashboard uses the historical repeat breakdown (`v_repeat_support`) |
| Auth | Single demo admin until the frontend exists |
| `female_headed`, `disability_in_household` | Model inputs **and** audit dimensions, as the spec states |

## Model choice

### Need model (drives allocation)

- **Algorithm:** LightGBM quantile regression — three models at the 10th / 50th / 90th percentile.
- **Target:** `poverty_gap = max(0, poverty_line − consumption_pc)` (spec option A). Higher = needier.
- **Training objective:** welfare-weighted loss (aversion 1.5) — missing a destitute household costs more than missing a borderline one.
- **Validation:** GroupKFold on `area_code`, 5 folds.
- **Intervals:** conformalized quantile regression so the 80% interval actually covers 78–82%.
- **Explanations:** SHAP top-5 drivers per application; monotone constraints on features whose direction is not in doubt.
- **Baselines it must beat:** ridge-regression PMT, and ranking by `monthly_deficit` alone.
- **Not in v1:** separate urban/rural models, selection-bias reweighting, model-vs-caseworker (contraction) evaluation.

### Repeat-support model (Phase 3, dashboard forecasting only)

LightGBM classifier for P(reapply within 365 days), calibrated; Brier score,
PR-AUC, calibration curve. **Never an input to allocation** — that would
penalise people for having needed help before.

### Metrics

| Metric | Role | Target |
|---|---|---|
| Exclusion error, bottom decile of true need | Headline | Agree before launch |
| Subgroup gap (max − min exclusion error) across ethnicity, gender of head, disability, age band, urban/rural, region, need category, application channel, referral source | Fairness gate | Agree before launch |
| Interval coverage | Calibration | 78–82% |
| Welfare-weighted pinball loss | Training objective | Beats both baselines |
| Spearman rank correlation (predicted vs true need) | Ranking quality | Supporting |
| Inclusion error | Report only | — |
| Band volumes | Sanity | auto 25–40%, review 15–30%, defer 35–55% |
| Override rate | Review is real | > 5% |
| PSI per feature, SHAP stability | Monthly drift | PSI < 0.2 |

## How the model plugs into the backend

- Training is offline (CLI): `train` → `evaluate` → writes `models/<version>/`
  (three boosters, `metadata.json` with feature list, category levels,
  conformal adjustment, poverty line, data hash; `metrics.json`).
- A `model_versions` table tracks candidate / active / retired versions.
- Every scorer implements one interface: `predict(df) → need_lo, need_mid, need_hi` (+ SHAP drivers).
  A transparent **rule-based placeholder scorer** implements it too, so the API
  and dashboard work before any model is trained.
- Scoring is two-step: a provisional need score when an application is
  submitted; final bands from a cycle-level allocation, since rank depends on
  who else applied that cycle. Deferral is never final without a human.

## Phases

- [ ] **Phase 0 — Foundations.** Point-in-time survey join; realistic survey dates in the generator; allocation edge cases + budget check; full audit dimensions on out-of-fold predictions; dashboard support-type grouping; README corrections; pytest suite.
- [ ] **Phase 1 — ML pipeline v1.** `train` / `evaluate` / `score` CLI; baselines; monotone constraints; model artifact + metadata; `model_versions` table; metrics report.
- [ ] **Phase 2 — Backend API.** FastAPI: data entry (households, surveys, applications), cycles + allocation, review queue + overrides, dashboard endpoints, model management; placeholder scorer + active-model loader; demo admin auth; Docker Compose for API + DB.
- [ ] **Phase 3 — Monitoring + repeat model.** Repeat-support classifier; monthly drift job; override-rate and fairness endpoints.
- [ ] **Phase 4 — Frontend.** Dashboard, data-entry forms, platform management, on top of the Phase 2 API.

Demo target: Phases 0–1 plus the dashboard endpoints of Phase 2.
