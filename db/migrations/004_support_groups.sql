-- Five support groups instead of four (agreed 24 Sep 2026):
--   health (medical), food, housing_bills (rent arrears, utilities),
--   education_childcare (education, childcare), funeral_other (funeral, other).
-- Every view calls support_group(), so replacing the function is enough.
-- Safe to re-run.

CREATE OR REPLACE FUNCTION support_group(nc need_category_enum) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE nc
        WHEN 'medical'      THEN 'health'
        WHEN 'food'         THEN 'food'
        WHEN 'rent_arrears' THEN 'housing_bills'
        WHEN 'utilities'    THEN 'housing_bills'
        WHEN 'education'    THEN 'education_childcare'
        WHEN 'childcare'    THEN 'education_childcare'
        ELSE 'funeral_other'   -- funeral, other
    END
$$;
COMMENT ON FUNCTION support_group(need_category_enum) IS
    'Dashboard grouping of need_category: health, food, housing_bills, education_childcare, funeral_other.';
