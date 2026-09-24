// Everything about one application, as a reviewer needs it: who, what, the
// model's view of it and why, and the household's history.

import type { ApplicationDetail } from "../api/types";
import { day, GROUP_LABEL, NEED_LABEL, need, num, pct, rwf, shortId } from "../lib/format";
import { DriverBars, RangeChart } from "./charts";
import { Card, Pill, StatusPill } from "./ui";

const SHOCKS: [string, string][] = [
  ["shock_bereavement_12m", "Death in the family"], ["shock_serious_illness_12m", "Serious illness"], ["shock_job_loss_12m", "Job loss"],
  ["shock_eviction_12m", "Eviction"], ["shock_displacement_12m", "Displacement"], ["shock_disaster_12m", "Disaster"],
  ["shock_crop_failure_12m", "Crop failure"],
];

export function deferredLastYear(a: ApplicationDetail): number {
  const t = Date.parse(a.submitted_at);
  return a.history.filter((h) => h.application_id !== a.application_id && h.status === "deferred"
    && t - Date.parse(h.submitted_at) < 365 * 86_400_000 && Date.parse(h.submitted_at) < t).length;
}

export function lean(a: ApplicationDetail): "approve" | "deny" | null {
  const s = a.score;
  if (!s) return null;
  return s.cutoff === null || s.need_mid >= s.cutoff ? "approve" : "deny";
}

export function CaseHeader({ a }: { a: ApplicationDetail }) {
  const s = a.survey;
  const shocks = s ? SHOCKS.filter(([k]) => s[k] === true).map(([, l]) => l) : [];
  const deferred = deferredLastYear(a);
  const everHelped = a.history.some((h) => h.award_amount !== null && h.application_id !== a.application_id);
  const facts: [string, string][] = [
    ["Household", s ? `${s.household_size} ${s.household_size === 1 ? "person" : "people"}${s.children_under_5 ? ` · ${s.children_under_5} under five` : ""}` : "No survey"],
    ["District", `${a.area_name} · ${a.urban_rural}`],
    ["Income / essential costs", s ? `${num(s.monthly_income)} / ${num(s.essential_costs)} RWF` : "—"],
    ["Food insecurity", s?.food_security_score !== null && s?.food_security_score !== undefined ? `${s.food_security_score} of 8` : "—"],
    ["Shocks this year", shocks.length ? shocks.join(", ") : "None recorded"],
    ["Survey used", s ? day(s.survey_date) : "None before submission"],
  ];
  return (
    <Card>
      <div className="row between" style={{ alignItems: "flex-start", gap: 16 }}>
        <div className="stack" style={{ gap: 4 }}>
          <h2 className="serif" style={{ fontSize: 24, fontWeight: 600 }}>#{a.application_id} · {NEED_LABEL[a.need_category]} · {rwf(a.amount_requested)}</h2>
          <span className="muted">
            {GROUP_LABEL[a.support_group]} · submitted {day(a.submitted_at)}{a.application_channel ? ` ${a.application_channel.replace("_", " ")}` : ""}
            {a.caseworker ? ` · ${a.caseworker}` : ""} · household <span className="mono">{shortId(a.household_id)}</span>
          </span>
        </div>
        <div className="row wrap" style={{ justifyContent: "flex-end" }}>
          <StatusPill status={a.status} />
          {deferred >= 2 && <Pill tone="amber">Deferred {deferred} times in a year</Pill>}
          {!everHelped && a.history.length > 1 && <Pill tone="amber">Never helped</Pill>}
        </div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))" }}>
        {facts.map(([k, v]) => <div key={k} className="stack" style={{ gap: 2 }}><span className="small muted">{k}</span><span style={{ fontWeight: 500 }}>{v}</span></div>)}
      </div>
    </Card>
  );
}

export function ScoreCards({ a }: { a: ApplicationDetail }) {
  const s = a.score;
  const p = a.provisional_score;
  if (!s && !p) return <Card title="Need estimate"><span className="muted">No score yet.</span></Card>;
  const lo = s?.need_lo ?? p!.need_lo, mid = s?.need_mid ?? p!.need_mid, hi = s?.need_hi ?? p!.need_hi;
  const cutoff = s?.cutoff ?? null;
  const drivers = s?.top_shap_features ?? p!.top_drivers.map((d) => [d.feature, d.contribution] as [string, number]);
  let text: string;
  if (!s) text = "Provisional: the band is set when the cycle is allocated, because it depends on who else applied and the budget left.";
  else if (cutoff === null) text = "The budget covered every application in its cycle.";
  else if (lo > cutoff) text = `Even the bottom of the likely range is above the cutoff: the model is confident this household is among the neediest.`;
  else if (hi < cutoff) text = `Even the top of the likely range (${need(lo)} to ${need(hi)}) is below the cutoff, so the model leans deny.${a.status === "appealed" ? " This is an appeal: weigh what the survey cannot see." : ""}`;
  else text = `The likely range (${need(lo)} to ${need(hi)}) crosses the cutoff, so the model cannot place it safely. It leans ${mid >= cutoff ? "approve" : "deny"}. A person decides.`;
  return (
    <div className="grid" style={{ gridTemplateColumns: "minmax(0,1.05fr) minmax(0,1fr)" }}>
      <Card title="Where the case sits" note="need, RWF per person a month">
        <RangeChart lo={lo} mid={mid} hi={hi} cutoff={cutoff} />
        <p style={{ margin: 0 }}>{text}</p>
        <span className="small muted">Scored by {s?.model_version ?? p!.model_version}{s ? ` on ${day(s.scored_at)}` : ""}</span>
      </Card>
      <Card title="Main reasons" note="left lowers need · right raises it">
        {drivers.length ? <DriverBars drivers={drivers} /> : <span className="muted">No explanation for this score.</span>}
      </Card>
    </div>
  );
}

export function HistoryCard({ a }: { a: ApplicationDetail }) {
  return (
    <Card title="Household history" note={`${a.history.length} application${a.history.length === 1 ? "" : "s"}`}>
      <div>
        {a.history.map((h) => (
          <div key={h.application_id} className="list-row" style={{ gridTemplateColumns: "90px minmax(0,1fr) auto", fontWeight: h.application_id === a.application_id ? 600 : 400 }}>
            <span className="muted">{day(h.submitted_at)}</span>
            <span>{NEED_LABEL[h.need_category]} · {rwf(h.amount_requested)}{h.award_amount !== null ? <span className="muted"> · awarded {rwf(h.award_amount)}</span> : null}</span>
            <StatusPill status={h.status} />
          </div>
        ))}
      </div>
      {a.repeat_forecast && (
        <span className="small muted">Planning forecast: {pct(a.repeat_forecast.p_return_1y, 1)} chance this household applies again within a year. Never used to decide.</span>
      )}
    </Card>
  );
}

export function DecisionsCard({ a }: { a: ApplicationDetail }) {
  if (!a.reviews.length && !a.award) return null;
  return (
    <Card title="Decisions">
      {a.award && <div className="callout info">Awarded {rwf(a.award.award_amount)} on {day(a.award.award_date)}.</div>}
      {a.reviews.map((r) => (
        <div key={r.review_id} className="stack" style={{ gap: 2, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
          <span><b>{r.final_decision.replace("_", " ")}</b> by {r.caseworker ?? "a caseworker"} on {day(r.reviewed_at)}{r.overridden ? <Pill tone="amber">Override</Pill> : null}</span>
          {r.notes && <span className="muted">“{r.notes}”</span>}
        </div>
      ))}
    </Card>
  );
}
