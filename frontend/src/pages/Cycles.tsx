import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useApi, useApiMutation } from "../api/hooks";
import type { AllocationPlan, Cycle, CycleBudget } from "../api/types";
import { Meter, Stacked } from "../components/charts";
import { Button, Card, ErrorBox, Field, Input, Legend, Loading, Modal, PageHeader, Pill } from "../components/ui";
import { BAND, compact, day, monthLabel, monthShort, num, pct, rwf } from "../lib/format";
import { usePageFilters } from "../state/filters";

export default function Cycles() {
  usePageFilters([]);
  const cycles = useApi<Cycle[]>("/cycles");
  const [picked, setPicked] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const list = cycles.data ?? [];
  const open = list.find((c) => c.waiting_applications > 0) ?? list[0];
  const current = list.find((c) => c.cycle_id === picked) ?? open;

  return (
    <>
      <PageHeader title="Funding cycles" subtitle="One fixed budget a month · ranking under budget, not an eligibility test"
        actions={<Button kind="primary" onClick={() => setCreating(true)}>+ New cycle</Button>} />
      {cycles.error ? <ErrorBox error={cycles.error} /> : !cycles.data ? <Loading lines={6} /> : <>
        <div className="grid" style={{ gridTemplateColumns: "minmax(0,1.35fr) minmax(0,1fr)" }}>
          {current ? <AllocationCard key={current.cycle_id} cycle={current} /> : <Card title="No cycles yet"><span className="muted">Create the first one.</span></Card>}
          <DemandCard cycles={list.slice(0, 6).reverse()} />
        </div>
        <Card title="All cycles" note="select one to allocate or preview">
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Cycle</th><th className="num">Budget</th><th className="num">Requested</th><th className="num">Awarded</th><th>Of budget</th><th className="num">Applications</th><th>Status</th></tr></thead>
              <tbody>
                {list.map((c) => {
                  const share = c.budget_total ? c.total_awarded / c.budget_total : 0;
                  return (
                    <tr key={c.cycle_id} className="clickable" onClick={() => setPicked(c.cycle_id)} aria-selected={current?.cycle_id === c.cycle_id}
                      style={current?.cycle_id === c.cycle_id ? { outline: "2px solid var(--teal)", outlineOffset: -2 } : undefined}>
                      <td style={{ fontWeight: 600 }}>{monthLabel(c.period_start)}</td>
                      <td className="num">{compact(c.budget_total)}</td>
                      <td className="num">{compact(c.total_requested)}</td>
                      <td className="num">{compact(c.total_awarded)}</td>
                      <td style={{ minWidth: 160 }}>
                        <div className="row"><div style={{ width: 90 }}><Meter value={Math.min(share, 1)} max={1} color={share > 1.005 ? "var(--amber)" : "var(--teal)"} /></div>
                          <span style={{ color: share > 1.005 ? "var(--amber-text)" : undefined }}>{pct(c.total_awarded, c.budget_total)}</span></div>
                      </td>
                      <td className="num">{num(c.total_applications)}</td>
                      <td>{c.waiting_applications ? <Pill tone="amber">{num(c.waiting_applications)} waiting</Pill> : c.total_applications ? <Pill>Allocated</Pill> : <Pill>Open · empty</Pill>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      </>}
      {creating && <NewCycleModal onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setPicked(id); }} />}
    </>
  );
}

function AllocationCard({ cycle }: { cycle: Cycle }) {
  const qc = useQueryClient();
  const budget = useApi<CycleBudget>(`/cycles/${cycle.cycle_id}/budget`).data;
  const [plan, setPlan] = useState<AllocationPlan | null>(null);
  const [committed, setCommitted] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function run(commit: boolean) {
    setBusy(true); setError(null);
    try {
      const p = await api<AllocationPlan>(`/cycles/${cycle.cycle_id}/${commit ? "allocate" : "preview"}`, { method: commit ? "POST" : "GET" });
      setPlan(p); setCommitted(commit);
      if (commit) {
        for (const prefix of ["/cycles", "/applications", "/reviews", "/dashboard", "/me"]) {
          qc.invalidateQueries({ predicate: (q) => String(q.queryKey[0]).startsWith(prefix) });
        }
      }
    } catch (e) { setError(e); } finally { setBusy(false); setConfirming(false); }
  }

  const counts = plan ? Object.keys(BAND).map((b) => ({ band: b, n: plan.applications.filter((a) => a.band === b).length })) : null;
  const s = plan?.summary;
  return (
    <Card>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack" style={{ gap: 4 }}>
          <span className="side-title">{cycle.waiting_applications ? "Waiting to be allocated" : "Selected cycle"}</span>
          <h2 className="serif" style={{ fontSize: 24, fontWeight: 600 }}>{monthLabel(cycle.period_start)}</h2>
          <span className="muted">{day(cycle.period_start)} – {day(cycle.period_end)} · budget {rwf(cycle.budget_total)} · {budget ? `${rwf(Math.max(budget.remaining, 0))} left` : "…"} · {num(cycle.waiting_applications)} waiting</span>
        </div>
        <div className="row">
          <Button disabled={busy || !cycle.waiting_applications} onClick={() => run(false)}>Preview</Button>
          <Button kind="primary" disabled={busy || !cycle.waiting_applications} onClick={() => setConfirming(true)}>Allocate</Button>
        </div>
      </div>
      {error ? <ErrorBox error={error} /> : null}
      {!plan ? (
        <p className="muted" style={{ margin: 0 }}>
          {cycle.waiting_applications
            ? "Preview shows what allocating now would do and changes nothing. Allocate ranks the waiting applications against what is left of the budget."
            : "Nothing is waiting in this cycle. New applications submitted to it can be allocated later against what is left."}
        </p>
      ) : (
        <>
          <div className={`callout ${committed ? "info" : ""}`}>{committed ? "Allocated. Awards are made, reviews queued, deferrals notified of their appeal route." : "Preview only — nothing has been saved. The random audit sample will differ when you allocate."}</div>
          <div className="grid cols-4">
            {counts!.map(({ band, n }) => (
              <div key={band} className="card tight" style={{ gap: 4 }}>
                <span className="row small muted" style={{ gap: 8 }}><i className="swatch" style={{ background: BAND[band].color }} />{BAND[band].label}</span>
                <b style={{ fontSize: 22 }}>{num(n)}</b>
              </div>
            ))}
          </div>
          {s && <>
            <Stacked label="Budget" total={Math.max(s.budget, s.committed + s.in_review)} height={16} parts={[
              { value: s.committed, color: "var(--teal)", label: "Committed at allocation" },
              { value: s.in_review, color: "var(--amber)", label: "Held for reviewers" },
              { value: Math.max(s.remaining - s.in_review, 0), color: "var(--teal-soft)", label: "Left" },
            ]} />
            <Legend items={[[`Committed ${compact(s.committed)}`, "var(--teal)"], [`Held for reviewers ${compact(s.in_review)}`, "var(--amber)"], [`Left ${compact(Math.max(s.remaining - s.in_review, 0))}`, "var(--teal-soft)"]]} />
          </>}
          <span className="small muted">Scored with {plan.model_version} · cutoff {plan.cutoff !== null ? num(plan.cutoff) : "none (the budget covered everyone)"}</span>
        </>
      )}
      {confirming && (
        <Modal title={`Allocate ${monthLabel(cycle.period_start)}?`} onClose={() => setConfirming(false)} actions={<>
          <Button onClick={() => setConfirming(false)}>Cancel</Button>
          <Button kind="primary" disabled={busy} onClick={() => run(true)}>{busy ? "Allocating…" : `Allocate ${num(cycle.waiting_applications)} applications`}</Button>
        </>}>
          <p style={{ margin: 0 }}>Auto-approved and audit-sample applications are awarded straight away. Close cases go to the review queue; the rest are deferred with an appeal route. This cannot be undone.</p>
        </Modal>
      )}
    </Card>
  );
}

function DemandCard({ cycles }: { cycles: Cycle[] }) {
  const peak = Math.max(1, ...cycles.map((c) => Math.max(c.total_requested, c.budget_total)));
  const ratios = cycles.filter((c) => c.budget_total && c.total_requested).map((c) => c.total_requested / c.budget_total);
  return (
    <Card title="Demand against budget" note="RWF">
      <Legend items={[["Requested", "var(--teal-soft)"], ["Budget", "var(--teal)"]]} />
      <div className="columns" style={{ height: 190 }}>
        {cycles.map((c) => (
          <div key={c.cycle_id} className="col">
            <span className="small muted">{c.budget_total ? `${(c.total_requested / c.budget_total).toFixed(1)}×` : ""}</span>
            <div className="row" style={{ alignItems: "flex-end", gap: 4, height: 140 }}>
              <div title={`Requested ${rwf(c.total_requested)}`} style={{ width: 20, height: (c.total_requested / peak) * 140, background: "var(--teal-soft)", border: "1px solid var(--teal-mid)", borderRadius: "3px 3px 0 0" }} />
              <div title={`Budget ${rwf(c.budget_total)}`} style={{ width: 20, height: (c.budget_total / peak) * 140, background: "var(--teal)", borderRadius: "3px 3px 0 0" }} />
            </div>
            <span className="small">{monthShort(c.period_start)}</span>
          </div>
        ))}
      </div>
      {ratios.length > 0 && <p className="small muted" style={{ margin: 0 }}>Requests ran {Math.min(...ratios).toFixed(1)} to {Math.max(...ratios).toFixed(1)} times the budget. Ranking decides who is helped first.</p>}
    </Card>
  );
}

function NewCycleModal({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [budget, setBudget] = useState("");
  const create = useApiMutation<Record<string, unknown>, Cycle>("POST", () => "/cycles", ["/cycles", "/dashboard"]);
  const ok = start && end && end > start && Number(budget) > 0;
  return (
    <Modal title="New funding cycle" onClose={onClose} actions={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button kind="primary" disabled={!ok || create.isPending}
        onClick={() => create.mutate({ period_start: start, period_end: end, budget_total: Number(budget) }, { onSuccess: (c) => onCreated(c.cycle_id) })}>Create cycle</Button>
    </>}>
      <div className="grid cols-2">
        <Field label="Starts"><Input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
        <Field label="Ends"><Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
      </div>
      <Field label="Budget (RWF)" hint="Fixed for the cycle: applications are ranked against it."><Input type="number" min={1} value={budget} onChange={(e) => setBudget(e.target.value)} /></Field>
      {create.error && <ErrorBox error={create.error} />}
    </Modal>
  );
}
