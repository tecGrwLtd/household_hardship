// Display helpers. Labels live here so every page words things the same way.

export type SupportGroup = "financial" | "health" | "education" | "bereavement";

export const SUPPORT_GROUPS: SupportGroup[] = ["financial", "health", "education", "bereavement"];

export const GROUP_LABEL: Record<string, string> = {
  financial: "Financial",
  health: "Health",
  education: "Education",
  bereavement: "Bereavement",
};

// CSS variables, so the colours follow the theme.
export const GROUP_COLOR: Record<string, string> = {
  financial: "var(--teal)",
  health: "var(--blue)",
  education: "var(--amber)",
  bereavement: "var(--violet)",
};

export const NEED_LABEL: Record<string, string> = {
  rent_arrears: "Rent arrears",
  medical: "Medical",
  utilities: "Utilities",
  food: "Food",
  funeral: "Funeral",
  childcare: "Childcare",
  education: "Education",
  other: "Other",
};

export const NEED_GROUP: Record<string, SupportGroup> = {
  rent_arrears: "financial",
  medical: "health",
  utilities: "financial",
  food: "financial",
  funeral: "bereavement",
  childcare: "financial",
  education: "education",
  other: "financial",
};

export type Tone = "neutral" | "teal" | "amber" | "blue" | "danger";

export const STATUS: Record<string, { label: string; tone: Tone }> = {
  submitted: { label: "Submitted", tone: "neutral" },
  in_review: { label: "In review", tone: "amber" },
  auto_approved: { label: "Auto-approved", tone: "teal" },
  audit_approved: { label: "Audit sample", tone: "blue" },
  awarded: { label: "Awarded", tone: "teal" },
  deferred: { label: "Deferred", tone: "neutral" },
  appealed: { label: "Appealed", tone: "amber" },
  withdrawn: { label: "Withdrawn", tone: "neutral" },
  closed: { label: "Closed", tone: "neutral" },
};

export const BAND: Record<string, { label: string; color: string }> = {
  auto_approve: { label: "Auto-approve", color: "var(--teal)" },
  audit_approve: { label: "Audit sample", color: "var(--blue)" },
  human_review: { label: "Review", color: "var(--amber)" },
  defer: { label: "Defer", color: "var(--grey)" },
};

const nf = new Intl.NumberFormat("en-GB");

export function num(n: number | string | null | undefined, digits = 0): string {
  if (n === null || n === undefined || n === "") return "—";
  const v = typeof n === "string" ? Number(n) : n;
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(v);
}

export function rwf(n: number | string | null | undefined): string {
  return n === null || n === undefined ? "—" : `${nf.format(Math.round(Number(n)))} RWF`;
}

/** 15,637,551 -> "15.6M" */
export function compact(n: number | string | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const v = Number(n);
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 1 : 2).replace(/\.?0+$/, "")}M`;
  if (Math.abs(v) >= 1e3) return `${Math.round(v / 1e3)}k`;
  return nf.format(Math.round(v));
}

export function pct(part: number, whole: number, digits = 0): string {
  if (!whole) return "—";
  return `${((part / whole) * 100).toFixed(digits)}%`;
}

const monthFmt = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
const shortMonth = new Intl.DateTimeFormat("en-GB", { month: "short", timeZone: "UTC" });
const dayFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

export const monthLabel = (iso: string | null | undefined) => (iso ? monthFmt.format(new Date(iso)) : "All months");
// en-GB abbreviates September as "Sept"; keep every month at three letters.
const sep = (s: string) => s.replace("Sept", "Sep");
export const monthShort = (iso: string) => sep(shortMonth.format(new Date(iso)));
export const day = (iso: string | null | undefined) => (iso ? sep(dayFmt.format(new Date(iso))) : "—");

/** Model need estimates at or below zero mean "above the poverty line". */
export function need(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n <= 0 ? "above the line" : nf.format(Math.round(n));
}

export const shortId = (uuid: string) => `HH-${uuid.slice(0, 4)}`;

export function initials(name: string): string {
  return name.replace(/\./g, "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
}

/** Readable names for model features in explanations. */
export const FEATURE_LABEL: Record<string, string> = {
  monthly_income: "Monthly income",
  household_size: "Household size",
  deficit_ratio: "Costs far above income",
  monthly_deficit: "Monthly shortfall",
  local_housing_cost_index: "Housing costs in the district",
  livestock_count: "Livestock owned",
  land_area: "Land farmed",
  income_seasonality: "Income varies by season",
  income_std_12m: "Income swings",
  income_volatility: "Income precarity",
  stated_need_amount: "Amount said to close the gap",
  amount_requested: "Amount requested",
  food_security_score: "Food insecurity",
  shock_count_12m: "Shocks this year",
  essential_costs: "Essential costs",
  children_under_5: "Children under five",
  members_over_65: "Members over 65",
  dependency_ratio: "Dependants per earner",
  asset_index: "Assets owned",
  crowding: "People per room",
  earners_count: "Earners",
  hours_worked: "Hours worked",
  area_poverty_rate: "District poverty rate",
  area_deprivation_index: "District deprivation",
  deficit_per_person: "Shortfall per person",
  assets_owned: "Assets owned",
  prior_applications_count: "Earlier applications",
  days_since_last_application: "Time since last application",
  days_since_hardship_onset: "Days since hardship began",
  application_completeness: "Form completeness",
  need_category: "Kind of need",
};

export const featureLabel = (f: string) =>
  FEATURE_LABEL[f] ?? f.replace(/^shock_(.*)_12m$/, "Shock: $1").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
