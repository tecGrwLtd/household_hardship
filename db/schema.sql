-- ============================================================================
-- Household Hardship Allocation Platform — Database Schema
-- Target: PostgreSQL 14+
--
-- Source documents:
--   - "Household hardship allocation model — design spec.docx"
--   - Team meeting notes (Greenhouses call, Sep 2026)
--
-- Design principles carried over from the spec:
--   1. Protected attributes (ethnicity, gender_head, disability, age_band,
--      religion, nationality, immigration_status) are collected for AUDIT
--      ONLY, in their own table, and must never be joined into any view or
--      query that feeds the ML feature pipeline.
--   2. Stigmatised/never-collect fields (substance use, offending history,
--      mental-health diagnosis) are deliberately NOT modelled anywhere in
--      this schema.
--   3. was_approved / historical decisions are never stored as a label —
--      applications.status records workflow state, not a training target.
--   4. caseworker_id is retained (for contraction evaluation / override
--      monitoring) but is flagged never-a-feature in the data dictionary.
-- ============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- for gen_random_uuid()

-- ----------------------------------------------------------------------------
-- ENUM TYPES
-- ----------------------------------------------------------------------------

CREATE TYPE urban_rural_enum AS ENUM ('urban', 'rural');

CREATE TYPE tenure_enum AS ENUM (
    'owned', 'mortgaged', 'private_rent', 'social', 'informal', 'temporary'
);

CREATE TYPE employment_type_enum AS ENUM (
    'formal', 'informal', 'self_employed', 'unemployed', 'unable_to_work'
);

-- Ordered so the enum can double as an ordinal (none < primary < ... < tertiary)
CREATE TYPE education_level_enum AS ENUM (
    'none', 'primary', 'secondary', 'vocational', 'tertiary'
);

-- design spec's "need_category" plus 'education', which the client asked
-- for by name in the kickoff call (education loan / health loan / financial loan)
CREATE TYPE need_category_enum AS ENUM (
    'rent_arrears', 'medical', 'utilities', 'food', 'funeral',
    'childcare', 'education', 'other'
);

CREATE TYPE application_channel_enum AS ENUM (
    'online', 'phone', 'in_person', 'caseworker_submitted'
);

CREATE TYPE application_status_enum AS ENUM (
    'submitted', 'in_review', 'auto_approved', 'audit_approved',
    'deferred', 'appealed', 'withdrawn', 'awarded', 'closed'
);

CREATE TYPE decision_band_enum AS ENUM (
    'auto_approve', 'human_review', 'defer', 'audit_approve'
);

CREATE TYPE model_status_enum AS ENUM ('candidate', 'active', 'retired');

-- ----------------------------------------------------------------------------
-- REFERENCE DATA
-- ----------------------------------------------------------------------------

CREATE TABLE area_reference (
    area_code                  VARCHAR(20) PRIMARY KEY,
    area_name                  TEXT NOT NULL,
    urban_rural                urban_rural_enum NOT NULL,
    region                     VARCHAR(50),        -- province; audit dimension only, never a feature
    area_deprivation_index     NUMERIC(6, 3),      -- prefer a published index over raw postcode
    area_poverty_rate          NUMERIC(5, 4),      -- smallest geography available
    distance_to_services_km    NUMERIC(6, 2),      -- to nearest job centre / clinic / bank
    local_unemployment_rate    NUMERIC(5, 4),
    local_housing_cost_index   NUMERIC(6, 3),      -- anchors the deficit calculation
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE area_reference IS
    'Area-level PMT augmentation fields. Join on area_code. Largest accuracy '
    'gains in published PMT literature come from this join — worth the effort.';

CREATE TABLE funding_cycles (
    cycle_id        SERIAL PRIMARY KEY,
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    budget_total    NUMERIC(14, 2) NOT NULL,
    budget_currency VARCHAR(3) NOT NULL DEFAULT 'RWF',
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_cycle_dates CHECK (period_end > period_start)
);
COMMENT ON TABLE funding_cycles IS
    'One row per allocation period. The budget is fixed per cycle — this is a '
    'ranking-under-budget problem, not a per-applicant eligibility test.';

CREATE TABLE caseworkers (
    caseworker_id   SERIAL PRIMARY KEY,
    display_name    TEXT NOT NULL,
    region          VARCHAR(50),
    active          BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE caseworkers IS
    'Retained for contraction evaluation and override-rate monitoring. '
    'caseworker_id must NEVER be used as a model feature.';

-- ----------------------------------------------------------------------------
-- HOUSEHOLDS
-- ----------------------------------------------------------------------------

CREATE TABLE households (
    household_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_code       VARCHAR(20) NOT NULL REFERENCES area_reference(area_code),
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes           TEXT
);
CREATE INDEX idx_households_area_code ON households(area_code);

-- One row per household per survey wave (PMT is retaken periodically —
-- label drift means these numbers need refreshing, not a one-time capture).
CREATE TABLE household_surveys (
    survey_id                       BIGSERIAL PRIMARY KEY,
    household_id                    UUID NOT NULL REFERENCES households(household_id),
    survey_date                     DATE NOT NULL,

    -- composition
    household_size                  SMALLINT NOT NULL CHECK (household_size > 0),
    children_under_5                SMALLINT NOT NULL DEFAULT 0,
    members_over_65                 SMALLINT NOT NULL DEFAULT 0,
    female_headed                   BOOLEAN,
    single_caregiver                BOOLEAN,
    education_head                  education_level_enum,
    literacy_head                   BOOLEAN,               -- drop where universal

    -- housing (PMT staples)
    roof_material                   VARCHAR(50),
    wall_material                   VARCHAR(50),
    floor_material                  VARCHAR(50),
    rooms                           SMALLINT CHECK (rooms >= 0),
    tenure                          tenure_enum,
    water_source                    VARCHAR(50),
    sanitation_type                 VARCHAR(50),
    electricity                     BOOLEAN,
    cooking_fuel                    VARCHAR(50),

    -- assets (asset_* prefix mirrors the feature pipeline's asset_index build)
    asset_phone                     BOOLEAN DEFAULT false,
    asset_radio                     BOOLEAN DEFAULT false,
    asset_tv                        BOOLEAN DEFAULT false,
    asset_fridge                    BOOLEAN DEFAULT false,
    asset_washing_machine           BOOLEAN DEFAULT false,
    asset_bicycle                   BOOLEAN DEFAULT false,
    asset_motorcycle                BOOLEAN DEFAULT false,
    asset_car                       BOOLEAN DEFAULT false,
    livestock_count                 INTEGER,               -- rural contexts only
    land_area                       NUMERIC(8, 2),          -- rural contexts only, hectares

    -- income / labour
    employment_type                 employment_type_enum,
    earners_count                   SMALLINT,
    hours_worked                    NUMERIC(5, 2),          -- weekly, 4-week average
    monthly_income                  NUMERIC(12, 2),
    income_std_12m                  NUMERIC(12, 2),         -- feeds income_volatility
    income_seasonality              NUMERIC(6, 4),          -- coefficient of variation
    essential_costs                 NUMERIC(12, 2),         -- feeds monthly_deficit

    -- wellbeing
    food_security_score             SMALLINT,               -- use FIES/HFIAS as published
    chronic_illness                 BOOLEAN,                -- presence only, condition unnamed
    disability_in_household         BOOLEAN,
    dependents_requiring_care       SMALLINT DEFAULT 0,

    -- shocks (12 months)
    shock_bereavement_12m           BOOLEAN DEFAULT false,
    shock_serious_illness_12m       BOOLEAN DEFAULT false,
    shock_job_loss_12m              BOOLEAN DEFAULT false,
    shock_eviction_12m              BOOLEAN DEFAULT false,
    shock_displacement_12m          BOOLEAN DEFAULT false,
    shock_disaster_12m              BOOLEAN DEFAULT false,
    shock_crop_failure_12m          BOOLEAN DEFAULT false,  -- rural contexts only

    -- training target (Option A from the spec: consumption per capita)
    -- Real deployments won't have this column populated for most rows —
    -- it exists only for approved + randomly-audited applicants. Synthetic
    -- data here fills it for every row since it is the ground truth we
    -- generate from.
    consumption_pc                  NUMERIC(12, 2),

    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_surveys_household_id ON household_surveys(household_id);
CREATE INDEX idx_surveys_survey_date ON household_surveys(survey_date);

COMMENT ON COLUMN household_surveys.consumption_pc IS
    'Ground-truth welfare measure (Option A target). In production this is '
    'observed only for approved/audited applicants — see the selective-labels '
    'note in the design spec. Never derive this from was_approved.';

-- Protected / audit-only attributes. Deliberately isolated: this table must
-- never be joined into any query that builds model training features.
CREATE TABLE protected_attributes (
    household_id        UUID PRIMARY KEY REFERENCES households(household_id),
    ethnicity            VARCHAR(50),
    gender_head          VARCHAR(30),
    disability           VARCHAR(50),
    age_band             VARCHAR(20),
    religion             VARCHAR(50),
    nationality          VARCHAR(50),
    immigration_status   VARCHAR(50),
    collected_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE protected_attributes IS
    'AUDIT ONLY. Collected so disparate impact can be measured, never modelled. '
    'Do not join this table into any feature-building view or ML pipeline. '
    'Substance use, offending history and mental-health diagnosis are '
    'intentionally NOT represented anywhere in this schema — do not add them.';

-- ----------------------------------------------------------------------------
-- APPLICATIONS
-- ----------------------------------------------------------------------------

CREATE TABLE applications (
    application_id              BIGSERIAL PRIMARY KEY,
    household_id                UUID NOT NULL REFERENCES households(household_id),
    cycle_id                    INTEGER NOT NULL REFERENCES funding_cycles(cycle_id),
    caseworker_id                INTEGER REFERENCES caseworkers(caseworker_id),

    submitted_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    amount_requested              NUMERIC(12, 2) NOT NULL,
    need_category                 need_category_enum NOT NULL,
    stated_need_amount             NUMERIC(12, 2),         -- what the applicant says closes the gap
    days_since_hardship_onset      INTEGER,                 -- delay often signals isolation, not lower need
    prior_applications_count       INTEGER NOT NULL DEFAULT 0,
    days_since_last_application    INTEGER,                 -- null if first-ever application
    referral_source                VARCHAR(50),              -- model it, then audit it
    application_channel            application_channel_enum,
    application_completeness       NUMERIC(4, 3),           -- fraction of optional fields filled
    documentation_provided         BOOLEAN,                  -- audit for disparate impact before trusting

    status                        application_status_enum NOT NULL DEFAULT 'submitted',
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_applications_household_id ON applications(household_id);
CREATE INDEX idx_applications_cycle_id ON applications(cycle_id);
CREATE INDEX idx_applications_need_category ON applications(need_category);
CREATE INDEX idx_applications_submitted_at ON applications(submitted_at);

COMMENT ON COLUMN applications.caseworker_id IS
    'Kept for contraction evaluation (comparing caseworkers of different '
    'strictness on comparable applicants). Never a model feature.';

-- Model registry. Training writes a 'candidate'; activation (after its
-- evaluation gates pass) makes it the single 'active' version that scores
-- new applications. Artifacts live on disk at artifact_path
-- (models/<version>/: model files, metadata.json, metrics.json).
CREATE TABLE model_versions (
    model_version   VARCHAR(30) PRIMARY KEY,
    kind            VARCHAR(20) NOT NULL,           -- 'lgbm_quantile' | 'rules' | 'repeat_lgbm' | 'repeat_history'
    purpose         VARCHAR(10) NOT NULL DEFAULT 'need',   -- 'need' (allocation) | 'repeat' (planning forecast)
    status          model_status_enum NOT NULL DEFAULT 'candidate',
    artifact_path   TEXT,                           -- relative to the project root; NULL for 'rules'
    trained_at      TIMESTAMPTZ,
    training_rows   INTEGER,
    data_hash       VARCHAR(64),                    -- sha256 of the exact training matrix
    metrics         JSONB,                          -- evaluation report incl. gates
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at    TIMESTAMPTZ
);
CREATE UNIQUE INDEX one_active_model_per_purpose ON model_versions (purpose) WHERE status = 'active';
COMMENT ON TABLE model_versions IS
    'Registry of model versions. At most one active per purpose (need, repeat).';

-- The transparent rule-based placeholder (backend/ml/baselines.py) is live
-- from day one, so the platform works before any model is trained.
INSERT INTO model_versions (model_version, kind, status, notes, activated_at)
VALUES ('rules-v0', 'rules', 'active',
        'Placeholder scorer: hand-written formula, replace with a trained version.', now());

-- Model output per application per cycle. Multiple rows possible if the
-- model is re-run (model_version distinguishes them).
CREATE TABLE model_scores (
    score_id        BIGSERIAL PRIMARY KEY,
    application_id  BIGINT NOT NULL REFERENCES applications(application_id),
    cycle_id        INTEGER NOT NULL REFERENCES funding_cycles(cycle_id),
    model_version   VARCHAR(30) NOT NULL REFERENCES model_versions(model_version),
    need_lo         NUMERIC(12, 2) NOT NULL,   -- 10th percentile (INTERVAL[0])
    need_mid        NUMERIC(12, 2) NOT NULL,   -- median prediction, used for ranking
    need_hi         NUMERIC(12, 2) NOT NULL,   -- 90th percentile (INTERVAL[1])
    cutoff          NUMERIC(12, 2),            -- budget cutoff at scoring time; NULL = budget covered every application
    band            decision_band_enum NOT NULL,
    top_shap_features JSONB,                    -- [["monthly_deficit", 0.42], ...]
    scored_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_model_scores_application_id ON model_scores(application_id);
CREATE INDEX idx_model_scores_cycle_id ON model_scores(cycle_id);

-- Repeat-support forecast per application (backend/ml/repeat.py). PLANNING
-- ONLY: feeds dashboard forecasts, never allocation.
CREATE TABLE repeat_forecasts (
    forecast_id     BIGSERIAL PRIMARY KEY,
    application_id  BIGINT NOT NULL REFERENCES applications(application_id),
    model_version   VARCHAR(30) NOT NULL REFERENCES model_versions(model_version),
    p_return_1y     NUMERIC(5, 4) NOT NULL,          -- P(household applies again within 365 days)
    forecast_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_repeat_forecasts_application_id ON repeat_forecasts(application_id);

-- Monthly drift reports for the need model (backend/ml/drift.py).
CREATE TABLE drift_reports (
    report_id       BIGSERIAL PRIMARY KEY,
    model_version   VARCHAR(30) NOT NULL REFERENCES model_versions(model_version),
    window_start    DATE NOT NULL,
    window_end      DATE NOT NULL,
    applications    INTEGER NOT NULL,
    status          VARCHAR(10) NOT NULL,            -- ok | watch | alert
    report          JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE application_reviews (
    review_id        BIGSERIAL PRIMARY KEY,
    application_id   BIGINT NOT NULL REFERENCES applications(application_id),
    caseworker_id    INTEGER NOT NULL REFERENCES caseworkers(caseworker_id),
    reviewed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    initial_band     decision_band_enum NOT NULL,
    final_decision   VARCHAR(30) NOT NULL,   -- 'approved' | 'denied' | 'deferred' | 'appeal_upheld' ...
    overridden       BOOLEAN NOT NULL DEFAULT false,
    notes            TEXT
);
CREATE INDEX idx_reviews_application_id ON application_reviews(application_id);
COMMENT ON TABLE application_reviews IS
    'Human-in-the-loop record required for GDPR Art.22 / EU AI Act Art.14 '
    'compliance on the automated path. Monitor overridden rate — a review '
    'function with ~1% overrides is read as a rubber stamp.';

CREATE TABLE awards (
    award_id                 BIGSERIAL PRIMARY KEY,
    application_id           BIGINT NOT NULL REFERENCES applications(application_id),
    household_id             UUID NOT NULL REFERENCES households(household_id),
    award_amount             NUMERIC(12, 2) NOT NULL,
    award_date               DATE NOT NULL,
    need_category            need_category_enum NOT NULL,
    outcome_followup_days    INTEGER,   -- days later a follow-up outcome was recorded (Option B / C target)
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_awards_household_id ON awards(household_id);
CREATE INDEX idx_awards_application_id ON awards(application_id);

CREATE TABLE fairness_audits (
    audit_id          BIGSERIAL PRIMARY KEY,
    cycle_id          INTEGER REFERENCES funding_cycles(cycle_id),  -- NULL = pooled across all cycles
    model_version     VARCHAR(30) REFERENCES model_versions(model_version),
    attribute         VARCHAR(30) NOT NULL,   -- e.g. 'ethnicity', 'gender_head', 'urban_rural'
    group_value       VARCHAR(50) NOT NULL,
    n                 INTEGER NOT NULL,
    exclusion_error   NUMERIC(6, 4) NOT NULL, -- share of bottom-decile true-need deferred
    gap_vs_best       NUMERIC(6, 4) NOT NULL,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_fairness_audits_cycle_id ON fairness_audits(cycle_id);
COMMENT ON TABLE fairness_audits IS
    'Output of audit() in the modelling pipeline. Disaggregated, never a '
    'single blended fairness number — interactions hide in the aggregate.';

COMMIT;
