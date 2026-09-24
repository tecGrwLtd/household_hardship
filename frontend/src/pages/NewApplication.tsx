import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi, useApiMutation } from "../api/hooks";
import type { ApplicationDetail, Caseworker, Cycle, HouseholdDetail, HouseholdListItem } from "../api/types";
import { DriverBars } from "../components/charts";
import { Button, Card, Check, Empty, ErrorBox, Field, Input, Loading, PageHeader, Select } from "../components/ui";
import { day, GROUP_LABEL, monthLabel, NEED_GROUP, NEED_LABEL, need, num, rwf, shortId, STATUS } from "../lib/format";
import { useUser } from "../state/auth";
import { usePageFilters } from "../state/filters";

const REFERRALS: [string, string][] = [["self", "Self"], ["ngo_partner", "NGO partner"], ["caseworker_outreach", "Caseworker outreach"],
  ["community_leader", "Community leader"], ["prior_beneficiary", "Former beneficiary"]];
const CHANNELS: [string, string][] = [["in_person", "In person"], ["phone", "Phone"], ["online", "Online"], ["caseworker_submitted", "Submitted by a caseworker"]];
const STEPS = ["Household", "Latest survey", "Application", "Check and submit"];

interface Draft {
  cycle_id: string; received: string; need_category: string; amount_requested: string; stated_need_amount: string;
  days_since_hardship_onset: string; referral_source: string; application_channel: string; documentation_provided: boolean; caseworker_id: string;
}

export default function NewApplication() {
  usePageFilters([]);
  const user = useUser();
  const [search, setSearch] = useSearchParams();
  const householdId = search.get("household");
  const [step, setStep] = useState(householdId ? 1 : 0);
  const today = new Date().toISOString().slice(0, 10);
  const [d, setD] = useState<Draft>({ cycle_id: "", received: today, need_category: "", amount_requested: "", stated_need_amount: "",
    days_since_hardship_onset: "", referral_source: "self", application_channel: "in_person", documentation_provided: false, caseworker_id: "" });
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((p) => ({ ...p, [k]: v }));

  const household = useApi<HouseholdDetail>(householdId ? `/households/${householdId}` : null).data;
  const cycles = useApi<Cycle[]>("/cycles").data;
  const caseworkers = useApi<Caseworker[]>(user.role === "admin" ? "/caseworkers" : null).data;
  const submit = useApiMutation<Record<string, unknown>, ApplicationDetail & { provisional_score: ApplicationDetail["provisional_score"] }>(
    "POST", () => "/applications", ["/applications", "/dashboard", "/me", "/households", "/cycles"]);

  // Default cycle: the one today falls in, else the newest.
  useEffect(() => {
    if (cycles && !d.cycle_id) {
      const open = cycles.find((c) => c.period_start <= today && today <= c.period_end) ?? cycles[0];
      if (open) set("cycle_id", String(open.cycle_id));
    }
  }, [cycles]); // eslint-disable-line react-hooks/exhaustive-deps

  const cycle = cycles?.find((c) => String(c.cycle_id) === d.cycle_id);
  const receivedOk = !cycle || (cycle.period_start <= d.received && d.received <= cycle.period_end);
  const formOk = d.cycle_id && d.need_category && Number(d.amount_requested) > 0 && receivedOk;

  function send() {
    const body: Record<string, unknown> = {
      household_id: householdId, cycle_id: Number(d.cycle_id), need_category: d.need_category,
      amount_requested: Number(d.amount_requested), referral_source: d.referral_source, application_channel: d.application_channel,
      documentation_provided: d.documentation_provided,
      submitted_at: d.received === today ? undefined : `${d.received}T12:00:00Z`,
    };
    if (d.stated_need_amount) body.stated_need_amount = Number(d.stated_need_amount);
    if (d.days_since_hardship_onset) body.days_since_hardship_onset = Number(d.days_since_hardship_onset);
    if (user.role === "admin" && d.caseworker_id) body.caseworker_id = Number(d.caseworker_id);
    submit.mutate(body);
  }

  if (submit.data) return <Submitted app={submit.data} onAnother={() => { submit.reset(); setStep(0); setSearch({}); }} />;

  return (
    <>
      <PageHeader title="New application" subtitle="For a registered household · about 5 minutes with the applicant" />
      <Card className="tight">
        <ol className="row" style={{ listStyle: "none", margin: 0, padding: 0, gap: 16 }}>
          {STEPS.map((label, i) => (
            <li key={label} className="row" style={{ flex: 1, gap: 10 }} aria-current={i === step ? "step" : undefined}>
              <span className="avatar" style={{ width: 28, height: 28, background: i <= step ? "var(--teal)" : "var(--card)", color: i <= step ? "var(--on-teal)" : "var(--muted)", border: i <= step ? 0 : "1px solid var(--field)" }}>
                {i < step ? "✓" : i + 1}
              </span>
              <span style={{ fontWeight: i === step ? 600 : 500, color: i <= step ? "var(--ink)" : "var(--muted)" }}>{label}</span>
            </li>
          ))}
        </ol>
      </Card>
      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1fr) 340px", alignItems: "start" }}>
        <div className="stack" style={{ gap: 14 }}>
          {step === 0 && <PickHousehold onPick={(id) => { setSearch({ household: id }); setStep(1); }} />}
          {step === 1 && householdId && (
            <Card title="Is the latest survey still right?">
              {!household ? <Loading /> : household.surveys.length === 0 ? (
                <div className="stack"><div className="callout">This household has no survey yet, so the application cannot be scored.</div>
                  <div><Link className="btn primary" to={`/households?id=${household.household_id}&tab=survey`}>Record a survey wave</Link></div></div>
              ) : (
                <div className="stack">
                  <p style={{ margin: 0 }}>The application will be scored on the survey of <b>{day(household.surveys[0].survey_date)}</b>. If the household's situation has changed since (income, costs, shocks, who lives there), record a new wave first.</p>
                  <div className="row"><Link className="btn" to={`/households?id=${household.household_id}&tab=survey`}>Record a new wave</Link>
                    <Button kind="primary" onClick={() => setStep(2)}>The survey is still right</Button></div>
                </div>
              )}
              <div><Button kind="ghost" onClick={() => { setSearch({}); setStep(0); }}>← Choose another household</Button></div>
            </Card>
          )}
          {step === 2 && (
            <Card title="What is the application for?">
              <div className="grid cols-3">
                <Field label="Funding cycle"><Select value={d.cycle_id} onChange={(e) => set("cycle_id", e.target.value)}
                  options={(cycles ?? []).map((c) => [String(c.cycle_id), `${monthLabel(c.period_start)} · ${rwf(c.budget_total)}`])} /></Field>
                <Field label="Received on" hint={!receivedOk && cycle ? `Must fall within the cycle (${day(cycle.period_start)} – ${day(cycle.period_end)})` : undefined}>
                  <Input type="date" value={d.received} onChange={(e) => set("received", e.target.value)} aria-invalid={!receivedOk} /></Field>
                {user.role === "admin" ? (
                  <Field label="Caseworker"><Select value={d.caseworker_id} onChange={(e) => set("caseworker_id", e.target.value)} placeholder="None"
                    options={(caseworkers ?? []).map((c) => [String(c.caseworker_id), c.display_name])} /></Field>
                ) : <Field label="Caseworker"><Input value={`${user.display_name} (you)`} readOnly /></Field>}
              </div>
              <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
                <legend style={{ fontSize: 13, fontWeight: 600, paddingBottom: 8 }}>Need</legend>
                <div className="choice-grid">
                  {Object.entries(NEED_LABEL).map(([k, label]) => (
                    <label key={k} className={`choice ${d.need_category === k ? "selected" : ""}`}>
                      <span className="row" style={{ gap: 8 }}><input type="radio" name="need" checked={d.need_category === k} onChange={() => set("need_category", k)} />{label}</span>
                      <span className="sub">{GROUP_LABEL[NEED_GROUP[k]]}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <div className="grid cols-3">
                <Field label="Amount requested (RWF)"><Input type="number" min={1} value={d.amount_requested} onChange={(e) => set("amount_requested", e.target.value)} required /></Field>
                <Field label="Amount that would close the gap" hint="Optional"><Input type="number" min={0} value={d.stated_need_amount} onChange={(e) => set("stated_need_amount", e.target.value)} /></Field>
                <Field label="Days since the hardship began" hint="Late applications often mean isolation, not less need"><Input type="number" min={0} value={d.days_since_hardship_onset} onChange={(e) => set("days_since_hardship_onset", e.target.value)} /></Field>
                <Field label="Referred by"><Select value={d.referral_source} onChange={(e) => set("referral_source", e.target.value)} options={REFERRALS} /></Field>
                <Field label="How they applied"><Select value={d.application_channel} onChange={(e) => set("application_channel", e.target.value)} options={CHANNELS} /></Field>
                <div style={{ alignSelf: "end", paddingBottom: 6 }}><Check label="Supporting documents provided" checked={d.documentation_provided} onChange={(e) => set("documentation_provided", e.target.checked)} /></div>
              </div>
              <div className="row between"><Button onClick={() => setStep(1)}>← Back</Button><Button kind="primary" disabled={!formOk} onClick={() => setStep(3)}>Continue to check</Button></div>
            </Card>
          )}
          {step === 3 && household && (
            <Card title="Check before submitting">
              <div className="grid cols-2">
                {([["Household", `${shortId(household.household_id)} · ${household.area_name}`], ["Cycle", cycle ? monthLabel(cycle.period_start) : "—"],
                  ["Need", `${NEED_LABEL[d.need_category]} (${GROUP_LABEL[NEED_GROUP[d.need_category]]})`], ["Amount requested", rwf(Number(d.amount_requested))],
                  ["Received", day(d.received)], ["Referred by / channel", `${REFERRALS.find((r) => r[0] === d.referral_source)?.[1]} · ${CHANNELS.find((c) => c[0] === d.application_channel)?.[1]}`],
                  ["Documents", d.documentation_provided ? "Provided" : "Not provided"], ["Survey used", day(household.surveys[0]?.survey_date)]] as [string, string][]).map(([k, v]) => (
                  <div key={k} className="row between" style={{ borderTop: "1px solid var(--line)", padding: "8px 0" }}><span className="muted">{k}</span><b style={{ fontWeight: 500 }}>{v}</b></div>
                ))}
              </div>
              {submit.error && <ErrorBox error={submit.error} />}
              <div className="row between"><Button onClick={() => setStep(2)}>← Edit</Button><Button kind="primary" disabled={submit.isPending} onClick={send}>{submit.isPending ? "Submitting…" : "Submit application"}</Button></div>
            </Card>
          )}
        </div>
        <div className="stack" style={{ gap: 14 }}>
          {household && <HouseholdSide h={household} />}
          <Card title="After you submit">
            <p style={{ margin: 0 }}>You get a provisional need estimate and its main reasons. The decision comes when the cycle is allocated, because the budget is fixed: tell the applicant this. Nobody is refused by the model alone.</p>
          </Card>
        </div>
      </div>
    </>
  );
}

function PickHousehold({ onPick }: { onPick: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => { const t = setTimeout(() => setTerm(q), 300); return () => clearTimeout(t); }, [q]);
  const list = useApi<{ total: number; items: HouseholdListItem[] }>("/households", { q: term, limit: 12 }, { keepPrevious: true }).data;
  return (
    <Card title="Which household?" actions={<Link className="btn small" to="/households?new=1">Register a new household</Link>}>
      <Input type="search" autoFocus placeholder="Household ID (e.g. HH-3037) or district" aria-label="Find household" value={q} onChange={(e) => setQ(e.target.value)} />
      {!list ? <Loading /> : list.items.length === 0 ? <Empty>No household matches. Register it first.</Empty> : (
        <div className="grid cols-2">
          {list.items.map((h) => (
            <button key={h.household_id} type="button" className="item" onClick={() => onPick(h.household_id)}>
              <span className="title"><span className="mono">{shortId(h.household_id)}</span><span className="small muted">{h.area_name}</span></span>
              <span className="meta">{h.household_size ? `${h.household_size} people · ` : ""}{h.last_survey ? `surveyed ${day(h.last_survey)}` : "no survey"} · {num(h.applications_count)} applications</span>
            </button>
          ))}
        </div>
      )}
    </Card>
  );
}

function HouseholdSide({ h }: { h: HouseholdDetail }) {
  const s = h.surveys[0];
  const helped = h.applications.filter((a) => ["auto_approved", "audit_approved", "awarded"].includes(a.status)).length;
  const last = h.applications[0];
  const since = last ? Math.round((Date.now() - Date.parse(last.submitted_at)) / 86_400_000) : null;
  const rows = useMemo<[string, string][]>(() => s ? [
    ["Household", `${s.household_size} people${s.children_under_5 ? ` · ${s.children_under_5} under five` : ""}`],
    ["District", `${h.area_name} · ${h.urban_rural}`],
    ["Income / costs", `${num(s.monthly_income)} / ${num(s.essential_costs)} RWF`],
    ["Food insecurity", s.food_security_score !== null ? `${s.food_security_score} of 8` : "—"],
    ["Survey", day(s.survey_date)],
  ] : [], [s, h]);
  return (
    <Card title="This household" note={shortId(h.household_id)}>
      {rows.map(([k, v]) => <div key={k} className="row between small" style={{ borderTop: "1px solid var(--line)", padding: "7px 0" }}><span className="muted">{k}</span><b style={{ fontWeight: 500 }}>{v}</b></div>)}
      {last ? (
        <div className={`callout ${helped ? "info" : ""}`}>
          Applied {h.applications.length} time{h.applications.length === 1 ? "" : "s"} before, last {since} days ago ({STATUS[last.status]?.label.toLowerCase()}). {helped ? `Helped ${helped} time${helped === 1 ? "" : "s"}.` : "Never helped."}
        </div>
      ) : <div className="callout info">First application.</div>}
    </Card>
  );
}

function Submitted({ app, onAnother }: { app: ApplicationDetail; onAnother: () => void }) {
  const p = app.provisional_score;
  return (
    <>
      <PageHeader title={`Application #${app.application_id} submitted`} subtitle="The decision comes when the cycle is allocated" />
      <div className="grid cols-2">
        <Card title="Provisional need estimate" note="RWF per person a month">
          {p ? <>
            <div className="row" style={{ gap: 24 }}>
              <div className="stack" style={{ gap: 0 }}><span className="small muted">Estimate</span><b className="serif" style={{ fontSize: 30 }}>{need(p.need_mid)}</b></div>
              <div className="stack" style={{ gap: 0 }}><span className="small muted">Likely range</span><b style={{ fontSize: 18 }}>{need(p.need_lo)} – {need(p.need_hi)}</b></div>
            </div>
            <p className="muted" style={{ margin: 0 }}>{p.note}</p>
            {!p.has_survey && <div className="callout">No survey before this application — record one so it can be scored properly.</div>}
          </> : <span className="muted">No estimate available.</span>}
        </Card>
        <Card title="Main reasons">{p && p.top_drivers.length ? <DriverBars drivers={p.top_drivers.map((x) => [x.feature, x.contribution])} /> : <span className="muted">—</span>}</Card>
      </div>
      <div className="row"><Link className="btn primary" to={`/applications/${app.application_id}`}>Open the application</Link><Button onClick={onAnother}>Start another</Button></div>
    </>
  );
}
