# API guide

For whoever builds the dashboard and the data-entry screens. The full,
always-current reference (every field, try-it-out buttons) is the
generated OpenAPI page at **http://localhost:8080/docs** once the stack is
running (`docker compose up -d --build`, see the README).

## Authentication

One demo admin for now (real accounts come with the frontend).

```bash
curl -X POST localhost:8080/auth/login -d "username=admin&password=admin-dev-only"
# {"access_token": "...", "token_type": "bearer", "expires_at": "..."}
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

## Endpoints

### Reference data (for form dropdowns)

| Method | Path | Notes |
|---|---|---|
| GET | `/areas` | Districts with urban/rural and region |
| GET | `/caseworkers` | |
| GET | `/need-categories` | Each category with its support group (education / health / financial / bereavement) |
| GET | `/cycles` | Funding cycles with application counts and totals awarded |
| POST | `/cycles` | `{period_start, period_end, budget_total}` |
| GET | `/cycles/{id}/budget` | Budget, awarded so far, remaining |

### Data entry

| Method | Path | Notes |
|---|---|---|
| POST | `/households` | `{area_code}` → `household_id` |
| GET | `/households` | `?area_code=&limit=&offset=` |
| GET | `/households/{id}` | Household, its survey waves, its applications |
| POST | `/households/{id}/surveys` | One survey wave — record a new wave when circumstances change; applications are scored on the latest wave on or before their submission date |
| PUT | `/households/{id}/protected-attributes` | Ethnicity, gender of head, disability, age band, … **Write-only**: never returned per household, only as aggregate fairness figures |
| POST | `/applications` | Returns the application plus `provisional_score` (need estimate, interval, top drivers). `prior_applications_count` and `days_since_last_application` are computed by the server and rejected if sent |
| GET | `/applications` | `?cycle_id=&status=&household_id=&limit=&offset=` |
| GET | `/applications/{id}` | Application, latest score + explanation (or provisional estimate), reviews, award |
| POST | `/applications/{id}/appeal` | Deferred → appealed (back into the review queue) |

### Allocation and review

| Method | Path | Notes |
|---|---|---|
| GET | `/cycles/{id}/preview` | Dry run: bands, cutoff, budget split. Writes nothing |
| POST | `/cycles/{id}/allocate` | Commits the bands for all `submitted` applications in the cycle |
| GET | `/reviews/queue` | `?cycle_id=` — cases waiting for a person, neediest first, with `model_lean` and `top_shap_features` |
| POST | `/reviews/{application_id}` | `{decision: "approve" \| "deny", caseworker_id?, notes?}`. Records whether the reviewer overrode the model's lean |

### Dashboard (read-only)

| Method | Path | What it answers (kickoff call) |
|---|---|---|
| GET | `/dashboard/monthly-support` | "20 applicants this month → education / health / financial"; of those, how many were helped before, within a year or more than a year ago; how many were awarded. `?start=&end=` (month dates) |
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

Training, forecasting and drift reports are not API calls: `python -m
backend.ml train | train-repeat | forecast | drift` (see
`backend/ml/README.md`). Allocation and review decisions refresh the repeat
forecasts of the applications they touch automatically.

## Errors

Standard FastAPI shape: `{"detail": "..."}`. 401 = missing or expired
token; 404 = unknown id; 409 = the action conflicts with the current state
(wrong status, over budget, gates failed); 422 = invalid input (the detail
says which field).
