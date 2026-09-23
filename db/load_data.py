#!/usr/bin/env python3
"""
Load schema.sql + views.sql + the CSVs in db/seed/ into a Postgres database.

Usage:
    python3 load_data.py --dsn "postgresql://hardship_app:hardship_dev_only@localhost:5432/hardship_platform"

Safe to re-run against an empty database. Not idempotent against a database
that already has data (rerunning will violate primary key constraints) —
drop and recreate the database first if you want a clean reload.
"""
import argparse
import sys
from pathlib import Path

import psycopg2

HERE = Path(__file__).resolve().parent
SEED_DIR = HERE / "seed"

# FK-safe load order
TABLE_ORDER = [
    "area_reference",
    "funding_cycles",
    "caseworkers",
    "households",
    "household_surveys",
    "protected_attributes",
    "applications",
    "application_reviews",
    "awards",
]

# tables with an explicit serial/bigserial PK we populate ourselves and
# must therefore bump the sequence for afterwards
SEQUENCES = {
    "funding_cycles": ("funding_cycles_cycle_id_seq", "cycle_id"),
    "caseworkers": ("caseworkers_caseworker_id_seq", "caseworker_id"),
    "household_surveys": ("household_surveys_survey_id_seq", "survey_id"),
    "applications": ("applications_application_id_seq", "application_id"),
    "application_reviews": ("application_reviews_review_id_seq", "review_id"),
    "awards": ("awards_award_id_seq", "award_id"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True, help="Postgres connection string")
    ap.add_argument("--skip-schema", action="store_true", help="Skip running schema.sql/views.sql (data-only load)")
    args = ap.parse_args()

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        if not args.skip_schema:
            print("Applying schema.sql ...")
            cur.execute((HERE / "schema.sql").read_text(encoding="utf-8"))
            print("Applying views.sql ...")
            cur.execute((HERE / "views.sql").read_text(encoding="utf-8"))
            conn.commit()

        for table in TABLE_ORDER:
            csv_path = SEED_DIR / f"{table}.csv"
            if not csv_path.exists():
                print(f"  skip {table}: no CSV found at {csv_path}", file=sys.stderr)
                continue
            with open(csv_path, "r", encoding="utf-8") as f:
                header = f.readline().strip()
                columns = header.split(",")
                col_list = ", ".join(f'"{c}"' for c in columns)
                f.seek(0)
                next(f)  # skip header for COPY (we pass HEADER true anyway, but be explicit)
                cur.copy_expert(
                    f'COPY {table} ({col_list}) FROM STDIN WITH (FORMAT csv, NULL \'\')',
                    f,
                )
            conn.commit()
            cur.execute(f"SELECT count(*) FROM {table}")
            print(f"  loaded {table}: {cur.fetchone()[0]} rows")

        for table, (seq_name, pk_col) in SEQUENCES.items():
            cur.execute(f"SELECT setval('{seq_name}', COALESCE((SELECT MAX({pk_col}) FROM {table}), 1))")
        conn.commit()
        print("Sequences reset. Load complete.")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
