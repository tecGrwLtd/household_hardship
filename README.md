# Household Hardship Allocation Platform

Backend for the household hardship allocation platform, built from the
design spec (`Household hardship allocation model — design spec.docx`) and
the kickoff call: database, need model, and the API the dashboard and
data-entry screens sit on. There is no real applicant data yet, so this
ships with a synthetic, internally-consistent dataset — 3,500 households,
~7,100 applications across 24 monthly funding cycles — sized to build and
demo a real dashboard against.

## What's here

```
docker-compose.yml    Postgres + API, one command
Dockerfile            API image
backend/api/          FastAPI app: data entry, cycle allocation, human review,
                      dashboard, model management (docs/API.md)
backend/ml/           The need model: evaluate / train / activate / score from the
                      command line, baselines, rule-based placeholder, model
                      registry (backend/ml/README.md)
db/
  schema.sql          Full PostgreSQL DDL (tables, enums, indexes, constraints)
  views.sql           Dashboard-ready views (see below)
  erd.mmd             Entity-relationship diagram (Mermaid)
  seed/*.csv          The synthetic dataset, one CSV per table
  load_data.py        Loads schema.sql + views.sql + seed/*.csv into any Postgres instance
data/
  generate_synthetic_data.py   Regenerate the synthetic dataset
models/               Trained model artifacts (git-ignored; built by `train`)
tests/                pytest: unit tests (no database) + API tests (need Postgres)
docs/
  API.md              API guide for the frontend
  DATA_DICTIONARY.md  Every table and column, with notes on what's safe to use where
  ROADMAP.md          Plan, decisions and progress
```

## Run it

Everything in Docker — no local Python needed:

```bash
docker compose up -d --build     # Postgres 16 (schema + views applied on first boot) and the API on :8080

DSN=postgresql://hardship_app:hardship_dev_only@db:5432/hardship_platform
docker compose run --rm api python db/load_data.py --skip-schema --dsn $DSN   # synthetic data
docker compose run --rm api python -m backend.ml --dsn $DSN score             # score with the placeholder
```

The API is at http://localhost:8080/docs (log in with `admin` /
`admin-dev-only`; see `docs/API.md`). A fresh database scores with the
transparent rule-based placeholder (`rules-v0`). To train and switch to the
real model:

```bash
docker compose run --rm api python -m backend.ml --dsn $DSN train        # prints the evaluation and the version name
docker compose run --rm api python -m backend.ml --dsn $DSN activate <version>
docker compose run --rm api python -m backend.ml --dsn $DSN score
```

`docker compose down -v` gives you a clean slate. Passwords and the token
secret in `docker-compose.yml` are development defaults — set
`HARDSHIP_DB_PASSWORD`, `HARDSHIP_ADMIN_PASSWORD` and `HARDSHIP_SECRET` for
anything beyond a laptop.

### Local development

```bash
python -m venv .venv && .venv/Scripts/activate      # or source .venv/bin/activate
pip install -r requirements-dev.txt
docker compose up -d db
python db/load_data.py --skip-schema --dsn postgresql://hardship_app:hardship_dev_only@localhost:5432/hardship_platform
uvicorn backend.api.main:app --port 8080 --reload
pytest                                               # API tests create and drop their own database
```

Without Docker, point `load_data.py` at any Postgres 14+ database and drop
`--skip-schema` — it will create everything itself.

## What's already verified

This isn't just a schema on paper — schema.sql, views.sql, and all nine CSVs
have been loaded end-to-end into a real Postgres 16 instance and checked for:
budget adherence (historical award totals land at roughly 80–105% of each
cycle's fixed budget — the simulated caseworkers' review and audit approvals
can overshoot slightly, as real ones do),
referential integrity (every household has exactly one survey and one
protected-attributes row), time ordering (every household registers and is
surveyed before its first application), and value ranges (no negative incomes, completeness
scores in [0,1], etc). See `docs/DATA_DICTIONARY.md` for the full rundown.

## Dashboard-ready views (`db/views.sql`)

Built directly from what was asked for in the kickoff call:

- **`v_monthly_support`** — the kickoff-call dashboard in one view: per month
  and support group (education / health / financial / bereavement), how many
  applied, how many had been helped before — within a year or over a year
  ago — and how many were awarded. This is the "20 applicants this month,
  broken down into education / health / financial" chart.
- **`v_monthly_applications`** — applicant count per month x need category
  (plus its `support_group`), with the current decision-band mix
  (auto-approve / human review / defer / audit-approve). The band columns
  are all zero until applications have been scored (`python -m backend.ml score`)
  — that's expected on a fresh load.
- **`v_repeat_support`** — per application: repeat *applicant* (first-time /
  repeat within 1 year / over 1 year) and repeat *beneficiary* (never
  helped / helped within 1 year / over 1 year, from prior awards).
- **`v_cycle_summary`** — one row per funding cycle: applications, repeat
  applications, total awarded vs. budget.

The grouping of the eight need categories into the four support groups
lives in one SQL function, `support_group()`, at the top of `views.sql`.

## Important modelling constraints baked into the schema

These come straight from the design spec and matter for how the frontend
queries and displays data, not just for the model:

- **`protected_attributes` is audit-only.** Ethnicity, gender, disability,
  age band, religion, nationality and immigration status live in their own
  table specifically so a dashboard can show disparate-impact reporting
  without that data ever leaking into a model-facing query. Don't join it
  into anything feeding a ranking or approval-likelihood display.
- **No `was_approved` column exists anywhere.** `applications.status` is a
  workflow/audit field (submitted, deferred, awarded, etc.), not a model
  label — training a model on it would just imitate past caseworkers.
- **Substance use, offending history and mental-health diagnosis are not in
  this schema at all**, by design — the spec calls these out as fields that
  must never be collected for scoring.
- **`caseworker_id` is kept for accountability (override-rate monitoring,
  contraction evaluation), never as something a model or a "predicted
  approval" feature should use.**

## Currency & geography

Amounts are in RWF; `area_reference` uses Rwanda's district names as a
stand-in geography (urban: Nyarugenge, Gasabo, Kicukiro, Musanze, Rubavu,
Huye; the rest rural). Swap in real areas whenever real intake data exists —
the schema doesn't care what's in `area_code`, as long as it's a stable key.

## Regenerating or resizing the dataset

```bash
cd data
python3 generate_synthetic_data.py
```

Edit `N_HOUSEHOLDS` / `N_CYCLES` at the top of the script to resize. The
generator seeds `numpy`'s RNG (42) so output is reproducible; change the seed
for a different draw. It works from a single latent "poverty severity" value
per household and derives every other field from it with noise, rather than
randomising columns independently — that's what makes correlations (e.g.
poorer households have lower food-security scores, less asset ownership,
worse housing materials) show up realistically in dashboard charts, not just
in the model.
