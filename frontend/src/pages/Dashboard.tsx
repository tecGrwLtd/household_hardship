import { Link } from "react-router-dom";
import { useApi } from "../api/hooks";
import type {
  AmountRow, ChannelRow, DistrictRow, Drift, FairnessRow, HeatRow, ModelHealth, MonthlyRow, OutcomeRow, SummaryExtra, SupportTypeRow, TrendRow,
} from "../api/types";
import { BarList, Columns, Donut, Heatmap, LineChart, Meter, Sparkline, Stacked } from "../components/charts";
import { Button, Card, ErrorBox, Legend, Loading, PageHeader, Pill } from "../components/ui";
import { compact, GROUP_COLOR, GROUP_LABEL, GROUP_SHORT, monthLabel, monthShort, num, pct, rwf, SUPPORT_GROUPS } from "../lib/format";
import { useFilterChips, useFilters, usePageFilters, type FilterKey } from "../state/filters";

const KEYS: FilterKey[] = ["month", "support_group", "region", "area_code", "urban_rural"];
const CHANNEL_LABEL: Record<string, string> = {
  in_person: "In person", phone: "Phone", online: "Online", caseworker_submitted: "Via a caseworker",
  self: "Self", ngo_partner: "NGO partner", caseworker_outreach: "Caseworker outreach", community_leader: "Community leader",
  prior_beneficiary: "Former beneficiary", "not recorded": "Not recorded",
};

export default function Dashboard() {
  const params = usePageFilters(KEYS);
  const chips = useFilterChips(KEYS);
  const { values } = useFilters();
  const opts = { keepPrevious: true };
  const summary = useApi<SummaryExtra>("/dashboard/summary", params, opts);
  const trend = useApi<TrendRow[]>("/dashboard/trend", { ...params, months: 12 }, opts).data;
  const types = useApi<SupportTypeRow[]>("/dashboard/support-types", params, opts);
  const outcomes = useApi<OutcomeRow[]>("/dashboard/outcomes", params, opts).data;
  const heat = useApi<HeatRow[]>("/dashboard/heatmap", params, opts).data;
  const channels = useApi<Record<string, ChannelRow[]>>("/dashboard/channels", params, opts).data;
  const amounts = useApi<AmountRow[]>("/dashboard/amounts", params, opts).data;
  const districts = useApi<DistrictRow[]>("/dashboard/districts", params, opts).data;
  const monthly = useApi<MonthlyRow[]>("/dashboard/monthly", { ...params, months: 12 }, opts).data;

  function exportCsv() {
    const rows = [["support_type", "applicants", "helped_within_1y", "helped_over_1y", "first_help", "awarded", "awarded_rwf", "expected_back_1y"],
      ...(types.data ?? []).map((t) => [t.support_group, t.applicants, t.helped_within_1y, t.helped_over_1y, t.first_help, t.awarded, Math.round(t.awarded_amount), t.expected_back_1y.toFixed(1)])];
    const url = URL.createObjectURL(new Blob([rows.map((r) => r.join(",")).join("\n")], { type: "text/csv" }));
    Object.assign(document.createElement("a"), { href: url, download: `hardship-dashboard-${values.month || "all-months"}.csv` }).click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <PageHeader title="Dashboard" subtitle="Who applied, who was helped, where the need is and what is coming — for the filters in the sidebar"
        chips={chips} actions={<><Button onClick={exportCsv} disabled={!types.data}>Export CSV</Button><Button onClick={() => window.print()}>Print</Button></>} />
      {summary.error ? <ErrorBox error={summary.error} /> : <Kpis s={summary.data} trend={trend} month={values.month} />}

      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1.6fr) minmax(0,1fr)" }}>
        <MoneyCard trend={trend} month={values.month} />
        <OutcomesCard rows={outcomes} />
      </div>

      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1.15fr) minmax(0,1fr)" }}>
        <SupportTypes rows={types.data} />
        <Card title="Where each kind of need is" note="applicants by region and support type">
          {!heat ? <Loading lines={5} /> : heat.length === 0 ? <span className="muted">No applications for these filters.</span> : (() => {
            const regions = [...new Set(heat.map((h) => h.region))].sort();
            const groups = SUPPORT_GROUPS.filter((g) => heat.some((h) => h.support_group === g));
            const v = (r: string, g: string) => heat.find((h) => h.region === r && h.support_group === g)?.applicants ?? 0;
            return <Heatmap rows={regions} cols={groups} value={v} colLabel={(g) => <span title={GROUP_LABEL[g]}>{GROUP_SHORT[g]}</span>}
              total={(r) => num(groups.reduce((a, g) => a + v(r, g), 0))} />;
          })()}
        </Card>
      </div>

      <div className="grid cols-3">
        <ChannelsCard data={channels} />
        <AmountsCard rows={amounts} />
        <Card title="Where the need is" note="top districts">
          {!districts ? <Loading /> : districts.length === 0 ? <span className="muted">No applications for these filters.</span> : <>
            <BarList rows={districts.slice(0, 7).map((d) => ({ key: d.area_code, label: <b style={{ fontWeight: 500 }}>{d.area_name}</b>, sub: d.region, value: d.applicants }))} />
            {districts.length > 7 && <Link to="/applications" className="small" style={{ fontWeight: 600 }}>All {districts.length} districts in Applications →</Link>}
          </>}
        </Card>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1.3fr) minmax(0,1fr) minmax(0,0.95fr)" }}>
        <MonthlyMix rows={monthly} month={values.month} />
        <ComingBack s={summary.data} types={types.data} />
        <PlatformHealth />
      </div>
    </>
  );
}

function Kpis({ s, trend, month }: { s?: SummaryExtra; trend?: TrendRow[]; month: string }) {
  if (!s) return <div className="grid cols-3" style={{ gridTemplateColumns: "repeat(6, minmax(0,1fr))" }}>{Array.from({ length: 6 }, (_, i) => <div key={i} className="card tight"><Loading lines={2} /></div>)}</div>;
  const t = trend ?? [];
  const helped = s.helped_within_1y + s.helped_over_1y;
  const change = s.previous_applicants ? Math.round(((s.applicants - s.previous_applicants) / s.previous_applicants) * 100) : null;
  const overBudget = s.budget_applies && s.budget !== null && s.awarded_amount > s.budget;
  const tiles: { label: string; value: string; sub: string; warn?: boolean; spark: number[] }[] = [
    { label: "Applicants", value: num(s.applicants), spark: t.map((r) => r.applicants),
      sub: change !== null ? `${change > 0 ? "+" : change < 0 ? "−" : "±"}${Math.abs(change)}% on the month before` : month ? "" : "all months" },
    { label: "Requested", value: compact(s.requested), spark: t.map((r) => r.requested),
      sub: s.budget_applies && s.budget ? `RWF · ${(s.requested / s.budget).toFixed(1)}× the budget` : "RWF requested" },
    { label: "Households helped", value: num(s.awarded), spark: t.map((r) => r.awarded), warn: overBudget,
      sub: `${compact(s.awarded_amount)} RWF` + (s.budget_applies && s.budget ? ` · ${pct(s.awarded_amount, s.budget)} of budget` : "") },
    { label: "Approval rate", value: s.decided ? pct(s.awarded, s.decided) : "—", spark: t.map((r) => (r.decided ? r.awarded / r.decided : 0)),
      sub: `${num(s.awarded)} of ${num(s.decided)} decided` },
    { label: "Average award", value: s.average_award ? compact(s.average_award) : "—", spark: t.map((r) => (r.awarded ? r.awarded_amount / r.awarded : 0)),
      sub: s.median_requested ? `RWF · median request ${compact(s.median_requested)}` : "RWF" },
    { label: "Helped before", value: num(helped), spark: t.map((r) => r.helped_before),
      sub: s.applicants ? `${pct(helped, s.applicants)} · ${num(s.helped_within_1y)} within a year` : "—" },
  ];
  return (
    <div className="grid" style={{ gridTemplateColumns: "repeat(6, minmax(0,1fr))" }}>
      {tiles.map((k) => (
        <section key={k.label} className="card tight kpi" style={{ gap: 6 }}>
          <span className="label">{k.label}</span>
          <span className="value" style={{ fontSize: 30 }}>{k.value}</span>
          <span className={`sub ${k.warn ? "warn" : ""}`} style={{ minHeight: 32 }}>{k.sub}</span>
          <Sparkline values={k.spark} label={k.label} color={k.warn ? "var(--amber)" : "var(--teal)"} />
        </section>
      ))}
    </div>
  );
}

function MoneyCard({ trend, month }: { trend?: TrendRow[]; month: string }) {
  if (!trend) return <Card title="Money over time"><Loading lines={6} /></Card>;
  const labels = trend.map((r) => monthShort(r.month));
  const last = trend[trend.length - 1];
  return (
    <Card title="Money over time" note={`${month ? `to ${monthLabel(month)}` : "last 12 months"} · RWF`}>
      <Legend items={[["Requested", "var(--teal-mid)"], ["Awarded", "var(--teal)"], ["Monthly budget", "var(--amber)"]]} />
      {trend.length < 2 ? <span className="muted">Not enough months for a trend.</span> : (
        <LineChart labels={labels} format={compact} series={[
          { name: "Requested", color: "var(--teal-mid)", values: trend.map((r) => r.requested) },
          { name: "Awarded", color: "var(--teal)", values: trend.map((r) => r.awarded_amount), area: true },
          { name: "Monthly budget", color: "var(--amber)", values: trend.map((r) => r.budget), dashed: true },
        ]} />
      )}
      {last && last.budget ? (
        <span className="small muted">Latest month: {rwf(last.requested)} requested against a {rwf(last.budget)} budget — {(last.requested / last.budget).toFixed(1)}×. The budget is the programme's, whatever the filters.</span>
      ) : null}
    </Card>
  );
}

function OutcomesCard({ rows }: { rows?: OutcomeRow[] }) {
  if (!rows) return <Card title="What happened to them"><Loading /></Card>;
  const n = (s: string) => rows.find((r) => r.status === s)?.applications ?? 0;
  const total = rows.reduce((a, r) => a + r.applications, 0);
  const parts = [
    { label: "Awarded automatically", value: n("auto_approved"), color: "var(--teal)" },
    { label: "Audit sample", value: n("audit_approved"), color: "var(--blue)" },
    { label: "Awarded by reviewers", value: n("awarded"), color: "var(--teal-mid)" },
    { label: "With a person", value: n("in_review") + n("appealed"), color: "var(--amber)" },
    { label: "Waiting for allocation", value: n("submitted"), color: "var(--violet)" },
    { label: "Deferred · can appeal", value: n("deferred"), color: "var(--grey)" },
    { label: "Withdrawn or closed", value: n("withdrawn") + n("closed"), color: "var(--chip)" },
  ].filter((p) => p.value > 0);
  const helped = n("auto_approved") + n("audit_approved") + n("awarded");
  return (
    <Card title="What happened to them" note={`${num(total)} applications`}>
      <div className="row" style={{ gap: 20, alignItems: "center" }}>
        <Donut parts={parts} center={total ? pct(helped, total) : "—"} sub="helped" />
        <div className="stack" style={{ gap: 7, flex: 1 }}>
          {parts.map((p) => (
            <div key={p.label} className="row between small">
              <span className="row" style={{ gap: 8 }}><i className="swatch" style={{ background: p.color }} />{p.label}</span>
              <b>{num(p.value)}</b>
            </div>
          ))}
        </div>
      </div>
      <Link to="/review" style={{ fontWeight: 600 }}>Open the review queue →</Link>
    </Card>
  );
}

function SupportTypes({ rows }: { rows?: SupportTypeRow[] }) {
  const peak = Math.max(1, ...(rows ?? []).map((r) => r.applicants));
  return (
    <Card title="Applicants by support type" note="and who had been helped before">
      <Legend items={[["Helped within a year", "var(--teal)"], ["Helped over a year ago", "var(--teal-mid)"], ["First time", "var(--teal-soft)"]]} />
      {!rows ? <Loading lines={4} /> : rows.length === 0 ? <span className="muted">No applications for these filters.</span> :
        rows.map((r) => {
          const helped = r.helped_within_1y + r.helped_over_1y;
          return (
            <div key={r.support_group} className="group-row">
              <span className="row" style={{ gap: 8, fontWeight: 500, whiteSpace: "nowrap" }}><i className="dot" style={{ background: GROUP_COLOR[r.support_group] }} />{GROUP_LABEL[r.support_group]}</span>
              <b style={{ textAlign: "right" }}>{num(r.applicants)}</b>
              <div style={{ width: `${(r.applicants / peak) * 100}%` }}>
                <Stacked height={20} label={GROUP_LABEL[r.support_group]} parts={[
                  { value: r.helped_within_1y, color: "var(--teal)", label: "Helped within a year" },
                  { value: r.helped_over_1y, color: "var(--teal-mid)", label: "Helped over a year ago" },
                  { value: r.first_help, color: "var(--teal-soft)", label: "First time" },
                ]} />
              </div>
              <span className="small muted">{num(helped)} helped before · {num(r.awarded)} awarded ({pct(r.awarded, r.applicants)})</span>
            </div>
          );
        })}
    </Card>
  );
}

function ChannelsCard({ data }: { data?: Record<string, ChannelRow[]> }) {
  const block = (title: string, rows: ChannelRow[]) => {
    const peak = Math.max(1, ...rows.map((r) => r.applicants));
    return (
      <div className="stack" style={{ gap: 8 }}>
        <span className="side-title">{title}</span>
        {rows.map((r) => (
          <div key={r.value} style={{ display: "grid", gridTemplateColumns: "130px minmax(0,1fr) 44px 52px", gap: 8, alignItems: "center" }}>
            <span className="small">{CHANNEL_LABEL[r.value] ?? r.value}</span>
            <Meter value={r.applicants} max={peak} />
            <b className="small" style={{ textAlign: "right" }}>{num(r.applicants)}</b>
            <span className="small muted" style={{ textAlign: "right" }} title="Share of decided applications that were funded">{r.decided ? pct(r.awarded, r.decided) : "—"}</span>
          </div>
        ))}
      </div>
    );
  };
  return (
    <Card title="How people reach us" note="applicants · % funded">
      {!data ? <Loading lines={6} /> : <>{block("Channel", data.application_channel)}{block("Referred by", data.referral_source)}</>}
      <span className="small muted">Referral routes reflect access to advocacy — watch who they leave out.</span>
    </Card>
  );
}

function AmountsCard({ rows }: { rows?: AmountRow[] }) {
  if (!rows) return <Card title="What people ask for"><Loading lines={6} /></Card>;
  return (
    <Card title="What people ask for" note="RWF requested">
      <Legend items={[["Applications", "var(--teal-soft)"], ["Funded", "var(--teal)"]]} />
      <Columns height={140} columns={rows.map((r) => ({
        key: r.band, label: r.band,
        parts: [{ label: "Funded", value: r.awarded, color: "var(--teal)" }, { label: "Not funded", value: r.applicants - r.awarded, color: "var(--teal-soft)" }],
      }))} />
      <span className="small muted">
        Larger requests are funded more often here ({rows.length ? pct(rows[rows.length - 1].awarded, rows[rows.length - 1].applicants) : "—"} of the largest band) — they tend to come with larger shortfalls.
      </span>
    </Card>
  );
}

function MonthlyMix({ rows, month }: { rows?: MonthlyRow[]; month: string }) {
  if (!rows) return <Card title="Applicants per month"><Loading lines={5} /></Card>;
  const months = [...new Set(rows.map((r) => r.month))].sort();
  const columns = months.map((m) => ({
    key: m, label: monthShort(m), current: m === month,
    parts: SUPPORT_GROUPS.map((g) => ({ label: GROUP_LABEL[g], color: GROUP_COLOR[g], value: rows.find((r) => r.month === m && r.support_group === g)?.applicants ?? 0 })),
  }));
  return (
    <Card title="Applicants per month" note="by support type">
      <Legend items={SUPPORT_GROUPS.map((g) => [GROUP_LABEL[g], GROUP_COLOR[g]])} />
      {columns.length ? <Columns columns={columns} height={140} /> : <span className="muted">No applications for these filters.</span>}
    </Card>
  );
}

function ComingBack({ s, types }: { s?: SummaryExtra; types?: SupportTypeRow[] }) {
  return (
    <Card title="Coming back within a year" note="planning forecast">
      {!s ? <Loading /> : !s.forecast_count ? <span className="muted">No repeat forecast yet — run <span className="mono">python -m backend.ml forecast</span>.</span> : <>
        <div className="row" style={{ gap: 18 }}>
          <b className="serif" style={{ fontSize: 34, whiteSpace: "nowrap" }}>≈ {num(s.expected_back_1y)}</b>
          <span className="muted">of {num(s.forecast_count)} applicants are expected to apply again within a year ({pct(s.expected_back_1y, s.forecast_count)})</span>
        </div>
        {types && types.map((t) => (
          <div key={t.support_group} style={{ display: "grid", gridTemplateColumns: "100px minmax(0,1fr) 60px", gap: 10, alignItems: "center" }}>
            <span className="small">{GROUP_LABEL[t.support_group]}</span>
            <Meter value={t.expected_back_1y} max={t.applicants} color={GROUP_COLOR[t.support_group]} track="var(--chip)" />
            <span className="small" style={{ textAlign: "right" }}>{num(t.expected_back_1y)}</span>
          </div>
        ))}
        <span className="small muted">For budgeting only — never used to decide who is helped.</span>
      </>}
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
    { title: "Reviewer override rate", sub: health ? `${num(health.reviews.reviews)} reviews · floor ${pct((health.reviews as { override_rate_floor?: number }).override_rate_floor ?? 0.05, 1)}` : "…",
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
