import { Link } from "react-router-dom";
import { useApi } from "../api/hooks";
import type { DistrictRow, Drift, FairnessRow, ModelHealth, MonthlyRow, OutcomeRow, Summary, SupportTypeRow } from "../api/types";
import { BarList, Columns, Stacked } from "../components/charts";
import { Button, Card, ErrorBox, Kpi, Legend, Loading, PageHeader, Pill } from "../components/ui";
import { compact, GROUP_COLOR, GROUP_LABEL, monthLabel, monthShort, num, pct, SUPPORT_GROUPS } from "../lib/format";
import { useFilterChips, useFilters, usePageFilters, type FilterKey } from "../state/filters";

const KEYS: FilterKey[] = ["month", "support_group", "region", "area_code", "urban_rural"];

export default function Dashboard() {
  const params = usePageFilters(KEYS);
  const chips = useFilterChips(KEYS);
  const { values } = useFilters();
  const summary = useApi<Summary>("/dashboard/summary", params, { keepPrevious: true });
  const types = useApi<SupportTypeRow[]>("/dashboard/support-types", params, { keepPrevious: true });
  const outcomes = useApi<OutcomeRow[]>("/dashboard/outcomes", params, { keepPrevious: true });
  const monthly = useApi<MonthlyRow[]>("/dashboard/monthly", { ...params, months: 7 }, { keepPrevious: true });
  const districts = useApi<DistrictRow[]>("/dashboard/districts", params, { keepPrevious: true });

  function exportCsv() {
    const rows = [["support_type", "applicants", "helped_within_1y", "helped_over_1y", "first_help", "awarded", "awarded_rwf", "expected_back_1y"],
      ...(types.data ?? []).map((t) => [t.support_group, t.applicants, t.helped_within_1y, t.helped_over_1y, t.first_help, t.awarded, Math.round(t.awarded_amount), t.expected_back_1y.toFixed(1)])];
    const blob = new Blob([rows.map((r) => r.join(",")).join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `hardship-dashboard-${values.month || "all-months"}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <>
      <PageHeader title="Dashboard" subtitle="Who applied, who was helped, and what is coming — for the filters in the sidebar"
        chips={chips} actions={<><Button onClick={exportCsv} disabled={!types.data}>Export CSV</Button><Button onClick={() => window.print()}>Print</Button></>} />
      {summary.error ? <ErrorBox error={summary.error} /> : <Kpis s={summary.data} month={values.month} />}
      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1.75fr) minmax(0,1fr)" }}>
        <SupportTypes rows={types.data} loading={types.isLoading} />
        <Outcomes rows={outcomes.data} />
      </div>
      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1.3fr) minmax(0,1fr) minmax(0,0.95fr)" }}>
        <Monthly rows={monthly.data} month={values.month} />
        <Card title="Where the need is" note="applicants by district">
          {districts.data ? (
            districts.data.length ? <BarList rows={districts.data.slice(0, 6).map((d) => ({ key: d.area_code, label: <b style={{ fontWeight: 500 }}>{d.area_name}</b>, sub: d.region, value: d.applicants }))} />
              : <span className="muted">No applications for these filters.</span>
          ) : <Loading />}
          {districts.data && districts.data.length > 6 && <Link to="/applications" className="small" style={{ fontWeight: 600 }}>All {districts.data.length} districts in Applications →</Link>}
        </Card>
        <PlatformHealth />
      </div>
    </>
  );
}

function Kpis({ s, month }: { s?: Summary; month: string }) {
  if (!s) return <div className="grid cols-5">{Array.from({ length: 5 }, (_, i) => <div key={i} className="card tight"><Loading lines={2} /></div>)}</div>;
  const helped = s.helped_within_1y + s.helped_over_1y;
  const change = s.previous_applicants ? Math.round(((s.applicants - s.previous_applicants) / s.previous_applicants) * 100) : null;
  const prevMonth = month ? new Date(Date.UTC(+month.slice(0, 4), +month.slice(5, 7) - 2, 1)).toISOString() : null;
  const overBudget = s.budget_applies && s.budget !== null && s.awarded_amount > s.budget;
  return (
    <div className="grid cols-5">
      <Kpi label="Applicants" value={num(s.applicants)}
        sub={change !== null && prevMonth ? `${change > 0 ? "+" : change < 0 ? "−" : "±"}${Math.abs(change)}% on ${monthShort(prevMonth)} (${num(s.previous_applicants)})` : "for the filters"} />
      <Kpi label="Requested" value={compact(s.requested)}
        sub={s.budget_applies && s.budget ? `RWF · ${(s.requested / s.budget).toFixed(1)}× the month's budget` : "RWF requested"} />
      <Kpi label="Awarded" value={num(s.awarded)} warn={overBudget}
        sub={`${compact(s.awarded_amount)} RWF` + (s.budget_applies && s.budget ? ` · ${pct(s.awarded_amount, s.budget)} of the ${compact(s.budget)} budget` : "")} />
      <Kpi label="Helped before" value={num(helped)}
        sub={s.applicants ? `${pct(helped, s.applicants)} · ${num(s.helped_within_1y)} within a year, ${num(s.helped_over_1y)} earlier` : "—"} />
      <Kpi label="Back within a year" value={s.forecast_count ? `≈ ${num(s.expected_back_1y)}` : "—"}
        sub={s.forecast_count ? `${pct(s.expected_back_1y, s.forecast_count)} expected · planning forecast` : "No repeat forecast yet"} />
    </div>
  );
}

function SupportTypes({ rows, loading }: { rows?: SupportTypeRow[]; loading: boolean }) {
  const peak = Math.max(1, ...(rows ?? []).map((r) => r.applicants));
  return (
    <Card title="Applicants by support type" note="and who had been helped before">
      <Legend items={[["Helped within a year", "var(--teal)"], ["Helped over a year ago", "var(--teal-mid)"], ["First time", "var(--teal-soft)"]]} />
      {loading || !rows ? <Loading lines={4} /> : rows.length === 0 ? <span className="muted">No applications for these filters.</span> :
        rows.map((r) => {
          const helped = r.helped_within_1y + r.helped_over_1y;
          return (
            <div key={r.support_group} className="group-row">
              <span className="row" style={{ gap: 8, fontWeight: 500 }}><i className="dot" style={{ background: GROUP_COLOR[r.support_group] }} />{GROUP_LABEL[r.support_group]}</span>
              <b style={{ textAlign: "right" }}>{num(r.applicants)}</b>
              <div style={{ width: `${(r.applicants / peak) * 100}%` }}>
                <Stacked height={20} label={GROUP_LABEL[r.support_group]} parts={[
                  { value: r.helped_within_1y, color: "var(--teal)", label: "Helped within a year" },
                  { value: r.helped_over_1y, color: "var(--teal-mid)", label: "Helped over a year ago" },
                  { value: r.first_help, color: "var(--teal-soft)", label: "First time" },
                ]} />
              </div>
              <span className="small muted">{num(helped)} helped before ({pct(helped, r.applicants)}) · {num(r.awarded)} awarded</span>
            </div>
          );
        })}
    </Card>
  );
}

function Outcomes({ rows }: { rows?: OutcomeRow[] }) {
  if (!rows) return <Card title="What happened to them"><Loading /></Card>;
  const n = (s: string) => rows.find((r) => r.status === s)?.applications ?? 0;
  const total = rows.reduce((a, r) => a + r.applications, 0);
  const groups = [
    { label: "Awarded", value: n("auto_approved") + n("audit_approved") + n("awarded"), color: "var(--teal)",
      sub: `${num(n("auto_approved"))} automatically · ${num(n("audit_approved"))} audit sample · ${num(n("awarded"))} by reviewers` },
    { label: "With a person", value: n("in_review") + n("appealed"), color: "var(--amber)", sub: `${num(n("appealed"))} appeals · ${num(n("in_review"))} reviews` },
    { label: "Waiting for allocation", value: n("submitted"), color: "var(--blue)", sub: "" },
    { label: "Deferred", value: n("deferred"), color: "var(--grey)", sub: "can still appeal" },
    { label: "Withdrawn or closed", value: n("withdrawn") + n("closed"), color: "var(--chip)", sub: "" },
  ].filter((g) => g.value > 0);
  return (
    <Card title="What happened to them" note={`${num(total)} applications`}>
      <Stacked label="Outcomes" parts={groups} height={12} />
      {groups.map((g) => (
        <div key={g.label} className="row between" style={{ alignItems: "baseline" }}>
          <div className="stack" style={{ gap: 1 }}>
            <span className="row" style={{ gap: 8, fontWeight: 500 }}><i className="swatch" style={{ background: g.color }} />{g.label}</span>
            {g.sub && <span className="small muted" style={{ paddingLeft: 18 }}>{g.sub}</span>}
          </div>
          <b style={{ fontSize: 16 }}>{num(g.value)}</b>
        </div>
      ))}
      <Link to="/review" style={{ fontWeight: 600 }}>Open the review queue →</Link>
    </Card>
  );
}

function Monthly({ rows, month }: { rows?: MonthlyRow[]; month: string }) {
  if (!rows) return <Card title="Applicants per month"><Loading lines={5} /></Card>;
  const months = [...new Set(rows.map((r) => r.month))].sort();
  const columns = months.map((m) => ({
    key: m, label: monthShort(m), current: m === month,
    parts: SUPPORT_GROUPS.map((g) => ({ label: GROUP_LABEL[g], color: GROUP_COLOR[g], value: rows.find((r) => r.month === m && r.support_group === g)?.applicants ?? 0 })),
  }));
  return (
    <Card title="Applicants per month" note={month ? `to ${monthLabel(month)}` : "latest months"}>
      <Legend items={SUPPORT_GROUPS.map((g) => [GROUP_LABEL[g], GROUP_COLOR[g]])} />
      {columns.length ? <Columns columns={columns} /> : <span className="muted">No applications for these filters.</span>}
    </Card>
  );
}

function PlatformHealth() {
  const health = useApi<ModelHealth>("/dashboard/model-health").data;
  const drift = useApi<Drift>("/dashboard/drift").data;
  const fairness = useApi<{ rows: FairnessRow[] }>("/dashboard/fairness").data;
  const rate = health?.reviews.override_rate;
  const gap = fairness?.rows.length ? Math.max(...fairness.rows.map((r) => Number(r.gap_vs_best))) : null;
  const driftStatus = drift?.latest?.status;
  const items = [
    { title: "Model drift", sub: drift?.latest ? `Checked ${monthLabel(drift.latest.window_end)}` : "No drift check yet",
      pill: driftStatus ? { ok: "Stable", watch: "Watch", alert: "Watch closely" }[driftStatus] : "—", tone: driftStatus === "ok" ? "teal" : driftStatus ? "amber" : "neutral" },
    { title: "Reviewer override rate", sub: health ? `${num(health.reviews.reviews)} reviews · floor is 5%` : "…",
      pill: rate !== null && rate !== undefined ? pct(rate, 1, 1) : "—", tone: health?.reviews.override_rate_ok ? "teal" : "amber" },
    { title: "Fairness gap", sub: "Poorest decile, all audited groups", pill: gap !== null ? gap.toFixed(3) : "—", tone: gap !== null && gap < 0.05 ? "teal" : "amber" },
  ] as const;
  return (
    <Card title="Platform health">
      {items.map((i) => (
        <Link key={i.title} to="/models" className="item" style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
          <span className="stack" style={{ gap: 2 }}><span style={{ fontWeight: 500 }}>{i.title}</span><span className="small muted">{i.sub}</span></span>
          <Pill tone={i.tone}>{i.pill}</Pill>
        </Link>
      ))}
    </Card>
  );
}
