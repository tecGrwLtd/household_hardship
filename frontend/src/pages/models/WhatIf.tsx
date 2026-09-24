// "Try the model": score an imagined household and see the estimate, its
// range, where it would have landed in the latest cycle, and why.

import { useState, type FormEvent } from "react";
import { useApi, useApiMutation } from "../../api/hooks";
import type { Area, ModelVersion, WhatIfResult } from "../../api/types";
import { DriverBars, RangeChart } from "../../components/charts";
import { Button, Card, Check, ErrorBox, Field, Input, Pill, Select } from "../../components/ui";
import { BAND, monthLabel, need, NEED_LABEL, pct } from "../../lib/format";

const SHOCKS = ["bereavement", "serious_illness", "job_loss", "eviction", "displacement", "disaster", "crop_failure"];
const ASSETS = ["phone", "radio", "tv", "fridge", "washing_machine", "bicycle", "motorcycle", "car"];
const label = (s: string) => (s === "bereavement" ? "Death in the family" : s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()));

type Form = {
  version: string; area_code: string; need_category: string; amount_requested: number; household_size: number; children_under_5: number;
  members_over_65: number; monthly_income: number; essential_costs: number; food_security_score: number; employment_type: string;
  shocks: string[]; assets: string[]; prior_applications_count: number; female_headed: boolean; disability_in_household: boolean; chronic_illness: boolean;
};

const PRESETS: Record<string, Partial<Form>> = {
  "Struggling family": { area_code: "AR017", need_category: "food", amount_requested: 30000, household_size: 7, children_under_5: 2, members_over_65: 1,
    monthly_income: 12000, essential_costs: 95000, food_security_score: 7, employment_type: "informal", shocks: ["job_loss", "serious_illness"], assets: ["phone"], female_headed: true },
  "Recent shock": { area_code: "AR002", need_category: "funeral", amount_requested: 45000, household_size: 4, children_under_5: 1, members_over_65: 0,
    monthly_income: 45000, essential_costs: 80000, food_security_score: 4, employment_type: "self_employed", shocks: ["bereavement"], assets: ["phone", "radio", "bicycle"], female_headed: false },
  "Stable household": { area_code: "AR001", need_category: "utilities", amount_requested: 20000, household_size: 3, children_under_5: 0, members_over_65: 0,
    monthly_income: 220000, essential_costs: 70000, food_security_score: 1, employment_type: "formal", shocks: [], assets: ["phone", "tv", "fridge", "motorcycle"], female_headed: false },
};

export default function WhatIf() {
  const areas = useApi<Area[]>("/areas").data;
  const versions = useApi<ModelVersion[]>("/models").data;
  const [f, setF] = useState<Form>(() => ({ ...(PRESETS["Struggling family"] as Form), version: "", prior_applications_count: 0, disability_in_household: false, chronic_illness: false }));
  const run = useApiMutation<Record<string, unknown>, WhatIfResult>("POST", () => "/models/what-if");
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((p) => ({ ...p, [k]: v }));
  const toggle = (k: "shocks" | "assets", v: string) => set(k, f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v]);

  function submit(e?: FormEvent) {
    e?.preventDefault();
    const { version, ...rest } = f;
    run.mutate({ ...rest, version: version || null });
  }
  const numField = (k: keyof Form, text: string, step = 1) => (
    <Field label={text}><Input type="number" min={0} step={step} value={String(f[k])} onChange={(e) => set(k, Number(e.target.value) as never)} /></Field>
  );

  const r = run.data;
  return (
    <div className="grid" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", alignItems: "start" }}>
      <Card title="An imagined household" actions={<div className="row">{Object.keys(PRESETS).map((p) => (
        <Button key={p} small onClick={() => { setF((x) => ({ ...x, ...PRESETS[p] })); run.reset(); }}>{p}</Button>
      ))}</div>}>
        <form className="stack" style={{ gap: 12 }} onSubmit={submit}>
          <div className="grid cols-3">
            <Field label="District"><Select value={f.area_code} onChange={(e) => set("area_code", e.target.value)} options={(areas ?? []).map((a) => [a.area_code, `${a.area_name} · ${a.urban_rural}`])} /></Field>
            <Field label="Need"><Select value={f.need_category} onChange={(e) => set("need_category", e.target.value)} options={Object.entries(NEED_LABEL)} /></Field>
            {numField("amount_requested", "Amount requested (RWF)", 1000)}
            {numField("household_size", "People")}
            {numField("children_under_5", "Under five")}
            {numField("members_over_65", "Over 65")}
            {numField("monthly_income", "Monthly income (RWF)", 1000)}
            {numField("essential_costs", "Essential costs (RWF)", 1000)}
            <Field label="Main employment"><Select value={f.employment_type} onChange={(e) => set("employment_type", e.target.value)}
              options={["formal", "informal", "self_employed", "unemployed", "unable_to_work"].map((x) => [x, label(x)])} /></Field>
          </div>
          <Field label={`Food insecurity: ${f.food_security_score} of 8`}>
            <input type="range" min={0} max={8} value={f.food_security_score} onChange={(e) => set("food_security_score", Number(e.target.value))} style={{ accentColor: "var(--teal)" }} />
          </Field>
          <fieldset className="group"><legend>Shocks this year</legend>
            <div className="grid cols-3" style={{ gap: 4 }}>{SHOCKS.map((s) => <Check key={s} label={label(s)} checked={f.shocks.includes(s)} onChange={() => toggle("shocks", s)} />)}</div></fieldset>
          <fieldset className="group"><legend>Assets owned</legend>
            <div className="grid cols-4" style={{ gap: 4 }}>{ASSETS.map((s) => <Check key={s} label={s === "tv" ? "TV" : label(s)} checked={f.assets.includes(s)} onChange={() => toggle("assets", s)} />)}</div></fieldset>
          <div className="row wrap" style={{ gap: 18 }}>
            <Check label="Female-headed" checked={f.female_headed} onChange={(e) => set("female_headed", e.target.checked)} />
            <Check label="Disability in household" checked={f.disability_in_household} onChange={(e) => set("disability_in_household", e.target.checked)} />
            <Check label="Chronic illness" checked={f.chronic_illness} onChange={(e) => set("chronic_illness", e.target.checked)} />
          </div>
          <div className="row between wrap">
            <Field label="Model" quiet><Select value={f.version} onChange={(e) => set("version", e.target.value)} placeholder="The active need model"
              options={(versions ?? []).filter((v) => v.purpose === "need" && v.kind !== "rules").map((v) => [v.model_version, v.model_version])} /></Field>
            <Button kind="primary" type="submit" disabled={run.isPending}>{run.isPending ? "Scoring…" : "Score this household"}</Button>
          </div>
        </form>
      </Card>

      <div className="stack" style={{ gap: 14 }}>
        {run.error && <ErrorBox error={run.error} />}
        {!r ? (
          <Card title="The model's view"><p className="muted" style={{ margin: 0 }}>Pick a preset or describe a household, then score it. Nothing is saved; no real household is affected.</p></Card>
        ) : <>
          <Card title="The model's view" note={r.model_version}>
            <div className="row" style={{ gap: 28, flexWrap: "wrap" }}>
              <div className="stack" style={{ gap: 0 }}><span className="small muted">Estimated need</span><b className="serif" style={{ fontSize: 36 }}>{need(r.need_mid)}</b><span className="small muted">RWF per person a month below the poverty line</span></div>
              <div className="stack" style={{ gap: 0 }}><span className="small muted">Likely range</span><b style={{ fontSize: 20 }}>{need(r.need_lo)} – {need(r.need_hi)}</b></div>
              {r.context && <div className="stack" style={{ gap: 4 }}><span className="small muted">In {monthLabel(r.context.period_start)} it would be</span>
                <Pill tone={r.context.likely_band === "auto_approve" ? "teal" : r.context.likely_band === "defer" ? "neutral" : "amber"}>{BAND[r.context.likely_band]?.label}</Pill></div>}
            </div>
            <RangeChart lo={r.need_lo} mid={r.need_mid} hi={r.need_hi} cutoff={r.context?.cutoff ?? null} />
            {r.context && r.context.needier_than_share !== null && (
              <p style={{ margin: 0 }}>Needier than <b>{pct(r.context.needier_than_share, 1)}</b> of the {r.context.applicants} applicants in {monthLabel(r.context.period_start)}.
                {r.context.likely_band === "human_review" ? " Its range crosses that cycle's cutoff, so a person would decide." : ""}</p>
            )}
            <span className="small muted">A band is only ever set by allocating a real cycle: it depends on who else applied and the budget left.</span>
          </Card>
          <Card title="Why" note="left lowers need · right raises it">
            <DriverBars drivers={r.drivers.map((d) => [d.feature, d.contribution])} />
          </Card>
        </>}
      </div>
    </div>
  );
}
