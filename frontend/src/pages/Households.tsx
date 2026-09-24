import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi, useApiMutation } from "../api/hooks";
import type { Area, HouseholdDetail, HouseholdListItem, Survey } from "../api/types";
import { Button, Card, Check, Empty, ErrorBox, Field, Input, Loading, Modal, PageHeader, Pill, Select, StatusPill, TextArea } from "../components/ui";
import { day, NEED_LABEL, num, rwf, shortId } from "../lib/format";
import { usePageFilters, type FilterKey } from "../state/filters";

const KEYS: FilterKey[] = ["region", "area_code"];
const PAGE = 20;

// Option values are exactly the categories the need model was trained on;
// anything else would be an unseen level at scoring time.
const opts = (...v: string[]): [string, string][] => v.map((x) => [x, x.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())]);
const CHOICES: Record<string, [string, string][]> = {
  education_head: opts("none", "primary", "secondary", "vocational", "tertiary"),
  roof_material: opts("iron_sheet", "tile", "thatch", "concrete"),
  wall_material: opts("burnt_brick", "mud_brick", "concrete_block", "wood"),
  floor_material: opts("earth", "cement", "tile"),
  tenure: opts("owned", "mortgaged", "private_rent", "social", "informal", "temporary"),
  water_source: opts("piped_indoor", "public_tap", "borehole", "surface_water", "vendor"),
  sanitation_type: opts("flush_private", "pit_latrine_private", "pit_latrine_shared", "none"),
  cooking_fuel: opts("electricity", "gas", "charcoal", "firewood"),
  employment_type: opts("formal", "informal", "self_employed", "unemployed", "unable_to_work"),
};
const ASSETS: [string, string][] = [["asset_phone", "Phone"], ["asset_radio", "Radio"], ["asset_tv", "TV"], ["asset_fridge", "Fridge"],
  ["asset_washing_machine", "Washing machine"], ["asset_bicycle", "Bicycle"], ["asset_motorcycle", "Motorcycle"], ["asset_car", "Car"]];
const SHOCKS: [string, string][] = [["shock_bereavement_12m", "Death in the family"], ["shock_serious_illness_12m", "Serious illness"],
  ["shock_job_loss_12m", "Job loss"], ["shock_eviction_12m", "Eviction"], ["shock_displacement_12m", "Displacement"],
  ["shock_disaster_12m", "Disaster"], ["shock_crop_failure_12m", "Crop failure (rural)"]];

export default function Households() {
  const params = usePageFilters(KEYS);
  const [search, setSearch] = useSearchParams();
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [page, setPage] = useState(0);
  const selected = search.get("id");
  const registering = search.get("new") === "1";

  useEffect(() => { const t = setTimeout(() => setTerm(q), 300); return () => clearTimeout(t); }, [q]);
  useEffect(() => setPage(0), [params, term]);
  const list = useApi<{ total: number; items: HouseholdListItem[] }>("/households", { ...params, q: term, limit: PAGE, offset: page * PAGE }, { keepPrevious: true });
  const current = selected ?? list.data?.items[0]?.household_id ?? null;

  return (
    <>
      <PageHeader title="Households" subtitle="Register households and record their survey waves · applications are scored on the latest wave before they were submitted"
        actions={<Button kind="primary" onClick={() => setSearch({ new: "1" })}>+ Register household</Button>} />
      <div className="split">
        <div className="stack" style={{ gap: 8, maxHeight: "calc(100vh - 150px)", overflowY: "auto", paddingRight: 4, position: "sticky", top: 16 }}>
          <Input type="search" placeholder="Household ID or district" aria-label="Search households" value={q} onChange={(e) => setQ(e.target.value)} />
          {list.error ? <ErrorBox error={list.error} /> : !list.data ? <Loading lines={6} /> : list.data.items.length === 0 ? <Empty>No households match.</Empty> :
            list.data.items.map((h) => (
              <button key={h.household_id} type="button" className={`item ${h.household_id === current ? "selected" : ""}`} onClick={() => setSearch({ id: h.household_id })}>
                <span className="title"><span className="mono">{shortId(h.household_id)}</span><span className="small muted" style={{ fontWeight: 500 }}>{h.area_name}</span></span>
                <span className="meta">
                  {h.household_size ? `${h.household_size} ${h.household_size === 1 ? "person" : "people"} · ` : ""}{h.last_survey ? `surveyed ${day(h.last_survey)}` : "no survey yet"} · {num(h.applications_count)} application{h.applications_count === 1 ? "" : "s"}{h.awards_count ? " · helped" : ""}
                </span>
              </button>
            ))}
          {list.data && list.data.total > PAGE && (
            <div className="row between small muted">
              <span>{num(page * PAGE + 1)}–{num(Math.min((page + 1) * PAGE, list.data.total))} of {num(list.data.total)}</span>
              <div className="row"><Button small disabled={page === 0} onClick={() => setPage(page - 1)}>Prev</Button><Button small disabled={(page + 1) * PAGE >= list.data.total} onClick={() => setPage(page + 1)}>Next</Button></div>
            </div>
          )}
        </div>
        {current ? <HouseholdPanel key={current} id={current} /> : <Card><Empty>Select or register a household.</Empty></Card>}
      </div>
      {registering && <RegisterModal onClose={() => setSearch({})} onCreated={(id) => setSearch({ id, tab: "survey" })} />}
    </>
  );
}

function RegisterModal({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const areas = useApi<Area[]>("/areas").data;
  const [area, setArea] = useState("");
  const [notes, setNotes] = useState("");
  const create = useApiMutation<{ area_code: string; notes: string | null }, { household_id: string }>("POST", () => "/households", ["/households"]);
  return (
    <Modal title="Register a household" onClose={onClose} actions={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button kind="primary" disabled={!area || create.isPending}
        onClick={() => create.mutate({ area_code: area, notes: notes.trim() || null }, { onSuccess: (h) => onCreated(h.household_id) })}>
        Register and add survey
      </Button>
    </>}>
      <Field label="District"><Select value={area} onChange={(e) => setArea(e.target.value)} placeholder="Choose…"
        options={(areas ?? []).map((a) => [a.area_code, `${a.area_name} · ${a.region} · ${a.urban_rural}`])} /></Field>
      <Field label="Notes" hint="Optional. Never used for scoring."><TextArea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
      {create.error && <ErrorBox error={create.error} />}
      <span className="small muted">Next you record the household's first survey wave.</span>
    </Modal>
  );
}

type Tab = "profile" | "survey" | "applications" | "audit";

function HouseholdPanel({ id }: { id: string }) {
  const [search, setSearch] = useSearchParams();
  const tab = (search.get("tab") as Tab) || "profile";
  const setTab = (t: Tab) => setSearch({ id, tab: t });
  const hh = useApi<HouseholdDetail>(`/households/${id}`);
  if (hh.error) return <ErrorBox error={hh.error} />;
  if (!hh.data) return <Card><Loading lines={8} /></Card>;
  const h = hh.data;
  const latest = h.surveys[0];
  const helped = h.applications.filter((a) => ["auto_approved", "audit_approved", "awarded"].includes(a.status)).length;
  return (
    <Card>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack" style={{ gap: 4 }}>
          <h2 className="serif" style={{ fontSize: 24, fontWeight: 600 }}><span className="mono" style={{ fontSize: 22 }}>{shortId(h.household_id)}</span> · {h.area_name}</h2>
          <span className="muted">{h.region} region · {h.urban_rural} · registered {day(h.registered_at)} · {latest ? `last survey ${day(latest.survey_date)}` : "no survey yet"} · helped {helped} time{helped === 1 ? "" : "s"}</span>
        </div>
        <Link className="btn primary" to={`/applications/new?household=${h.household_id}`}>+ New application</Link>
      </div>
      <div className="underline-tabs" role="tablist">
        {([["profile", "Profile"], ["survey", "Record a survey wave"], ["applications", `Applications · ${h.applications.length}`], ["audit", "Audit questions"]] as [Tab, string][]).map(([t, label]) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{label}</button>
        ))}
      </div>
      {tab === "profile" && <Profile h={h} onSurvey={() => setTab("survey")} />}
      {tab === "survey" && <SurveyForm householdId={h.household_id} previous={latest} onSaved={() => setTab("profile")} />}
      {tab === "applications" && (
        h.applications.length === 0 ? <Empty>No applications yet.</Empty> : (
          <div>
            {h.applications.map((a) => (
              <Link key={a.application_id} to={`/applications/${a.application_id}`} className="list-row" style={{ gridTemplateColumns: "80px 110px minmax(0,1fr) auto", color: "var(--ink)", textDecoration: "none" }}>
                <b>#{a.application_id}</b><span className="muted">{day(a.submitted_at)}</span>
                <span>{NEED_LABEL[a.need_category]} · {rwf(a.amount_requested)}</span><StatusPill status={a.status} />
              </Link>
            ))}
          </div>
        ))}
      {tab === "audit" && <AuditForm householdId={h.household_id} answered={h.audit_questions_answered} />}
    </Card>
  );
}

function Profile({ h, onSurvey }: { h: HouseholdDetail; onSurvey: () => void }) {
  const s = h.surveys[0];
  if (!s) return <div className="stack"><div className="callout">No survey yet — applications cannot be scored without one.</div><div><Button kind="primary" onClick={onSurvey}>Record the first survey wave</Button></div></div>;
  const row = (label: string, value: string) => <div className="row between" style={{ borderTop: "1px solid var(--line)", padding: "8px 0" }}><span className="muted">{label}</span><span style={{ fontWeight: 500, textAlign: "right" }}>{value}</span></div>;
  const yes = (k: string) => s[k] === true;
  const assets = ASSETS.filter(([k]) => yes(k)).map(([, l]) => l);
  const shocks = SHOCKS.filter(([k]) => yes(k)).map(([, l]) => l);
  return (
    <div className="grid cols-2">
      <div>
        <div className="row between"><b>Latest survey · {day(s.survey_date)}</b><span className="small muted">{h.surveys.length} wave{h.surveys.length === 1 ? "" : "s"}</span></div>
        {row("People", `${s.household_size} · ${s.children_under_5} under five · ${s.members_over_65} over 65`)}
        {row("Monthly income", rwf(s.monthly_income))}
        {row("Essential costs", rwf(s.essential_costs))}
        {row("Employment", String(s.employment_type ?? "—").replace(/_/g, " "))}
        {row("Food insecurity", s.food_security_score !== null ? `${s.food_security_score} of 8` : "—")}
      </div>
      <div>
        {row("Housing", [s.roof_material, s.wall_material, s.floor_material].filter(Boolean).join(" · ").replace(/_/g, " ") || "—")}
        {row("Rooms · tenure", `${s.rooms ?? "—"} · ${String(s.tenure ?? "—").replace(/_/g, " ")}`)}
        {row("Assets", assets.join(", ") || "None")}
        {row("Shocks this year", shocks.join(", ") || "None")}
        <div style={{ paddingTop: 10 }}><Button onClick={onSurvey}>Record a new wave</Button></div>
      </div>
    </div>
  );
}

type FormState = Record<string, string | boolean>;
const NUMERIC = ["household_size", "children_under_5", "members_over_65", "rooms", "livestock_count", "land_area", "earners_count",
  "hours_worked", "monthly_income", "income_std_12m", "income_seasonality", "essential_costs", "food_security_score", "dependents_requiring_care"];
const BOOLEAN = ["female_headed", "single_caregiver", "literacy_head", "electricity", "chronic_illness", "disability_in_household",
  ...ASSETS.map(([k]) => k), ...SHOCKS.map(([k]) => k)];

function SurveyForm({ householdId, previous, onSaved }: { householdId: string; previous?: Survey; onSaved: () => void }) {
  // A new wave starts from the previous one: the caseworker changes what changed.
  const [f, setF] = useState<FormState>(() => {
    const init: FormState = { survey_date: new Date().toISOString().slice(0, 10) };
    for (const k of [...NUMERIC, ...Object.keys(CHOICES)]) init[k] = previous?.[k] !== null && previous?.[k] !== undefined ? String(previous[k]) : "";
    for (const k of BOOLEAN) init[k] = previous?.[k] === true;
    return init;
  });
  const save = useApiMutation<Record<string, unknown>>("POST", () => `/households/${householdId}/surveys`, ["/households", "/applications"]);
  const set = (k: string, v: string | boolean) => setF((p) => ({ ...p, [k]: v }));
  const numField = (k: string, label: string, hint?: string) => (
    <Field label={label} hint={hint}><Input type="number" min={0} step="any" value={String(f[k])} onChange={(e) => set(k, e.target.value)} required={k === "household_size"} /></Field>
  );
  const choice = (k: string, label: string) => <Field label={label}><Select value={String(f[k])} onChange={(e) => set(k, e.target.value)} placeholder="Choose…" options={CHOICES[k]} /></Field>;
  const check = (k: string, label: string) => <Check key={k} label={label} checked={Boolean(f[k])} onChange={(e) => set(k, e.target.checked)} />;

  function submit(e: FormEvent) {
    e.preventDefault();
    const body: Record<string, unknown> = { survey_date: f.survey_date };
    for (const k of NUMERIC) if (f[k] !== "") body[k] = Number(f[k]);
    for (const k of Object.keys(CHOICES)) if (f[k] !== "") body[k] = f[k];
    for (const k of BOOLEAN) body[k] = f[k];
    save.mutate(body, { onSuccess: onSaved });
  }

  return (
    <form className="stack" style={{ gap: 14 }} onSubmit={submit}>
      <div className="grid" style={{ gridTemplateColumns: "220px 1fr", alignItems: "end" }}>
        <Field label="Survey date"><Input type="date" value={String(f.survey_date)} onChange={(e) => set("survey_date", e.target.value)} required /></Field>
        <span className="small muted" style={{ paddingBottom: 10 }}>{previous ? `Pre-filled from the ${day(previous.survey_date)} wave. ` : ""}A new wave never overwrites an old one.</span>
      </div>
      <fieldset className="group"><legend>Who lives here</legend>
        <div className="grid cols-4">{numField("household_size", "People in household")}{numField("children_under_5", "Children under 5")}{numField("members_over_65", "Members over 65")}{choice("education_head", "Education of head")}</div>
        <div className="row wrap" style={{ gap: 18, marginTop: 10 }}>{check("female_headed", "Female-headed")}{check("single_caregiver", "Single caregiver")}{check("literacy_head", "Head can read and write")}</div>
      </fieldset>
      <fieldset className="group"><legend>Housing</legend>
        <div className="grid cols-4">{choice("roof_material", "Roof")}{choice("wall_material", "Walls")}{choice("floor_material", "Floor")}{numField("rooms", "Rooms")}
          {choice("tenure", "Tenure")}{choice("water_source", "Water source")}{choice("sanitation_type", "Sanitation")}{choice("cooking_fuel", "Cooking fuel")}</div>
        <div style={{ marginTop: 10 }}>{check("electricity", "Electricity")}</div>
      </fieldset>
      <div className="grid cols-2">
        <fieldset className="group"><legend>Assets owned</legend><div className="grid cols-2">{ASSETS.map(([k, l]) => check(k, l))}</div></fieldset>
        <fieldset className="group"><legend>Shocks in the last 12 months</legend><div className="grid cols-2">{SHOCKS.map(([k, l]) => check(k, l))}</div></fieldset>
      </div>
      <fieldset className="group"><legend>Income and costs</legend>
        <div className="grid cols-3">{choice("employment_type", "Main employment")}{numField("earners_count", "Earners")}{numField("hours_worked", "Hours worked a week")}
          {numField("monthly_income", "Monthly income (RWF)")}{numField("income_std_12m", "Income swing (RWF)", "How much income varies over 12 months")}{numField("essential_costs", "Essential costs (RWF / month)")}</div>
      </fieldset>
      <fieldset className="group"><legend>Wellbeing and land</legend>
        <div className="grid cols-4">{numField("food_security_score", "Food insecurity (FIES 0–8)")}{numField("dependents_requiring_care", "Dependants needing care")}{numField("livestock_count", "Livestock (rural)")}{numField("land_area", "Land, hectares (rural)")}</div>
        <div className="row wrap" style={{ gap: 18, marginTop: 10 }}>{check("chronic_illness", "Chronic illness in household")}{check("disability_in_household", "Disability in household")}</div>
      </fieldset>
      {save.error && <ErrorBox error={save.error} />}
      <div className="row between">
        <span className="small muted">Consumption per person is recorded later, only for funded or audit-sample households.</span>
        <div className="row"><Button onClick={onSaved}>Cancel</Button><Button kind="primary" type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save survey wave"}</Button></div>
      </div>
    </form>
  );
}

const AUDIT: [string, string, [string, string][]][] = [
  ["gender_head", "Gender of household head", [["female", "Female"], ["male", "Male"], ["other", "Other"]]],
  ["age_band", "Age of household head", [["18-25", "18–25"], ["26-35", "26–35"], ["36-45", "36–45"], ["46-55", "46–55"], ["56-65", "56–65"], ["66+", "66+"]]],
  ["disability", "Disability (head)", [["yes", "Yes"], ["no", "No"]]],
  ["ethnicity", "Ethnicity", []],
  ["religion", "Religion", [["christian", "Christian"], ["muslim", "Muslim"], ["other", "Other"], ["none", "None"]]],
  ["nationality", "Nationality", [["rwandan", "Rwandan"], ["other", "Other"]]],
  ["immigration_status", "Immigration status", [["n/a", "Not applicable"], ["refugee", "Refugee"], ["other", "Other"]]],
];

function AuditForm({ householdId, answered }: { householdId: string; answered: boolean }) {
  const [f, setF] = useState<Record<string, string>>({});
  const save = useApiMutation<Record<string, string | null>>("PUT", () => `/households/${householdId}/protected-attributes`, ["/households"]);
  return (
    <form className="stack" style={{ gap: 14 }} onSubmit={(e) => {
      e.preventDefault();
      save.mutate(Object.fromEntries(AUDIT.map(([k]) => [k, f[k] || "not_disclosed"])));
    }}>
      <div className="callout info">
        These answers are only used to check the allocation is fair across groups, and only ever reported as group totals.
        They never reach the model and are never shown again here. Every question is optional.
      </div>
      <div className="row"><span>Status:</span>{answered || save.isSuccess ? <Pill tone="teal">Answered</Pill> : <Pill>Not answered</Pill>}</div>
      <div className="grid cols-3">
        {AUDIT.map(([k, label, choices]) => (
          <Field key={k} label={label}>
            {choices.length ? <Select value={f[k] ?? ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} placeholder="Prefer not to say" options={choices} />
              : <Input value={f[k] ?? ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} placeholder="Prefer not to say" />}
          </Field>
        ))}
      </div>
      {save.error && <ErrorBox error={save.error} />}
      {save.isSuccess && <div className="callout info">Saved. {answered ? "The earlier answers were replaced." : ""}</div>}
      <div><Button kind="primary" type="submit" disabled={save.isPending}>{answered ? "Replace answers" : "Save answers"}</Button></div>
    </form>
  );
}
