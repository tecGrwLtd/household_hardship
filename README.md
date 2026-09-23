# Household Hardship Allocation Platform — Database & Dataset

Backend/database handoff for the frontend developer, built from the design spec
(`Household hardship allocation model — design spec.docx`) and the kickoff
call. There is no real applicant data yet, so this ships with a synthetic,
internally-consistent dataset — 3,500 households, ~11,800 applications across
24 monthly funding cycles — sized to build and demo a real dashboard against.

## What's here

```
db/
  schema.sql          Full PostgreSQL DDL (tables, enums, indexes, constraints)
  views.sql           Dashboard-ready views (see below)
  erd.mmd             Entity-relationship diagram (Mermaid — paste into mermaid.live or a Markdown viewer)
  docker-compose.yml  One-command local Postgres, schema pre-applied
  seed/*.csv          The synthetic dataset, one CSV per table
  load_data.py        Loads schema.sql + views.sql + seed/*.csv into any Postgres instance
data/
  generate_synthetic_data.py   Regenerate the synthetic dataset (e.g. with more rows, a new seed)
backend/ml/
  features.py, model.py, allocate.py, audit.py, run_pipeline.py
  Runnable version of the design spec's modelling pipeline, trained and
  validated against the synthetic dataset (see backend/ml/README.md)
docs/
  DATA_DICTIONARY.md  Every table and column, with notes on what's safe to use where
```

## Quickest way to get a database running

```bash
cd db
docker compose up -d          # starts Postgres 16, applies schema.sql + views.sql automatically
pip install psycopg2-binary
python3 load_data.py --dsn "postgresql://hardship_app:hardship_dev_only@localhost:5432/hardship_platform" --skip-schema
```

(`--skip-schema` because docker-entrypoint-initdb.d already applied it on
first boot. Dropping the `hardship_db_data` volume and re-running `up -d`
gives you a truly clean slate.)

If you're not using Docker, point `load_data.py` at any Postgres 14+
database and drop `--skip-schema` — it will create everything itself.

## What's already verified

This isn't just a schema on paper — schema.sql, views.sql, and all nine CSVs
have been loaded end-to-end into a real Postgres 16 instance and checked for:
budget-adherence sanity (award totals track each cycle's fixed budget),
referential integrity (every household has exactly one survey and one
protected-attributes row), and value ranges (no negative incomes, completeness
scores in [0,1], etc). See `docs/DATA_DICTIONARY.md` for the full rundown.

## Dashboard-ready views (`db/views.sql`)

Built directly from what was asked for in the kickoff call:

- **`v_monthly_applications`** — applicant count per month x need category,
  with the current decision-band mix (auto-approve / human review / defer /
  audit-approve). This is the "20 applicants this month, broken down into
  education / health / financial" chart. The band columns are all zero
  until the ML pipeline has scored a cycle (see `backend/ml/`) — that's
  expected on a fresh load.
- **`v_repeat_support`** — per application: first-time vs. repeat-within-1-year
  vs. repeat-over-1-year. Exactly the "how many will need help again, and how
  soon" breakdown from the call.
- **`v_cycle_summary`** — one row per funding cycle: applications, repeat
  applications, total awarded vs. budget.

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
