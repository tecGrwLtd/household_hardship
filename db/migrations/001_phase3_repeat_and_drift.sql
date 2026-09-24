-- Phase 3: one active model per purpose, repeat forecasts, drift reports.
-- For databases created before this change; a fresh database gets all of
-- it from schema.sql. Safe to run more than once:
--   docker compose exec -T db psql -U hardship_app -d hardship_platform < db/migrations/001_phase3_repeat_and_drift.sql

BEGIN;

ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS purpose VARCHAR(10) NOT NULL DEFAULT 'need';
DROP INDEX IF EXISTS one_active_model_version;
CREATE UNIQUE INDEX IF NOT EXISTS one_active_model_per_purpose ON model_versions (purpose) WHERE status = 'active';
COMMENT ON TABLE model_versions IS
    'Registry of model versions. At most one active per purpose (need, repeat).';

-- Repeat-support forecast per application (backend/ml/repeat.py). PLANNING
-- ONLY: feeds dashboard forecasts, never allocation.
CREATE TABLE IF NOT EXISTS repeat_forecasts (
    forecast_id     BIGSERIAL PRIMARY KEY,
    application_id  BIGINT NOT NULL REFERENCES applications(application_id),
    model_version   VARCHAR(30) NOT NULL REFERENCES model_versions(model_version),
    p_return_1y     NUMERIC(5, 4) NOT NULL,          -- P(household applies again within 365 days)
    forecast_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_repeat_forecasts_application_id ON repeat_forecasts(application_id);

-- Monthly drift reports for the need model (backend/ml/drift.py).
CREATE TABLE IF NOT EXISTS drift_reports (
    report_id       BIGSERIAL PRIMARY KEY,
    model_version   VARCHAR(30) NOT NULL REFERENCES model_versions(model_version),
    window_start    DATE NOT NULL,
    window_end      DATE NOT NULL,
    applications    INTEGER NOT NULL,
    status          VARCHAR(10) NOT NULL,            -- ok | watch | alert
    report          JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
