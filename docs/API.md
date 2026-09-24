# API guide

For anyone building on the API (the web app in `frontend/` uses exactly
these endpoints). The full,
always-current reference (every field, try-it-out buttons) is the
generated OpenAPI page at **http://localhost:8080/docs** once the stack is
running (`docker compose up -d --build`, see the README).

## Authentication

Accounts live in `app_users` (passwords are bcrypt hashes checked inside
Postgres). Two roles:

- **admin** — the programme manager: everything below.
- **caseworker** — data entry and review. Their applications and review
  decisions are always attributed to them, whatever the request says. Admin
  only: creating cycles, preview / allocate, `/models/*`, and
  `/dashboard/fairness | model-health | drift | override-trend` (403 otherwise).

```bash
curl -X POST localhost:8080/auth/login -d "username=admin&password=admin-dev-only"
# {"access_token": "...", "token_type": "bearer", "expires_at": "...", "user": {"role": "admin", ...}}
curl localhost:8080/auth/me -H "Authorization: Bearer <token>"
```

Send `Authorization: Bearer <access_token>` on every request. Only
`/health` and `/auth/login` are open. Tokens last 12 hours
(`HARDSHIP_TOKEN_HOURS`).

## How an application moves through the platform

```
POST /applications ──► submitted ──(POST /cycles/{id}/allocate)──┬─► auto_approved   (award created)
   returns a provisional                                         ├─► audit_approved  (award created; random 4% of deferrals)
   need estimate                                                 ├─► in_review ──► reviewer approves ─► awarded (award created)
                                                                 │                 reviewer denies ───► deferred
                                                                 └─► deferred ──(POST /applications/{id}/appeal)──► appealed
                                                                                    reviewer approves ─► awarded
                                                                                    reviewer denies ───► closed
```

- **Bands come from the cycle, not the application.** The budget is fixed
  per cycle, so where an applicant lands depends on who else applied. A
  new application only gets a *provisional* need estimate; its band is set
  when the cycle is allocated. Say so in applicant-facing copy (design
  spec).
- **The model never refuses anyone on its own.** `deferred` is not a
  refusal — it has an appeal route — and only a reviewer can close a case.
- **Allocation can be re-run.** It only processes applications still
  `submitted`, against the budget still unspent, so late applications can
  be allocated without touching earlier decisions.
- **Awards equal the amount requested.** The platform ranks and bands; it
  does not size awards (out of scope in the design spec). Approvals are
  refused (409) if they would overspend the cycle.

## Global filters

The web app's sidebar filters apply to every page. Endpoints that honour
them take the same query parameters, all optional:

| Parameter | Example | Meaning |
|---|---|---|
| `month` | `2026-08-01` | Any day in the month |
| `support_group` | `health` | `health` / `food` / `housing_bills` / `education_childcare` / `funeral_other` |
| `region` | `Kigali` | Province |
| `area_code` | `AR007` | District |
| `urban_rural` | `rural` | urban / rural |
| `mine` | `true` | Only the signed-in caseworker's applications |

`GET /dashboard/filters` lists the values available. The review queue uses
every filter except `month` (a worklist must not hide an older appeal).

## Endpoints

### Reference data (for form dropdowns)

| Method | Path | Notes |
|---|---|---|
| GET | `/areas` | Districts with urban/rural and region |
| GET | `/caseworkers` | |
| GET | `/need-categories` | Each category with its support group: medical → health; food → food; rent arrears, utilities → housing_bills; education, childcare → education_childcare; funeral, other → funeral_other |
| GET | `/cycles` | Funding cycles with application counts and totals awarded |
| POST | `/cycles` | `{period_start, period_end, budget_total}` |
| GET | `/cycles/{id}/budget` | Budget, awarded so far, remaining |

### Data entry

| Method | Path | Notes |
|---|---|---|
| POST | `/households` | `{area_code}` → `household_id` |
| GET | `/households` | `?area_code=&region=&q=&limit=&offset=` → `{total, items}` with latest survey, size, applications, awards |
| GET | `/households/{id}` | Household, its survey waves, its applications |
| POST | `/households/{id}/surveys` | One survey wave — record a new wave when circumstances change; applications are scored on the latest wave on or before their submission date |
| PUT | `/households/{id}/protected-attributes` | Ethnicity, gender of head, disability, age band, … **Write-only**: never returned per household, only as aggregate fairness figures |
| POST | `/applications` | Returns the application plus `provisional_score` (need estimate, interval, top drivers). `prior_applications_count` and `days_since_last_application` are computed by the server and rejected if sent |
| GET | `/applications` | Global filters + `status`, `q` (id, household or district), `cycle_id`, `limit`, `offset`. Returns `{total, status_counts, items}` |
| GET | `/applications/{id}` | Application with district, the survey it was scored on, latest score + explanation (or provisional estimate), reviews, award, the household's other applications, repeat forecast |
| POST | `/applications/{id}/appeal` | Deferred → appealed (back into the review queue) |

### Allocation and review

| Method | Path | Notes |
|---|---|---|
| GET | `/cycles/{id}/preview` | Dry run: bands, cutoff, budget split. Writes nothing |
| POST | `/cycles/{id}/allocate` | Commits the bands for all `submitted` applications in the cycle |
| GET | `/reviews/queue` | Global filters (not month) + `mine` (default: yes for caseworkers), `kind` (`review` / `appeal`), `cycle_id`. Returns `{counts, mine, items}`; items carry `model_lean`, `top_shap_features` and `deferred_last_year` |
| POST | `/reviews/{application_id}` | `{decision: "approve" \| "deny", notes}` — a reason is required. Records whether the reviewer overrode the model's lean |
| GET | `/me/summary` | The signed-in caseworker's open cases, applications and awards this cycle, and review record |

### Dashboard (read-only)

| Method | Path | What it answers (kickoff call) |
|---|---|---|
| GET | `/dashboard/filters` | Values for the sidebar selectors |
| GET | `/dashboard/summary` | Headline figures for the filters: applicants (and last month's), requested, awarded, helped before, expected back within a year, the month's budget (`budget_applies` false when the filters show only part of the programme) |
| GET | `/dashboard/support-types` | Per support group: applicants, helped before within / over a year, awards, expected returns |
| GET | `/dashboard/outcomes` | What happened: applications by status |
| GET | `/dashboard/monthly` | Applicants per month and support group, `months` back from the selected month |
| GET | `/dashboard/districts` | Applicants and amounts per district |
| GET | `/dashboard/trend` | Per month for `months` (default 12): applicants, requested, awarded and the amount, decided, helped before, budget. Drives the sparklines and the money chart |
| GET | `/dashboard/heatmap` | Applicants per region × support group |
| GET | `/dashboard/channels` | How people applied (`channel`) and who referred them (`referral`) |
| GET | `/dashboard/amounts` | Requests bucketed by size, with how many in each bucket were awarded |
| GET | `/dashboard/monthly-support` | "20 applicants this month → by support type"; of those, how many were helped before, within a year or more than a year ago; how many were awarded. `?start=&end=` (month dates) |
| GET | `/dashboard/monthly-applications` | Per month × need category, with the decision-band mix |
| GET | `/dashboard/repeat-support` | Totals for first-time vs repeat applicants and never / within 1 year / over 1 year helped |
| GET | `/dashboard/cycles` | Per cycle: applications, households, repeat applications, awarded vs budget |
| GET | `/dashboard/fairness` | Exclusion error among the worst-off, by group, for the active model (groups under 20 suppressed) |
| GET | `/dashboard/model-health` | Active model and its evaluation gates, band mix, reviewer override rate (flagged if under 5%) |
| GET | `/dashboard/repeat-forecast` | "How many will need help again within a year?" — per month and support group: applicants, expected returns (sum of forecasts), and for months over a year old, actual returns. Planning only |
| GET | `/dashboard/override-trend` | Per month: reviews, override rate, approval rate |
| GET | `/dashboard/drift` | Latest monthly drift report for the active need model (status ok / watch / alert, shifted inputs, score shift, driver stability) and recent report statuses |

### Model management

| Method | Path | Notes |
|---|---|---|
| GET | `/models` | All versions with status and whether their gates passed |
| GET | `/models/active` | `?purpose=need` (default, allocation) or `repeat` (planning forecast) — one active model per purpose |
| GET | `/models/{version}` | Full evaluation report |
| POST | `/models/{version}/activate` | `{force?, reason?}` — replaces the active model of the same purpose only. Refused (409) if the version's gates failed, unless forced with a reason, which is recorded |
| GET | `/models/{version}/card` | Everything needed to present a version: description, training facts, evaluation against the baselines, feature importance (mean \|SHAP\|, coefficients or rule terms), settings and launch checks |
| POST | `/models/what-if` | Score an imagined household (`version` optional, defaults to the active need model). Returns the estimate, range, top reasons, and where it would have landed in the latest allocated cycle. Nothing is stored |

All model endpoints are admin-only.

### Admin console (admin only)

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/admin/users` | List accounts; create one `{username, password, display_name, role, caseworker_id?}` |
| PATCH | `/admin/users/{id}` | Change display name, role, linked caseworker, `active`, or reset the password. You cannot deactivate or demote yourself, nor the last active admin |
| GET / POST | `/admin/caseworkers` | Caseworkers with workload (open reviews, decisions, override rate); add one |
| PATCH | `/admin/caseworkers/{id}` | Rename, reassign region, (de)activate |
| GET | `/admin/settings` | Programme settings with their current value, description and allowed range |
| PUT | `/admin/settings/{key}` | `{value}`. Validated: `random_audit_rate` 0.03–0.05 (the spec's 3–5%), `override_rate_floor` 0–0.5, `max_exclusion_error` / `max_subgroup_gap` 0–1 or null, `poverty_line` positive or null (null = from the survey data). Takes effect on the next allocation, model health check or training run |
| GET | `/admin/audit-log` | `?action=&limit=&offset=`: sign-ins, data entry, allocations, reviews, activations, setting and account changes. Protected attributes are never written to it |
| GET | `/admin/system` | Row counts, active models, database version and size, last drift report, recent activity, and whether development secrets are still in use |
| POST | `/auth/password` | Any signed-in user: `{current_password, new_password}` |

Training, forecasting and drift reports are not API calls: `python -m
backend.ml train | train-repeat | forecast | drift` (see
`backend/ml/README.md`). Allocation and review decisions refresh the repeat
forecasts of the applications they touch automatically.

## Errors

Standard FastAPI shape: `{"detail": "..."}`. 401 = missing or expired
token; 404 = unknown id; 409 = the action conflicts with the current state
(wrong status, over budget, gates failed); 422 = invalid input (the detail
says which field).
