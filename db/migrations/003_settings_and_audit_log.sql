-- Platform settings and the admin activity log. For databases created before
-- this change; safe to run more than once:
--   docker compose exec -T db psql -U hardship_app -d hardship_platform < db/migrations/003_settings_and_audit_log.sql

BEGIN;
-- Platform settings an admin can change from the admin console. Each one is
-- read where it matters (allocation, model activation, monitoring, the next
-- training run); every change is written to audit_log.
CREATE TABLE IF NOT EXISTS platform_settings (
    key             VARCHAR(50) PRIMARY KEY,
    value           JSONB,                      -- null = not set
    description     TEXT NOT NULL,
    updated_by      VARCHAR(50),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO platform_settings (key, value, description) VALUES
    ('random_audit_rate', '0.04', 'Share of deferred applications approved at random at every allocation. The design spec asks for 3-5%: it is the only source of unbiased outcomes for retraining.'),
    ('override_rate_floor', '0.05', 'Reviewer override rate below which human review is flagged as a possible rubber stamp.'),
    ('max_exclusion_error', 'null', 'Launch threshold: the highest share of the poorest decile a need model may defer. A version above it cannot be activated without forcing. Blank until agreed.'),
    ('max_subgroup_gap', 'null', 'Launch threshold: the largest allowed gap in exclusion error between groups. Blank until agreed.'),
    ('poverty_line', 'null', 'Poverty line (RWF per person per month) for the next training run. Blank: the median consumption placeholder.')
ON CONFLICT (key) DO NOTHING;

-- Who changed what: admin actions and decisions, for accountability.
CREATE TABLE IF NOT EXISTS audit_log (
    log_id          BIGSERIAL PRIMARY KEY,
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    username        VARCHAR(50),
    action          VARCHAR(50) NOT NULL,       -- e.g. setting.update, user.create, model.activate, cycle.allocate
    target          TEXT,
    details         JSONB
);
CREATE INDEX IF NOT EXISTS idx_audit_log_at ON audit_log(at DESC);
COMMIT;
