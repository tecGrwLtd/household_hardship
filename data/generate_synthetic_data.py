#!/usr/bin/env python3
"""
Synthetic data generator for the Household Hardship Allocation Platform.

There is no real data yet (confirmed in the kickoff call), so this builds a
*plausible, internally-consistent* dataset from a latent "true poverty
severity" variable per household, then derives every observed field from
it with noise and area effects — the way real welfare data actually
behaves, rather than independently randomising each column.

Design choices worth flagging for whoever reads this later:

  - consumption_pc (the model's training target) is filled for every row
    here because we are the ground-truth generator. In production this
    column is only observed for approved/audited applicants — see the
    selective-labels section of the design spec. The ML pipeline
    (backend/ml/) deliberately trains only on approved + audit_approve rows
    to mirror that constraint, even though we *could* cheat and use every
    row since we generated them all.

  - A synthetic, mild correlation between poverty severity and a couple of
    protected attributes (gender_head, age_band) is baked in on purpose so
    the fairness_audits pipeline has something non-trivial to detect when
    you run it. This is a demo convenience, not a claim about the real
    population — say so if you show this dataset to anyone outside the dev
    team.

  - Areas are modelled on Rwanda's real district names/urban-rural split
    since that's this team's operating context. Swap area_reference for
    real geography whenever it's available.

Output: one CSV per table into db/seed/, in FK-safe load order.
"""
import csv
import math
import uuid
from datetime import date, timedelta
from pathlib import Path

import numpy as np

RNG = np.random.default_rng(42)
OUT_DIR = Path(__file__).resolve().parent.parent / "db" / "seed"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _norm(weights):
    """Normalise a list of (possibly negative-leaning) weights into probabilities."""
    w = np.clip(np.array(weights, dtype=float), 0.005, None)
    return w / w.sum()

N_HOUSEHOLDS = 3500
# 24 monthly cycles (two years) so that a genuine >1-year repeat gap can show
# up in the data at all — with only 12 months of history every repeat is by
# construction "within 1 year", which defeats the dashboard breakdown asked
# for in the kickoff call.
N_CYCLES = 24
TODAY = date(2026, 9, 22)

# ---------------------------------------------------------------------------
# 1. Areas (Rwanda's 30 districts; a handful flagged urban)
# ---------------------------------------------------------------------------
DISTRICTS = [
    ("Nyarugenge", "urban"), ("Gasabo", "urban"), ("Kicukiro", "urban"),
    ("Musanze", "urban"), ("Rubavu", "urban"), ("Huye", "urban"),
    ("Bugesera", "rural"), ("Gatsibo", "rural"), ("Kayonza", "rural"),
    ("Kirehe", "rural"), ("Ngoma", "rural"), ("Nyagatare", "rural"),
    ("Rwamagana", "rural"), ("Burera", "rural"), ("Gakenke", "rural"),
    ("Gicumbi", "rural"), ("Rulindo", "rural"), ("Gisagara", "rural"),
    ("Kamonyi", "rural"), ("Muhanga", "rural"), ("Nyamagabe", "rural"),
    ("Nyanza", "rural"), ("Nyaruguru", "rural"), ("Ruhango", "rural"),
    ("Karongi", "rural"), ("Ngororero", "rural"), ("Nyabihu", "rural"),
    ("Nyamasheke", "rural"), ("Rusizi", "rural"), ("Rutsiro", "rural"),
]

areas = []
for i, (name, ur) in enumerate(DISTRICTS):
    code = f"AR{i+1:03d}"
    base_deprivation = RNG.uniform(0.55, 0.85) if ur == "rural" else RNG.uniform(0.15, 0.45)
    areas.append({
        "area_code": code,
        "area_name": name,
        "urban_rural": ur,
        "area_deprivation_index": round(base_deprivation, 3),
        "area_poverty_rate": round(np.clip(base_deprivation + RNG.normal(0, 0.05), 0.03, 0.9), 4),
        "distance_to_services_km": round(RNG.uniform(1, 4) if ur == "urban" else RNG.uniform(3, 25), 2),
        "local_unemployment_rate": round(np.clip(RNG.normal(0.16 if ur == "urban" else 0.10, 0.04), 0.02, 0.4), 4),
        "local_housing_cost_index": round(RNG.uniform(1.3, 2.2) if ur == "urban" else RNG.uniform(0.6, 1.2), 3),
    })

area_codes = [a["area_code"] for a in areas]
area_deprivation = {a["area_code"]: a["area_deprivation_index"] for a in areas}
area_urban = {a["area_code"]: a["urban_rural"] for a in areas}
area_housing_cost = {a["area_code"]: a["local_housing_cost_index"] for a in areas}
# more households in higher-population districts (rough weighting, urban denser)
area_weights = np.array([3.5 if a["urban_rural"] == "urban" else 1.0 for a in areas])
area_weights = area_weights / area_weights.sum()

# ---------------------------------------------------------------------------
# 2. Funding cycles: 12 trailing months, modest budget relative to demand
#    on purpose, so allocation is a real ranking-under-budget problem.
# ---------------------------------------------------------------------------
cycles = []
cursor = date(TODAY.year, TODAY.month, 1)
months = []
for i in range(N_CYCLES):
    m = cursor.month - i
    y = cursor.year
    while m <= 0:
        m += 12
        y -= 1
    months.append(date(y, m, 1))
months = sorted(months)

for i, start in enumerate(months, start=1):
    if start.month == 12:
        end = date(start.year, 12, 31)
    else:
        end = date(start.year, start.month + 1, 1) - timedelta(days=1)
    cycles.append({
        "cycle_id": i,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "budget_total": round(RNG.uniform(3_200_000, 4_100_000), 2),  # RWF
        "budget_currency": "RWF",
        "notes": "",
    })

# ---------------------------------------------------------------------------
# 3. Caseworkers, deliberately varied in strictness/override rate so
#    contraction evaluation and override monitoring have something to bite on.
# ---------------------------------------------------------------------------
CASEWORKER_NAMES = [
    "J. Uwase", "E. Mugisha", "A. Ingabire", "P. Nshimiyimana", "C. Mukamana",
    "D. Habimana", "F. Uwimana", "G. Niyonsenga", "B. Mutesi", "S. Bizimana",
]
caseworkers = []
caseworker_strictness = {}  # higher = harsher (raises effective cutoff for that caseworker)
caseworker_override_rate = {}
for i, name in enumerate(CASEWORKER_NAMES, start=1):
    caseworkers.append({
        "caseworker_id": i,
        "display_name": name,
        "region": RNG.choice(["Kigali", "Southern", "Western", "Northern", "Eastern"]),
        "active": "true",
    })
    caseworker_strictness[i] = RNG.normal(0, 0.12)
    caseworker_override_rate[i] = float(np.clip(RNG.uniform(0.02, 0.14), 0.02, 0.2))

# ---------------------------------------------------------------------------
# 4. Households + latent poverty severity
# ---------------------------------------------------------------------------
household_ids = [str(uuid.uuid4()) for _ in range(N_HOUSEHOLDS)]
household_area = RNG.choice(area_codes, size=N_HOUSEHOLDS, p=area_weights)

area_effect = np.array([area_deprivation[a] for a in household_area])
individual_effect = RNG.normal(0, 0.9, size=N_HOUSEHOLDS)
severity = np.clip(1.8 * (area_effect - 0.5) + individual_effect, -3, 3)  # z-like, higher = worse off
severity_pct = (severity - severity.min()) / (severity.max() - severity.min())

households = []
for i in range(N_HOUSEHOLDS):
    reg_offset_days = int(RNG.integers(30, 900))
    households.append({
        "household_id": household_ids[i],
        "area_code": household_area[i],
        "registered_at": (TODAY - timedelta(days=reg_offset_days)).isoformat(),
        "notes": "",
    })

# ---------------------------------------------------------------------------
# 5. Household surveys — every field derived from severity + area + noise
# ---------------------------------------------------------------------------
EDU_LEVELS = ["none", "primary", "secondary", "vocational", "tertiary"]
ROOF = ["iron_sheet", "tile", "thatch", "concrete"]
WALL = ["burnt_brick", "mud_brick", "concrete_block", "wood"]
FLOOR = ["earth", "cement", "tile"]
WATER = ["piped_indoor", "public_tap", "borehole", "surface_water", "vendor"]
SANITATION = ["flush_private", "pit_latrine_private", "pit_latrine_shared", "none"]
COOKING_FUEL = ["electricity", "gas", "charcoal", "firewood"]
EMPLOYMENT = ["formal", "informal", "self_employed", "unemployed", "unable_to_work"]

surveys = []
protected = []

for i in range(N_HOUSEHOLDS):
    s = severity[i]
    pct = severity_pct[i]
    urban = area_urban[household_area[i]] == "urban"

    household_size = int(np.clip(round(RNG.normal(5.2 - 1.3 * (1 - pct) - (1.0 if urban else 0), 1.6)), 1, 14))
    children_under_5 = int(np.clip(RNG.binomial(household_size, 0.10 + 0.10 * pct), 0, household_size))
    members_over_65 = int(np.clip(RNG.binomial(household_size, 0.06 + 0.04 * pct), 0, household_size - children_under_5))

    # mild, deliberate synthetic correlation for the fairness-audit demo
    female_headed = bool(RNG.random() < (0.22 + 0.18 * pct))
    single_caregiver = bool(female_headed and RNG.random() < 0.35)

    edu_probs = np.array([
        0.05 + 0.20 * pct, 0.30 + 0.15 * pct, 0.35 - 0.10 * pct,
        0.20 - 0.10 * pct, 0.10 - 0.08 * pct,
    ])
    edu_probs = np.clip(edu_probs, 0.01, None)
    edu_probs /= edu_probs.sum()
    education_head = RNG.choice(EDU_LEVELS, p=edu_probs)
    literacy_head = bool(education_head != "none" and RNG.random() < 0.95) or bool(RNG.random() < 0.25)

    roof = RNG.choice(ROOF, p=_norm([0.35 - 0.2*pct, 0.05, 0.15 + 0.35*pct, 0.45 - 0.15*pct]))
    wall = RNG.choice(WALL, p=_norm([0.35 - 0.15*pct, 0.30 + 0.25*pct, 0.30 - 0.15*pct, 0.05 + 0.05*pct]))
    floor = RNG.choice(FLOOR, p=_norm([0.20 + 0.55*pct, 0.55 - 0.35*pct, 0.25 - 0.20*pct]))
    rooms = int(np.clip(round(RNG.normal(3.2 - 1.2 * pct, 1.0)), 1, 10))

    tenure_probs = _norm([0.28 - 0.10*pct, 0.05, 0.20 - 0.05*pct, 0.07, 0.30 + 0.10*pct, 0.10 + 0.05*pct])
    tenure = RNG.choice(["owned", "mortgaged", "private_rent", "social", "informal", "temporary"], p=tenure_probs)

    water = RNG.choice(WATER, p=_norm([0.45 - 0.35*pct if urban else 0.10, 0.25, 0.20 + 0.10*pct, 0.05 + 0.15*pct, 0.05 + 0.05*pct]))
    sanitation = RNG.choice(SANITATION, p=_norm([0.25 - 0.20*pct if urban else 0.03, 0.45 - 0.15*pct, 0.25 + 0.15*pct, 0.05 + 0.15*pct]))
    electricity = bool(RNG.random() < (0.85 if urban else 0.35) - 0.3 * pct)
    cooking_fuel = RNG.choice(COOKING_FUEL, p=_norm([0.25 if urban else 0.03, 0.20 - 0.10*pct, 0.30, 0.25 + 0.10*pct]))

    asset_p = {
        "asset_phone": 0.90 - 0.35 * pct, "asset_radio": 0.55 - 0.15 * pct,
        "asset_tv": 0.45 - 0.35 * pct, "asset_fridge": 0.30 - 0.28 * pct,
        "asset_washing_machine": 0.10 - 0.09 * pct, "asset_bicycle": 0.35 - 0.10 * pct,
        "asset_motorcycle": 0.20 - 0.12 * pct, "asset_car": 0.08 - 0.075 * pct,
    }
    assets = {k: bool(RNG.random() < max(v, 0.01)) for k, v in asset_p.items()}

    is_rural = not urban
    livestock_count = int(max(0, round(RNG.normal(3 - 2 * pct, 3)))) if is_rural else None
    land_area = round(max(0.0, RNG.normal(0.6 - 0.3 * pct, 0.5)), 2) if is_rural else None

    emp_probs = _norm([0.22 - 0.15*pct, 0.35 + 0.05*pct, 0.20, 0.15 + 0.10*pct, 0.08 + 0.05*pct])
    employment_type = RNG.choice(EMPLOYMENT, p=emp_probs)
    earners_count = int(np.clip(round(RNG.normal(1.6 - 0.5 * pct, 0.8)), 0, household_size))
    hours_worked = round(max(0, RNG.normal(38 - 10 * pct, 10)), 1) if employment_type != "unemployed" else round(max(0, RNG.normal(4, 4)), 1)

    base_income = 95_000 * math.exp(-1.6 * pct) * area_housing_cost[household_area[i]]
    monthly_income = round(max(3_000, RNG.normal(base_income, base_income * 0.28)), 2)
    income_std_12m = round(max(0, monthly_income * (0.10 + 0.35 * pct) * abs(RNG.normal(1, 0.3))), 2)
    income_seasonality = round(np.clip(income_std_12m / max(monthly_income, 1) + RNG.normal(0, 0.03), 0, 1.5), 4)

    essential_base = (18_000 * household_size) * area_housing_cost[household_area[i]]
    essential_costs = round(max(5_000, RNG.normal(essential_base, essential_base * 0.15)), 2)

    food_security_score = int(np.clip(round(RNG.normal(1.5 + 5.5 * pct, 1.8)), 0, 8))  # FIES-like, higher = worse
    chronic_illness = bool(RNG.random() < 0.12 + 0.10 * pct)
    disability_in_household = bool(RNG.random() < 0.06 + 0.05 * pct)
    dependents_requiring_care = int(np.clip(RNG.binomial(household_size, 0.05 + 0.05 * pct), 0, household_size))

    shocks = {
        "shock_bereavement_12m": RNG.random() < 0.05,
        "shock_serious_illness_12m": RNG.random() < (0.06 + 0.10 * pct),
        "shock_job_loss_12m": RNG.random() < (0.05 + 0.15 * pct),
        "shock_eviction_12m": RNG.random() < (0.02 + 0.08 * pct) and not urban == False,
        "shock_displacement_12m": RNG.random() < 0.015,
        "shock_disaster_12m": RNG.random() < (0.03 + 0.05 * pct),
        "shock_crop_failure_12m": (RNG.random() < (0.04 + 0.20 * pct)) if is_rural else False,
    }
    shocks = {k: bool(v) for k, v in shocks.items()}

    consumption_pc = round(max(1_500, (monthly_income * 0.75 + 5_000) / max(household_size, 1) * math.exp(RNG.normal(0, 0.08))), 2)

    survey_date = (TODAY - timedelta(days=int(RNG.integers(1, 400)))).isoformat()

    surveys.append({
        "household_id": household_ids[i], "survey_date": survey_date,
        "household_size": household_size, "children_under_5": children_under_5,
        "members_over_65": members_over_65, "female_headed": female_headed,
        "single_caregiver": single_caregiver, "education_head": education_head,
        "literacy_head": literacy_head, "roof_material": roof, "wall_material": wall,
        "floor_material": floor, "rooms": rooms, "tenure": tenure, "water_source": water,
        "sanitation_type": sanitation, "electricity": electricity, "cooking_fuel": cooking_fuel,
        **assets, "livestock_count": livestock_count, "land_area": land_area,
        "employment_type": employment_type, "earners_count": earners_count,
        "hours_worked": hours_worked, "monthly_income": monthly_income,
        "income_std_12m": income_std_12m, "income_seasonality": income_seasonality,
        "essential_costs": essential_costs, "food_security_score": food_security_score,
        "chronic_illness": chronic_illness, "disability_in_household": disability_in_household,
        "dependents_requiring_care": dependents_requiring_care, **shocks,
        "consumption_pc": consumption_pc,
    })

    # --- protected attributes (audit-only; mild synthetic correlation) ---
    age_band = RNG.choice(
        ["18-25", "26-35", "36-45", "46-55", "56-65", "66+"],
        p=_norm([0.08, 0.22, 0.24 - 0.05*pct, 0.20, 0.14 + 0.03*pct, 0.12 + 0.05*pct]),
    )
    protected.append({
        "household_id": household_ids[i],
        "ethnicity": "not_disclosed",  # placeholder category; real collection is a policy decision, not this script's
        "gender_head": "female" if female_headed else "male",
        "disability": "yes" if disability_in_household and RNG.random() < 0.7 else "no",
        "age_band": age_band,
        "religion": RNG.choice(["christian", "muslim", "other", "none"], p=[0.85, 0.06, 0.05, 0.04]),
        "nationality": "rwandan" if RNG.random() < 0.97 else "other",
        "immigration_status": "n/a",
    })

# ---------------------------------------------------------------------------
# 6. Applications — generated cycle by cycle (chronological) so that
#    prior_applications_count / days_since_last_application are consistent,
#    and so a simple, imperfect historical "caseworker" policy can be
#    simulated to rank applicants under each cycle's fixed budget.
# ---------------------------------------------------------------------------
survey_by_household = {s["household_id"]: s for s in surveys}
severity_by_household = {household_ids[i]: severity_pct[i] for i in range(N_HOUSEHOLDS)}
urban_by_household = {household_ids[i]: (area_urban[household_area[i]] == "urban") for i in range(N_HOUSEHOLDS)}

NEED_CATEGORIES = ["rent_arrears", "medical", "utilities", "food", "funeral", "childcare", "education", "other"]
REFERRAL_SOURCES = ["self", "ngo_partner", "caseworker_outreach", "community_leader", "prior_beneficiary"]

history = {hid: [] for hid in household_ids}       # list of submitted_at dates
last_status = {hid: None for hid in household_ids}  # last resolved status, modulates reapplication

applications = []
application_reviews = []
awards = []

app_id_counter = 1
review_id_counter = 1
award_id_counter = 1

EDU_IDX = {lvl: i for i, lvl in enumerate(EDU_LEVELS)}

for cycle in cycles:
    cycle_start = date.fromisoformat(cycle["period_start"])
    cycle_end = date.fromisoformat(cycle["period_end"])
    cycle_age_days = (TODAY - cycle_end).days

    # --- decide who applies this cycle ---
    applicants_this_cycle = []
    for hid in household_ids:
        pct = severity_by_household[hid]
        p = 0.05 + 0.20 * pct
        if last_status[hid] in ("deferred", "appealed"):
            p *= 2.0
        elif last_status[hid] in ("auto_approved", "audit_approved", "awarded"):
            p *= 0.4
        if history[hid]:
            days_since = (cycle_start - history[hid][-1]).days
            if days_since < 45:
                p *= 0.1
        if RNG.random() < min(p, 0.9):
            applicants_this_cycle.append(hid)

    if not applicants_this_cycle:
        continue

    batch = []
    for hid in applicants_this_cycle:
        sv = survey_by_household[hid]
        pct = severity_by_household[hid]
        urban = urban_by_household[hid]
        submitted_at = cycle_start + timedelta(days=int(RNG.integers(0, (cycle_end - cycle_start).days + 1)))

        deficit = max(0.0, sv["essential_costs"] - sv["monthly_income"])

        # need_category driven by the household's recorded shocks, else generic mix
        if sv["shock_eviction_12m"] and RNG.random() < 0.6:
            need_category = "rent_arrears"
        elif (sv["shock_serious_illness_12m"] or sv["chronic_illness"]) and RNG.random() < 0.55:
            need_category = "medical"
        elif sv["shock_bereavement_12m"] and RNG.random() < 0.6:
            need_category = "funeral"
        elif (sv["shock_crop_failure_12m"] or sv["food_security_score"] >= 6) and RNG.random() < 0.5:
            need_category = "food"
        elif sv["dependents_requiring_care"] > 0 and RNG.random() < 0.35:
            need_category = "childcare"
        elif sv["children_under_5"] + (sv["household_size"] - sv["children_under_5"]) > 0 and RNG.random() < 0.12:
            need_category = "education"
        else:
            need_category = RNG.choice(NEED_CATEGORIES, p=_norm([0.15, 0.15, 0.15, 0.12, 0.05, 0.08, 0.12, 0.18]))

        amount_requested = round(max(3_000, deficit * RNG.uniform(0.4, 1.3) + RNG.normal(0, 5_000)), 2)
        stated_need_amount = round(max(0, amount_requested * RNG.uniform(0.85, 1.15)), 2)
        days_since_hardship_onset = int(max(0, RNG.exponential(20) + (15 if sv["disability_in_household"] else 0)))

        prior_count = len(history[hid])
        days_since_last = (submitted_at - history[hid][-1]).days if history[hid] else None

        referral_source = RNG.choice(REFERRAL_SOURCES, p=_norm([0.40, 0.20, 0.20, 0.12, 0.08]))
        channel_probs = _norm([0.35 if urban else 0.10, 0.20, 0.35 if not urban else 0.45, 0.10])
        application_channel = RNG.choice(["online", "phone", "in_person", "caseworker_submitted"], p=channel_probs)

        edu_idx = EDU_IDX[sv["education_head"]]
        completeness_base = 0.55 + 0.08 * edu_idx + (0.12 if application_channel == "online" else -0.05 if application_channel == "in_person" else 0.0)
        application_completeness = round(float(np.clip(RNG.normal(completeness_base, 0.12), 0.05, 1.0)), 3)
        documentation_provided = bool(RNG.random() < np.clip(application_completeness + 0.05, 0, 1))

        caseworker_id = int(RNG.choice([c["caseworker_id"] for c in caseworkers]))

        # a deliberately imperfect proxy for how a human caseworker scored this
        # application historically — correlated with true need, but with noise
        # and a mild, worth-auditing tilt toward well-documented applications.
        decision_score = (
            deficit
            + 15_000 * (1 if need_category in ("medical", "food") else 0)
            + 8_000 * application_completeness
            + 5_000 * (1 if documentation_provided else 0)
            + RNG.normal(0, 12_000)
            + caseworker_strictness[caseworker_id] * -40_000
        )

        app_id = app_id_counter
        app_id_counter += 1

        batch.append({
            "application_id": app_id, "household_id": hid, "cycle_id": cycle["cycle_id"],
            "caseworker_id": caseworker_id, "submitted_at": submitted_at, "amount_requested": amount_requested,
            "need_category": need_category, "stated_need_amount": stated_need_amount,
            "days_since_hardship_onset": days_since_hardship_onset, "prior_applications_count": prior_count,
            "days_since_last_application": days_since_last, "referral_source": referral_source,
            "application_channel": application_channel, "application_completeness": application_completeness,
            "documentation_provided": documentation_provided, "decision_score": decision_score,
            "deficit": deficit, "cycle_age_days": cycle_age_days,
        })
        history[hid].append(submitted_at)

    # --- rank this cycle's batch under the fixed budget ---
    # Mirrors the design spec's allocate(): rank by score, walk the
    # cumulative requested amount until the budget runs out. This ties
    # funded volume directly to the budget instead of a guessed average
    # award size, so total_awarded tracks budget_total by construction.
    batch.sort(key=lambda r: r["decision_score"], reverse=True)
    n = len(batch)
    cum_cost = 0.0
    cutoff_idx = n
    for idx, rec in enumerate(batch):
        cum_cost += rec["amount_requested"]
        if cum_cost > cycle["budget_total"]:
            cutoff_idx = idx
            break
    auto_cut = max(0, int(cutoff_idx * 0.80))
    review_cut = min(n, int(cutoff_idx * 1.15))

    for rank, rec in enumerate(batch):
        hid = rec["household_id"]
        app_id = rec["application_id"]
        withdrawn = RNG.random() < 0.015

        if withdrawn:
            status = "withdrawn"
        elif rank < auto_cut:
            status = "auto_approved"
        elif rank < review_cut:
            # human review band: real reviewers sometimes disagree with the score
            reviewed_at = rec["submitted_at"] + timedelta(days=int(RNG.integers(1, 10)))
            approve = RNG.random() < 0.55
            overridden = RNG.random() < caseworker_override_rate[rec["caseworker_id"]]
            final_decision = "approved" if approve else "deferred"
            status = "awarded" if approve else ("appealed" if RNG.random() < 0.3 else "deferred")
            application_reviews.append({
                "review_id": review_id_counter, "application_id": app_id,
                "caseworker_id": rec["caseworker_id"], "reviewed_at": reviewed_at.isoformat(),
                "initial_band": "human_review", "final_decision": final_decision,
                "overridden": overridden, "notes": "",
            })
            review_id_counter += 1
        else:
            status = "deferred"
            # randomised audit sample among below-cutoff applicants
            if RNG.random() < 0.04:
                status = "audit_approved"
            elif rec["cycle_age_days"] > 90 and RNG.random() < 0.5:
                status = "closed"
            elif RNG.random() < 0.08:
                status = "appealed"

        last_status[hid] = status

        applications.append({
            "application_id": app_id, "household_id": hid, "cycle_id": rec["cycle_id"],
            "caseworker_id": rec["caseworker_id"], "submitted_at": rec["submitted_at"].isoformat() + "T09:00:00",
            "amount_requested": rec["amount_requested"], "need_category": rec["need_category"],
            "stated_need_amount": rec["stated_need_amount"],
            "days_since_hardship_onset": rec["days_since_hardship_onset"],
            "prior_applications_count": rec["prior_applications_count"],
            "days_since_last_application": rec["days_since_last_application"],
            "referral_source": rec["referral_source"], "application_channel": rec["application_channel"],
            "application_completeness": rec["application_completeness"],
            "documentation_provided": rec["documentation_provided"], "status": status,
        })

        if status in ("auto_approved", "audit_approved", "awarded"):
            award_amount = round(min(rec["amount_requested"], rec["stated_need_amount"] * RNG.uniform(0.9, 1.0)), 2)
            award_date = rec["submitted_at"] + timedelta(days=int(RNG.integers(2, 21)))
            outcome_followup_days = int(RNG.integers(60, 240)) if RNG.random() < 0.3 else None
            awards.append({
                "award_id": award_id_counter, "application_id": app_id, "household_id": hid,
                "award_amount": award_amount, "award_date": award_date.isoformat(),
                "need_category": rec["need_category"],
                "outcome_followup_days": outcome_followup_days if outcome_followup_days is not None else "",
            })
            award_id_counter += 1

print(f"Generated: {len(households)} households, {len(applications)} applications, "
      f"{len(awards)} awards, {len(application_reviews)} reviews")

# ---------------------------------------------------------------------------
# 7. Write CSVs
# ---------------------------------------------------------------------------
def write_csv(filename, rows, fieldnames):
    path = OUT_DIR / filename
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"  wrote {path} ({len(rows)} rows)")


write_csv("area_reference.csv", areas, list(areas[0].keys()))
write_csv("funding_cycles.csv", cycles, list(cycles[0].keys()))
write_csv("caseworkers.csv", caseworkers, list(caseworkers[0].keys()))
write_csv("households.csv", households, list(households[0].keys()))
write_csv("household_surveys.csv", surveys, list(surveys[0].keys()))
write_csv("protected_attributes.csv", protected, list(protected[0].keys()))
write_csv("applications.csv", applications, list(applications[0].keys()))
write_csv("application_reviews.csv", application_reviews, list(application_reviews[0].keys()))
write_csv("awards.csv", awards, list(awards[0].keys()))

print("Done.")
