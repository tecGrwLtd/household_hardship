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
| Dashboard grouping | medical → **Health**; food → **Food**; rent_arrears, utilities → **Housing & bills**; education, childcare → **Education & childcare**; funeral, other → **Funeral & other** (24 Sep; replaced education / health / financial / bereavement, where financial held 59% of applications) |
| Repeat-support prediction model | Phase 3. v1 dashboard uses the historical repeat breakdown (`v_repeat_support`) |
| Auth | Single demo admin until the frontend exists; then two roles, admin and caseworker, with different menus and views (24 Sep) |
| Frontend | React + TypeScript (Vite); English only; desktop; light and dark themes; global filters in the sidebar (24 Sep) |
| `female_headed`, `disability_in_household` | Model inputs **and** audit dimensions, as the spec states |

## Model choice

### Need model (drives allocation)

- **Algorithm:** LightGBM quantile regression — three models at the 10th / 50th / 90th percentile.
- **Target:** `poverty_gap = max(0, poverty_line − consumption_pc)` (spec option A). Higher = needier.
- **Training objective:** welfare-weighted loss (aversion 1.5) — missing a destitute household costs more than missing a borderline one.
- **Validation:** GroupKFold on `area_code`, 5 folds.
- **Intervals:** quantile models widened by cross-conformal calibration so the 80% interval actually covers 78–82%.
- **Explanations:** SHAP top-5 drivers per application; the central estimate is a monotone-constrained Huber model so need never rises with income or falls with a new shock (LightGBM cannot constrain quantile models).
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
| Spearman rank correlation among households below the poverty line | Ranking quality | Beats both baselines |
| Wrong-direction responses (income, costs, food insecurity, job loss) | Defensibility | < 5% of households |
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

- [x] **Phase 0 — Foundations.** (done 23 Sep 2026; also fixed inverted welfare weights) Point-in-time survey join; realistic survey dates in the generator; allocation edge cases + budget check; full audit dimensions on out-of-fold predictions; dashboard support-type grouping; README corrections; pytest suite.
- [x] **Phase 1 — ML pipeline v1.** (done 23 Sep 2026) `train` / `evaluate` / `activate` / `score` CLI; ridge-PMT and deficit baselines; rule-based placeholder live from day one; model artifact + metadata; `model_versions` registry with activation gates; metrics report. Changes from the plan: monotone constraints sit on a Huber central estimate (LightGBM refuses them on quantile objectives); calibration is cross-conformal; ranking is judged among the poor; `asset_index` is now fitted once and stored with the model (it used to be recomputed per batch).
- [x] **Phase 2 — Backend API.** (done 23 Sep 2026) FastAPI: data entry (households, surveys, applications, write-only protected attributes), cycles + preview + allocation against remaining budget, review queue with override tracking and appeals, dashboard endpoints, model management with gated activation; active-model loader (placeholder until a trained version is activated); demo admin auth; Docker Compose for API + DB; API integration tests. Guide: `docs/API.md`.
- [x] **Phase 3 — Monitoring + repeat model.** (done 24 Sep 2026) Repeat-support forecaster, chosen by evaluation between a regularised LightGBM and a history-only logistic regression (the latter wins on the synthetic data), calibration only where it helps, planning-only with its own registry purpose; `v_repeat_forecast` (expected vs actual returns); monthly drift report (PSI per input and score, SHAP stability, subgroup means, coverage on post-training outcomes) with a reference profile stored at training; override-rate trend; migration file for existing databases.
- [x] **Phase 4 — Frontend.** (done 24 Sep 2026) Web app on :3000 with two roles (admin: dashboard, applications, review, households, cycles, models; caseworker: my work, applications, review, households), new-application wizard, survey-wave entry, audit-only questions, allocation with preview and confirmation, gated model activation, global sidebar filters, light and dark themes. Backend additions: accounts and roles, application-level facts view behind every filtered figure, paging and search. Design: the "Hardship Platform UI" canvas.
- [x] **Phase 4b: Showcase and admin console.** (done 24 Sep 2026)
  - Dashboard: more chart types (sparklines, money line chart, outcome
    donut, region heatmap, channels, request sizes). Filters default to all
    months.
  - Model cards for every trained version: metrics against the baselines,
    fairness, drivers, settings and checks.
  - "Try the model" what-if scoring.
  - Admin console: accounts, caseworkers, validated programme settings, a
    full activity log, system status and the API catalogue.
  - Migration `003`.

Demo target: Phases 0–1 plus the dashboard endpoints of Phase 2.
