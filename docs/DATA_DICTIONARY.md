# Data Dictionary

Every table in `db/schema.sql`, with the notes that matter for using this
data correctly (not just what a column is called). "Model-safe" means: fine
to feed into the need-prediction model. "Audit-only" means: collect and
display it, disaggregate metrics by it, never train on it.

## `area_reference`

Area-level PMT augmentation. One row per district/area.

| Column | Type | Notes |
|---|---|---|
| `area_code` | PK | Stable join key |
| `area_name` | text | Human-readable |
| `urban_rural` | enum | `urban` / `rural` — PMT weights differ sharply between them |
| `region` | text | Province (Kigali / Northern / Southern / Eastern / Western). **Audit dimension only — never a model feature** (design spec) |
| `area_deprivation_index` | numeric | Prefer this over raw postcode/address |
| `area_poverty_rate` | numeric | Smallest geography available |
| `distance_to_services_km` | numeric | To nearest job centre / clinic / bank |
| `local_unemployment_rate` | numeric | |
| `local_housing_cost_index` | numeric | Anchors the deficit calculation |

All model-safe — these are area, not individual, attributes.

## `funding_cycles`

One row per allocation period. `budget_total` is fixed per cycle — the whole
platform is a ranking-under-budget problem, not a per-applicant eligibility
test. Two households with identical need can get different outcomes
depending on who else applied that cycle; surface that in applicant-facing
copy per the design spec.

## `caseworkers`

Retained for **contraction evaluation** (comparing outcomes across
caseworkers of different strictness on comparable applicants, which sidesteps
needing counterfactual labels) and **override-rate monitoring**. `caseworker_id`
must never be used as a model feature — it's an evaluation dimension only.

## `households`

Just identity + area. `household_id` is a UUID, generated at registration.

## `household_surveys`

The PMT survey snapshot. One row per household today (a real deployment
would retake this periodically — PMT weights drift). Model-safe unless noted.

| Column | Notes |
|---|---|
| `household_size`, `children_under_5`, `members_over_65` | Composition |
| `female_headed`, `single_caregiver` | Model-safe as *household* attributes; note `gender_head` is separately tracked in `protected_attributes` for audit — don't conflate the two uses |
| `education_head` | Ordinal enum: none < primary < secondary < vocational < tertiary |
| `literacy_head` | Drop from the feature set where universal in your population |
| `roof_material`, `wall_material`, `floor_material`, `rooms`, `tenure`, `water_source`, `sanitation_type`, `electricity`, `cooking_fuel` | PMT housing staples |
| `asset_*` (8 booleans) | Feed a first-principal-component "asset index" per the design spec, rather than 8 raw features |
| `livestock_count`, `land_area` | Rural contexts only — null for urban households by construction |
| `employment_type`, `earners_count`, `hours_worked` | Labour |
| `monthly_income`, `income_std_12m`, `income_seasonality`, `essential_costs` | Feed `monthly_deficit = essential_costs - monthly_income` — usually the strongest single predictor |
| `food_security_score` | FIES-style, 0–8, higher = more food-insecure |
| `chronic_illness`, `disability_in_household` | Presence only, no condition detail — matches the design spec's "collect presence, not diagnosis" rule |
| `dependents_requiring_care` | |
| `shock_*_12m` (7 booleans) | Sum for a `shock_count_12m` feature; compounding shocks behave non-linearly |
| `consumption_pc` | **Training target** (design spec Option A). In production this is only observed for approved/audited applicants (the selective-labels problem) — here it's filled for every row because this is synthetic ground truth. The ML pipeline in `backend/ml/` deliberately does *not* use rows outside `auto_approved`/`audit_approved`/`awarded` status for training, to mirror that real constraint even though it doesn't strictly need to on this synthetic set. |

## `protected_attributes` — AUDIT ONLY

One row per household. **Never join this table into a query that builds
model training features or a live "predicted need" display.** Its only
purpose is measuring disparate impact after the fact (`fairness_audits`
comes from disaggregating errors by these columns).

| Column | Notes |
|---|---|
| `ethnicity` | Placeholder value in synthetic data (`not_disclosed`) — real collection is a policy decision, not a data-modelling one |
| `gender_head` | audit counterpart to `household_surveys.female_headed` |
| `disability` | audit counterpart to `household_surveys.disability_in_household` |
| `age_band` | |
| `religion`, `nationality`, `immigration_status` | Immigration status may be a legal eligibility *gate* — that's a rule, never a model feature |

Deliberately **absent from this schema entirely**: substance use, offending
history, mental-health diagnosis. The design spec calls these out as
never-collect-for-scoring — the VI-SPDAT tool was phased out in 2020 over
exactly this. Don't add them even as "audit-only" columns.

## `applications`

One row per application. Model-safe fields are the application-record
variables from the design spec; a few are audit-relevant rather than
model-safe (noted below).

| Column | Notes |
|---|---|
| `amount_requested`, `stated_need_amount` | |
| `need_category` | `rent_arrears / medical / utilities / food / funeral / childcare / education / other`. `education` was added to the spec's list at the client's explicit request in the kickoff call (they described the dashboard breakdown as "education loan / health loan / financial loan"). The dashboard groups categories with `support_group()`: medical → health; food → food; rent arrears, utilities → housing & bills; education, childcare → education & childcare; funeral, other → funeral & other |
| `days_since_hardship_onset` | Delay often signals isolation, not lower need — don't read it as a negative signal |
| `prior_applications_count`, `days_since_last_application` | Power the `v_repeat_support` view |
| `referral_source` | Model it, then audit it — it encodes access to advocacy |
| `application_channel` | `online / phone / in_person / caseworker_submitted` |
| `application_completeness` | Fraction of optional fields filled |
| `documentation_provided` | **Audit for disparate impact before trusting as a model feature** — correlates with literacy/education/access in the synthetic data on purpose, mirroring a real risk the design spec flags |
| `status` | Workflow/audit state, **never a model label**. No `was_approved` column exists anywhere in this schema |
| `caseworker_id` | Never a feature — see `caseworkers` |

## `model_versions`

Registry of need-model versions. `python -m backend.ml train` adds a
`candidate`; `activate` makes it the single `active` version (a partial
unique index enforces one) and retires the previous one. A fresh database
starts with `rules-v0` — the transparent rule-based placeholder — active.

| Column | Notes |
|---|---|
| `kind` | `lgbm_quantile` (trained need model), `rules` (placeholder), `repeat_lgbm` / `repeat_history` (repeat forecaster) |
| `purpose` | `need` (drives allocation) or `repeat` (planning forecast only). One active version per purpose |
| `status` | `candidate / active / retired` |
| `artifact_path` | `models/<version>/` relative to the project root; NULL for `rules` |
| `data_hash` | sha256 of the exact training matrix — reproducibility |
| `metrics` | The full evaluation report the version was judged on, including `gates.passed` |

## `repeat_forecasts`

Probability that the household applies again within 365 days
(`p_return_1y`), per application, from the active repeat model. Written by
`python -m backend.ml forecast` and refreshed by the API when an application
is allocated or reviewed. **Planning only — never read by allocation.**
Latest row per application wins; `v_repeat_forecast` aggregates it.

## `drift_reports`

One row per monthly drift check of a need-model version
(`python -m backend.ml drift`): window, number of applications, `status`
(`ok / watch / alert`) and the full `report` (PSI per input and of the
score, SHAP stability, mean need by group, coverage on new outcomes).

## `model_scores`

Output of the need model + allocation rule, one row per application per
scoring run (`model_version` distinguishes re-runs). Written by
`python -m backend.ml score`.

| Column | Notes |
|---|---|
| `need_lo`, `need_mid`, `need_hi` | Prediction interval (10th/90th percentile, conformally widened) and the central estimate used for ranking |
| `cutoff` | The budget cutoff in effect when this was scored. NULL when the budget covered every application in the cycle (nobody deferred) |
| `band` | `auto_approve / human_review / defer / audit_approve` |
| `top_shap_features` | JSON array of `[feature, shap_value]` pairs for the top drivers of this score — this is what a caseworker-facing "why this score" explanation renders from |

## `application_reviews`

Human-in-the-loop record. Required for GDPR Art. 22 / EU AI Act Art. 14
purposes on the automated path per the design spec — the review has to be
real, with a monitored override rate (a ~1% override rate reads as a rubber
stamp, per the SCHUFA case cited in the spec).

## `awards`

Disbursement record. `outcome_followup_days`, when populated, is the seed
of a future Option B/C target (post-award outcome, or benefit-per-pound) —
mostly null in this synthetic set since only ~30% of awards have any
follow-up recorded, matching how sparse this data usually is in practice.

## `fairness_audits`

Output of the audit() step: exclusion error among the bottom-decile true-need
group, disaggregated by protected attribute and group value, with a
`gap_vs_best` column so a dashboard can flag the largest disparities directly
rather than requiring someone to eyeball a table.

Written by `python -m backend.ml train`, tagged with the candidate's
`model_version`. Computed on out-of-fold predictions (areas the model never trained on), not
the deployed model's in-sample scores. Rows with `cycle_id` NULL pool every
cycle in the run — those are the numbers to judge; per-cycle rows are for
trends only. Groups with fewer than 20 bottom-decile applicants are omitted
(`audit.MIN_GROUP_N`): too small to read, and a privacy risk.

## `platform_settings`

Programme settings an admin changes in the console, one row per key. Each row
holds a JSONB `value`, `updated_by` and `updated_at`. The API validates every
write (`backend/api/platform.py`) and logs it.

- `random_audit_rate`: share of each cycle drawn as a random audit sample.
- `override_rate_floor`: reviewer override rate below which model health is
  flagged, because reviewers may be rubber-stamping.
- `max_exclusion_error` and `max_subgroup_gap`: optional launch thresholds
  for activating a need model.
- `poverty_line`: optional override. When it is null, the line comes from the
  survey data.

## `audit_log`

An append-only activity record. Each row holds `username`, `action` (for
example `application.submit`, `household.create`, `cycle.allocate`,
`review.decide`, `model.activate`, `setting.update`, `user.update`),
`target`, JSONB `details` and `at` (timestamp). Model activations from the command
line are logged as user `command line`. Protected attributes are never
written here.

## Dashboard views — see `db/views.sql`

`v_monthly_support`, `v_monthly_applications`, `v_repeat_support`, `v_cycle_summary`, `v_repeat_outcomes`, `v_repeat_forecast` (and the `support_group()` function) — described
in the top-level README.
