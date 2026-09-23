-- ============================================================================
-- Dashboard-facing views
--
-- These map directly onto what was asked for in the kickoff call: a monthly
-- applicant count broken down by support type (education / health /
-- financial / bereavement), and a repeat-support breakdown (helped before?
-- came back within one year or after more than one?).
-- The frontend dev can query these directly instead of re-deriving the
-- logic in the dashboard layer.
--
-- Safe to re-apply: every view is dropped and recreated.
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS v_monthly_support, v_monthly_applications, v_repeat_support, v_cycle_summary;

-- The client talks about education / health / financial support; the schema
-- records the finer need_category. One place defines the grouping so every
-- view (and the API) agrees. Bereavement is kept apart from financial
-- because the client called out "loss / grieving" separately.
CREATE OR REPLACE FUNCTION support_group(nc need_category_enum) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE nc
        WHEN 'education' THEN 'education'
        WHEN 'medical'   THEN 'health'
        WHEN 'funeral'   THEN 'bereavement'
        ELSE 'financial'   -- rent_arrears, utilities, food, childcare, other
    END
$$;
COMMENT ON FUNCTION support_group(need_category_enum) IS
    'Dashboard grouping of need_category: education, health, financial, bereavement.';

-- One row per month x need_category, with a running band breakdown from the
-- latest model score per application.
CREATE VIEW v_monthly_applications AS
SELECT
    date_trunc('month', a.submitted_at)::date AS month,
    a.need_category,
    count(*) AS applicant_count,
    count(*) FILTER (WHERE latest.band = 'auto_approve')  AS auto_approved_count,
    count(*) FILTER (WHERE latest.band = 'human_review')  AS human_review_count,
    count(*) FILTER (WHERE latest.band = 'defer')         AS deferred_count,
    count(*) FILTER (WHERE latest.band = 'audit_approve') AS audit_approved_count,
    support_group(a.need_category) AS support_group
FROM applications a
LEFT JOIN LATERAL (
    SELECT ms.band
    FROM model_scores ms
    WHERE ms.application_id = a.application_id
    ORDER BY ms.scored_at DESC, ms.score_id DESC
    LIMIT 1
) latest ON true
GROUP BY 1, 2
ORDER BY 1, 2;

COMMENT ON VIEW v_monthly_applications IS
    'Monthly applicant volume by need category, with current decision-band mix.';

-- Repeat-support view: for every application, has this household applied
-- before, and — what the client actually asked — was it HELPED before
-- (an award dated before this submission), and how long ago?
-- "days_since_last_application" is null on a household's first-ever application.
CREATE VIEW v_repeat_support AS
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
    END AS repeat_bucket,
    support_group(a.need_category) AS support_group,
    coalesce(prior.awards_count, 0) AS prior_awards_count,
    (a.submitted_at::date - prior.last_award_date) AS days_since_last_award,
    CASE
        WHEN prior.last_award_date IS NULL THEN 'never_helped'
        WHEN a.submitted_at::date - prior.last_award_date < 365 THEN 'helped_within_1y'
        ELSE 'helped_over_1y'
    END AS helped_bucket
FROM applications a
LEFT JOIN LATERAL (
    SELECT count(*) AS awards_count, max(aw.award_date) AS last_award_date
    FROM awards aw
    WHERE aw.household_id = a.household_id
      AND aw.award_date < a.submitted_at::date
) prior ON true;

COMMENT ON VIEW v_repeat_support IS
    'Per-application repeat classification: repeat applicant (repeat_bucket) '
    'and repeat beneficiary — helped before, within or over a year ago '
    '(helped_bucket) — the breakdown asked for in the kickoff call.';

-- The kickoff-call dashboard in one view: per month and support group, how
-- many applied, how many had been helped before (within / over a year), and
-- how many were awarded.
CREATE VIEW v_monthly_support AS
SELECT
    date_trunc('month', r.submitted_at)::date AS month,
    r.support_group,
    count(*) AS applicant_count,
    count(*) FILTER (WHERE r.is_repeat_applicant)                   AS repeat_applicant_count,
    count(*) FILTER (WHERE r.helped_bucket <> 'never_helped')       AS previously_helped_count,
    count(*) FILTER (WHERE r.helped_bucket = 'helped_within_1y')    AS helped_within_1y_count,
    count(*) FILTER (WHERE r.helped_bucket = 'helped_over_1y')      AS helped_over_1y_count,
    count(aw.award_id)                                              AS awarded_count,
    coalesce(sum(aw.award_amount), 0)                               AS awarded_amount
FROM v_repeat_support r
LEFT JOIN awards aw ON aw.application_id = r.application_id
GROUP BY 1, 2
ORDER BY 1, 2;

COMMENT ON VIEW v_monthly_support IS
    'Monthly applicants per support group (education/health/financial/bereavement), '
    'repeat beneficiaries within/over one year, and awards.';

-- Single-row-per-cycle rollup for a cycle summary card.
CREATE VIEW v_cycle_summary AS
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
