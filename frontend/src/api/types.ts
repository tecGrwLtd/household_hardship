// Response shapes of the endpoints the app uses (see backend/api/routers).
// Numeric columns arrive as JSON numbers; dates as ISO strings.

export type Role = "admin" | "caseworker";

export interface User {
  user_id: number;
  username: string;
  display_name: string;
  role: Role;
  caseworker_id: number | null;
}

export interface FilterOptions {
  months: string[];
  support_groups: string[];
  regions: string[];
  districts: { area_code: string; area_name: string; region: string; urban_rural: string }[];
  urban_rural: string[];
}

export interface Summary {
  applicants: number;
  requested: number;
  awarded: number;
  awarded_amount: number;
  helped_within_1y: number;
  helped_over_1y: number;
  repeat_applicants: number;
  expected_back_1y: number;
  forecast_count: number;
  previous_applicants: number | null;
  budget: number | null;
  budget_applies: boolean;
}

export interface SupportTypeRow {
  support_group: string;
  applicants: number;
  helped_within_1y: number;
  helped_over_1y: number;
  first_help: number;
  awarded: number;
  awarded_amount: number;
  expected_back_1y: number;
}

export interface OutcomeRow { status: string; applications: number; awarded_amount: number }
export interface MonthlyRow { month: string; support_group: string; applicants: number; awarded: number }
export interface DistrictRow { area_code: string; area_name: string; region: string; urban_rural: string; applicants: number; requested: number; awarded: number }

export interface ApplicationListItem {
  application_id: number;
  household_id: string;
  cycle_id: number;
  submitted_at: string;
  need_category: string;
  support_group: string;
  amount_requested: number;
  status: string;
  area_name: string;
  region: string;
  awarded: boolean;
  award_amount: number | null;
  caseworker: string | null;
  caseworker_id: number | null;
}

export interface ApplicationPage { total: number; status_counts: Record<string, number>; items: ApplicationListItem[] }

export type Drivers = [string, number][];

export interface Score {
  score_id: number;
  model_version: string;
  need_lo: number;
  need_mid: number;
  need_hi: number;
  cutoff: number | null;
  band: string;
  top_shap_features: Drivers | null;
  scored_at: string;
}

export interface Provisional {
  model_version: string;
  need_lo: number;
  need_mid: number;
  need_hi: number;
  top_drivers: { feature: string; contribution: number }[];
  has_survey: boolean;
  note: string;
}

export interface Survey {
  survey_id: number;
  survey_date: string;
  household_size: number;
  children_under_5: number;
  members_over_65: number;
  monthly_income: number | null;
  essential_costs: number | null;
  food_security_score: number | null;
  employment_type: string | null;
  [key: string]: unknown;
}

export interface HistoryItem {
  application_id: number;
  submitted_at: string;
  need_category: string;
  support_group: string;
  amount_requested: number;
  status: string;
  award_amount: number | null;
}

export interface Review {
  review_id: number;
  caseworker: string | null;
  reviewed_at: string;
  initial_band: string;
  final_decision: string;
  overridden: boolean;
  notes: string | null;
}

export interface ApplicationDetail {
  application_id: number;
  household_id: string;
  cycle_id: number;
  caseworker_id: number | null;
  caseworker: string | null;
  submitted_at: string;
  amount_requested: number;
  stated_need_amount: number | null;
  need_category: string;
  support_group: string;
  status: string;
  application_channel: string | null;
  referral_source: string | null;
  prior_applications_count: number;
  days_since_last_application: number | null;
  documentation_provided: boolean | null;
  area_name: string;
  region: string;
  urban_rural: string;
  survey: Survey | null;
  score: Score | null;
  provisional_score: Provisional | null;
  reviews: Review[];
  award: { award_amount: number; award_date: string } | null;
  history: HistoryItem[];
  repeat_forecast: { p_return_1y: number; model_version: string } | null;
}

export interface QueueItem {
  application_id: number;
  household_id: string;
  cycle_id: number;
  status: string;
  need_category: string;
  support_group: string;
  amount_requested: number;
  caseworker_id: number | null;
  caseworker: string | null;
  submitted_at: string;
  area_name: string;
  region: string;
  helped_bucket: string;
  band: string | null;
  need_mid: number | null;
  cutoff: number | null;
  model_lean: "approve" | "deny";
  deferred_last_year: number;
}

export interface Queue {
  counts: { all: number; mine: number; review: number; appeal: number };
  mine: boolean;
  items: QueueItem[];
}

export interface HouseholdListItem {
  household_id: string;
  area_code: string;
  area_name: string;
  region: string;
  urban_rural: string;
  registered_at: string;
  last_survey: string | null;
  household_size: number | null;
  applications_count: number;
  awards_count: number;
}

export interface HouseholdDetail {
  household_id: string;
  area_code: string;
  area_name: string;
  region: string;
  urban_rural: string;
  registered_at: string;
  notes: string | null;
  audit_questions_answered: boolean;
  surveys: Survey[];
  applications: { application_id: number; cycle_id: number; submitted_at: string; need_category: string; amount_requested: number; status: string }[];
}

export interface Area { area_code: string; area_name: string; urban_rural: string; region: string }
export interface Caseworker { caseworker_id: number; display_name: string; region: string; active: boolean }

export interface Cycle {
  cycle_id: number;
  period_start: string;
  period_end: string;
  budget_total: number;
  budget_currency: string;
  total_applications: number;
  total_households: number;
  repeat_applications: number;
  total_awarded: number;
  total_requested: number;
  waiting_applications: number;
}

export interface CycleBudget { cycle_id: number; budget_total: number; currency: string; awarded: number; remaining: number }

export interface AllocationPlan {
  budget: CycleBudget;
  model_version: string | null;
  cutoff: number | null;
  summary: { budget: number; committed: number; in_review: number; remaining: number } | null;
  applications: { application_id: number; household_id: string; band: string; need_mid: number; amount_requested: number }[];
}

export interface MySummary {
  user: { display_name: string; role: Role; caseworker_id: number | null };
  cycle: { cycle_id: number; period_start: string; period_end: string } | null;
  open_cases: { open: number; appeals: number; oldest: string | null };
  this_cycle: { applications: number; helped: number; awarded_amount: number; by_support_group: { support_group: string; applicants: number }[] };
  reviews: { reviews: number; override_rate: number | null };
}

export interface ModelVersion {
  model_version: string;
  purpose: "need" | "repeat";
  kind: string;
  status: "candidate" | "active" | "retired";
  trained_at: string | null;
  activated_at: string | null;
  training_rows: number | null;
  notes: string | null;
  gates_passed: boolean | null;
}

export interface ModelVersionDetail extends ModelVersion {
  metrics: Record<string, unknown> | null;
}

export interface ModelHealth {
  active_model: {
    model_version: string;
    kind: string;
    activated_at: string;
    trained_at: string | null;
    training_rows: number | null;
    gates: Record<string, boolean | number | null> | null;
    evaluation: Record<string, unknown> | null;
  } | null;
  bands: Record<string, number>;
  reviews: { reviews: number; override_rate: number | null; approved: number; override_rate_ok: boolean | null };
}

export interface DriftReport {
  report_id: number;
  window_start: string;
  window_end: string;
  applications: number;
  status: "ok" | "watch" | "alert";
  report: {
    score_psi: number;
    features_shifted: { feature: string; psi: number; level: string }[];
    shap_stability: { overlap: number; top_features_now: string[] };
    interval_coverage: { n: number; coverage: number } | null;
  };
}

export interface Drift { model_version: string; latest: DriftReport | null; history: Omit<DriftReport, "report">[]; note: string | null }
export interface FairnessRow { attribute: string; group_value: string; n: number; exclusion_error: number; gap_vs_best: number }
export interface OverrideTrendRow { month: string; reviews: number; override_rate: number; approval_rate: number; override_rate_ok: boolean }

export interface SummaryExtra extends Summary { decided: number; average_award: number | null; median_requested: number | null }
export interface TrendRow { month: string; applicants: number; awarded: number; requested: number; awarded_amount: number; decided: number; helped_before: number; budget: number | null }
export interface HeatRow { region: string; support_group: string; applicants: number; awarded: number }
export interface ChannelRow { value: string; applicants: number; awarded: number; decided: number }
export interface AmountRow { band: string; from: number; to: number | null; applicants: number; awarded: number }

export interface ModelCard {
  version: ModelVersionDetail & { artifact_path: string | null; data_hash: string | null };
  metadata: Record<string, unknown>;
  features: string[] | null;
  importance: { feature: string; value: number }[];
  terms: { feature: string; weight: number }[] | null;
  metadata_missing?: boolean;
}

export interface WhatIfResult {
  model_version: string;
  kind: string;
  need_lo: number;
  need_mid: number;
  need_hi: number;
  drivers: { feature: string; contribution: number }[];
  context: { cycle_id: number; period_start: string; cutoff: number; likely_band: string; applicants: number; needier_than_share: number | null } | null;
}

export interface AdminUser { user_id: number; username: string; display_name: string; role: Role; caseworker_id: number | null; caseworker: string | null; active: boolean; created_at: string }
export interface AdminCaseworker { caseworker_id: number; display_name: string; region: string | null; active: boolean; applications: number; open_cases: number; reviews: number; override_rate: number | null; accounts: string | null }
export interface Setting { key: string; value: number | null; description: string; updated_by: string | null; updated_at: string }
export interface AuditEntry { log_id: number; at: string; username: string | null; action: string; target: string | null; details: Record<string, unknown> }
export interface SystemStatus {
  api_version: string;
  database: { version: string; size: string };
  counts: Record<string, number>;
  active_models: { purpose: string; model_version: string; kind: string; activated_at: string }[];
  last: Record<string, string>;
  last_drift_check: { status: string; window_end: string; created_at: string } | null;
  security: { development_secret: boolean; default_admin_password: boolean; token_hours: number };
}
