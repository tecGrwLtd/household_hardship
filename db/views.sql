-- ============================================================================
-- Dashboard-facing views
--
-- These map directly onto what was asked for in the kickoff call: a monthly
-- applicant count broken down by loan/need type, and a repeat-support
-- breakdown (helped before? will need help again within <1yr / >1yr?).
-- The frontend dev can query these directly instead of re-deriving the
-- logic in the dashboard layer.
-- ============================================================================

BEGIN;

-- One row per month x need_category, with a running band breakdown from the
-- latest model score per application. This is the source for the
-- "20 applicants this month -> X education / Y health / Z financial" chart.
CREATE OR REPLACE VIEW v_monthly_applications AS
SELECT
    date_trunc('month', a.submitted_at)::date AS month,
    a.need_category,
    count(*) AS applicant_count,
    count(*) FILTER (WHERE latest.band = 'auto_approve')  AS auto_approved_count,
    count(*) FILTER (WHERE latest.band = 'human_review')  AS human_review_count,
    count(*) FILTER (WHERE latest.band = 'defer')         AS deferred_count,
    count(*) FILTER (WHERE latest.band = 'audit_approve') AS audit_approved_count
FROM applications a
LEFT JOIN LATERAL (
    SELECT ms.band
    FROM model_scores ms
    WHERE ms.application_id = a.application_id
    ORDER BY ms.scored_at DESC
    LIMIT 1
) latest ON true
GROUP BY 1, 2
ORDER BY 1, 2;

COMMENT ON VIEW v_monthly_applications IS
    'Monthly applicant volume by need category, with current decision-band mix.';

-- Repeat-support view: for every application, was this household helped
-- before, and if they come back, was it within one year or more than a year?
-- "days_since_last_application" is null on a household's first-ever application.
CREATE OR REPLACE VIEW v_repeat_support AS
SELECT
    a.application_id,
    a.household_id,
    a.submitted_at,
    a.need_category,
    a.prior_applications_count,
    a.days_since_last_application,
    (a.prior_applications_count > 0) AS is_repeat_applicant,
    CASE
        WHEN a.prior_applications_count = 0 THEN 'first_time'
        WHEN a.days_since_last_application IS NULL THEN 'unknown'
        WHEN a.days_since_last_application < 365 THEN 'repeat_within_1y'
        ELSE 'repeat_over_1y'
    END AS repeat_bucket
FROM applications a;

COMMENT ON VIEW v_repeat_support IS
    'Per-application repeat-support classification: first-time vs. repeat '
    '(within 1 year) vs. repeat (over 1 year) — exactly the breakdown asked '
    'for in the kickoff call.';

-- Single-row-per-cycle rollup for a cycle summary card.
CREATE OR REPLACE VIEW v_cycle_summary AS
SELECT
    fc.cycle_id,
    fc.period_start,
    fc.period_end,
    fc.budget_total,
    fc.budget_currency,
    count(DISTINCT a.application_id) AS total_applications,
    count(DISTINCT a.household_id) AS total_households,
    count(DISTINCT a.application_id) FILTER (WHERE a.prior_applications_count > 0) AS repeat_applications,
    coalesce(sum(aw.award_amount), 0) AS total_awarded
FROM funding_cycles fc
LEFT JOIN applications a ON a.cycle_id = fc.cycle_id
LEFT JOIN awards aw ON aw.application_id = a.application_id
GROUP BY fc.cycle_id, fc.period_start, fc.period_end, fc.budget_total, fc.budget_currency
ORDER BY fc.period_start;

COMMIT;
