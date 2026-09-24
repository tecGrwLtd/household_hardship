import { Link } from "react-router-dom";
import { useApi } from "../api/hooks";
import type { ApplicationPage, MySummary, Queue } from "../api/types";
import { Stacked } from "../components/charts";
import { Card, Empty, Kpi, Legend, LinkButton, Loading, PageHeader, Pill, StatusPill } from "../components/ui";
import { day, GROUP_COLOR, GROUP_LABEL, monthLabel, NEED_LABEL, num, pct, rwf } from "../lib/format";
import { useUser } from "../state/auth";
import { usePageFilters } from "../state/filters";

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function Home() {
  const user = useUser();
  const params = usePageFilters(["month"]);
  const me = useApi<MySummary>("/me/summary", params).data;
  const queue = useApi<Queue>("/reviews/queue", { mine: true, limit: 5 }).data;
  const recent = useApi<ApplicationPage>("/applications", { mine: true, limit: 5, ...params }).data;

  const cycle = me?.cycle;
  const daysLeft = cycle ? Math.ceil((Date.parse(cycle.period_end) + 86_400_000 - Date.now()) / 86_400_000) : null;
  const cycleLine = cycle
    ? daysLeft !== null && daysLeft > 0
      ? `The ${monthLabel(cycle.period_start)} cycle closes in ${daysLeft} day${daysLeft === 1 ? "" : "s"} (${day(cycle.period_end)})`
      : `${monthLabel(cycle.period_start)} cycle · closed ${day(cycle.period_end)}`
    : "";

  return (
    <>
      <PageHeader title={`${greeting()}, ${user.display_name}`} subtitle={cycleLine ? `${cycleLine} · your caseload only` : "Your caseload"}
        actions={<><LinkButton to="/households?new=1">Register household</LinkButton><LinkButton to="/applications/new" kind="primary">+ New application</LinkButton></>} />
      {!me ? <Loading /> : (
        <div className="grid cols-4">
          <Kpi label="Waiting for your decision" value={num(me.open_cases.open)} warn={me.open_cases.open > 0}
            sub={me.open_cases.open ? `${num(me.open_cases.appeals)} appeals · oldest from ${day(me.open_cases.oldest)}` : "Nothing waiting"} />
          <Kpi label="Your applications this cycle" value={num(me.this_cycle.applications)} sub={cycle ? monthLabel(cycle.period_start) : ""} />
          <Kpi label="Helped this cycle" value={num(me.this_cycle.helped)} sub={`${rwf(me.this_cycle.awarded_amount)} awarded`} />
          <Kpi label="Your override rate" value={me.reviews.override_rate !== null ? pct(me.reviews.override_rate, 1, 1) : "—"}
            sub={me.reviews.override_rate !== null ? `over ${num(me.reviews.reviews)} reviews` : `shown after 10 reviews (${num(me.reviews.reviews)} so far)`} />
        </div>
      )}
      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1.2fr) minmax(0,1fr)" }}>
        <Card title="Needs your decision" note="neediest first">
          {!queue ? <Loading /> : queue.items.length === 0 ? <Empty>Nothing waiting for you.</Empty> : (
            <div>
              {queue.items.map((q) => (
                <Link key={q.application_id} to={`/review?id=${q.application_id}`} className="list-row" style={{ gridTemplateColumns: "70px minmax(0,1fr) 120px 90px", textDecoration: "none", color: "var(--ink)" }}>
                  <b>#{q.application_id}</b>
                  <span>{NEED_LABEL[q.need_category]} · <span className="muted">{q.area_name}</span></span>
                  <span style={{ textAlign: "right" }}>{rwf(q.amount_requested)}</span>
                  <span style={{ textAlign: "right" }}>{q.status === "appealed" ? <Pill tone="amber">Appeal</Pill> : <Pill tone="amber">Review</Pill>}</span>
                </Link>
              ))}
            </div>
          )}
          {queue && queue.counts.mine > 0 && <Link to="/review" style={{ fontWeight: 600 }}>All {num(queue.counts.mine)} in your review queue →</Link>}
        </Card>
        <Card title="Your recent applications" note={params.month ? monthLabel(String(params.month)) : undefined}>
          {!recent ? <Loading /> : recent.items.length === 0 ? <Empty>No applications from you in this period.</Empty> : (
            <div>
              {recent.items.map((a) => (
                <Link key={a.application_id} to={`/applications/${a.application_id}`} className="list-row" style={{ gridTemplateColumns: "60px minmax(0,1fr) 120px", textDecoration: "none", color: "var(--ink)" }}>
                  <b>#{a.application_id}</b>
                  <span>{NEED_LABEL[a.need_category]} · <span className="muted">{a.area_name} · {rwf(a.amount_requested)}</span></span>
                  <span style={{ textAlign: "right" }}><StatusPill status={a.status} /></span>
                </Link>
              ))}
            </div>
          )}
          <Link to="/applications" style={{ fontWeight: 600 }}>All your applications →</Link>
        </Card>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1.2fr) minmax(0,1fr)" }}>
        <Card title="Your caseload by support type" note={cycle ? monthLabel(cycle.period_start) : undefined}>
          {me && me.this_cycle.by_support_group.length ? <>
            <Stacked label="Caseload" parts={me.this_cycle.by_support_group.map((g) => ({ value: g.applicants, color: GROUP_COLOR[g.support_group], label: GROUP_LABEL[g.support_group] }))} />
            <Legend items={me.this_cycle.by_support_group.map((g) => [`${GROUP_LABEL[g.support_group]} ${g.applicants}`, GROUP_COLOR[g.support_group]])} />
          </> : <span className="muted">No applications this cycle yet.</span>}
        </Card>
        <Card title="Before an interview">
          <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 6 }}>
            <li>Record a new survey wave if the household's situation has changed.</li>
            <li>Explain that the decision comes when the cycle is allocated, because the budget is fixed.</li>
            <li>The audit-only questions are optional. Say so.</li>
          </ul>
        </Card>
      </div>
    </>
  );
}
