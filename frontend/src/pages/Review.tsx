import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi, useApiMutation } from "../api/hooks";
import type { ApplicationDetail, CycleBudget, Queue } from "../api/types";
import { CaseHeader, HistoryCard, lean, ScoreCards } from "../components/CaseView";
import { Button, Card, Empty, ErrorBox, Loading, PageHeader, TextArea } from "../components/ui";
import { day, NEED_LABEL, num, rwf } from "../lib/format";
import { useUser } from "../state/auth";
import { usePageFilters, type FilterKey } from "../state/filters";

// The month filter is deliberately not used: a worklist must not hide an older appeal.
const KEYS: FilterKey[] = ["support_group", "region", "area_code", "urban_rural"];
type Tab = "mine" | "all" | "appeal" | "review";

export default function Review() {
  const user = useUser();
  const params = usePageFilters(KEYS);
  const [tab, setTab] = useState<Tab>(user.role === "caseworker" ? "mine" : "all");
  const [search, setSearch] = useSearchParams();
  const selected = search.get("id") ? Number(search.get("id")) : null;

  const queue = useApi<Queue>("/reviews/queue", {
    ...params, mine: tab === "mine", kind: tab === "appeal" || tab === "review" ? tab : undefined, limit: 200,
  }, { keepPrevious: true });
  const items = queue.data?.items ?? [];
  const current = selected ?? items[0]?.application_id ?? null;

  const counts = queue.data?.counts;
  const tabs: [Tab, string, number | undefined][] = [
    ...(user.caseworker_id ? [["mine", "Assigned to me", counts?.mine] as [Tab, string, number | undefined]] : []),
    ["all", "All", counts?.all], ["appeal", "Appeals", counts?.appeal], ["review", "New reviews", counts?.review],
  ];

  const select = (id: number) => setSearch({ id: String(id) });

  return (
    <>
      <PageHeader title="Review queue" subtitle="Everything the model could not decide safely, plus appeals · only a person closes a case"
        actions={<div className="tabs" role="group" aria-label="Queue">
          {tabs.map(([t, label, n]) => (
            <button key={t} type="button" className="tab" aria-pressed={tab === t} onClick={() => { setTab(t); setSearch({}); }}>
              {label} {n !== undefined && <span className="count">{num(n)}</span>}
            </button>
          ))}
        </div>} />
      <div className="split">
        <div className="stack" style={{ gap: 8, maxHeight: "calc(100vh - 160px)", overflowY: "auto", paddingRight: 4 }}>
          {queue.error ? <ErrorBox error={queue.error} /> : !queue.data ? <Loading lines={6} /> : items.length === 0 ? <Empty>Nothing waiting here.</Empty> :
            items.map((q) => (
              <button key={q.application_id} type="button" className={`item ${q.application_id === current ? "selected" : ""}`} onClick={() => select(q.application_id)}
                aria-current={q.application_id === current}>
                <span className="title"><span>#{q.application_id} · {NEED_LABEL[q.need_category]}</span><span>{num(q.amount_requested)}</span></span>
                <span className="meta">
                  {q.area_name} · {q.status === "appealed" ? "appeal" : "review"} · {day(q.submitted_at)} · leans {q.model_lean}
                  {q.deferred_last_year >= 2 ? ` · deferred ${q.deferred_last_year}× this year` : ""}
                </span>
              </button>
            ))}
          {queue.data && items.length === 200 && <span className="small muted">Showing the first 200.</span>}
        </div>
        <div className="stack" style={{ gap: 14, minWidth: 0 }}>
          {current ? <CasePanel key={current} id={current} onDone={() => {
            const i = items.findIndex((x) => x.application_id === current);
            const next = items[i + 1] ?? items[i - 1];
            setSearch(next ? { id: String(next.application_id) } : {});
          }} /> : queue.data && <Empty>Select a case.</Empty>}
        </div>
      </div>
    </>
  );
}

function CasePanel({ id, onDone }: { id: number; onDone: () => void }) {
  const app = useApi<ApplicationDetail>(`/applications/${id}`);
  if (app.error) return <ErrorBox error={app.error} />;
  if (!app.data) return <Loading lines={8} />;
  const a = app.data;
  return (
    <>
      <CaseHeader a={a} />
      <ScoreCards a={a} />
      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1.25fr)" }}>
        <HistoryCard a={a} />
        {a.status === "in_review" || a.status === "appealed"
          ? <DecisionForm a={a} onDone={onDone} />
          : <Card title="Decided"><p style={{ margin: 0 }}>This application is no longer waiting for review. <Link to={`/applications/${a.application_id}`}>Open it</Link>.</p></Card>}
      </div>
    </>
  );
}

function DecisionForm({ a, onDone }: { a: ApplicationDetail; onDone: () => void }) {
  const user = useUser();
  const [decision, setDecision] = useState<"approve" | "deny" | "">("");
  const [notes, setNotes] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const budget = useApi<CycleBudget>(`/cycles/${a.cycle_id}/budget`).data;
  const decide = useApiMutation<{ _id: number; decision: string; notes: string }, { status: string }>(
    "POST", (b) => `/reviews/${b._id}`, ["/reviews", "/applications", "/dashboard", "/me", "/cycles"]);
  const modelLean = lean(a);
  const override = decision !== "" && modelLean !== null && decision !== modelLean;
  const overBudget = decision === "approve" && budget !== undefined && a.amount_requested > budget.remaining;
  const isAppeal = a.status === "appealed";

  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => { setDone(null); onDone(); }, 1200);
    return () => clearTimeout(t);
  }, [done, onDone]);

  if (done) return <Card title="Decision recorded"><div className="callout info">{done}</div></Card>;

  return (
    <Card title="Your decision">
      <div className="row" role="radiogroup" aria-label="Decision">
        {(["approve", "deny"] as const).map((d) => (
          <label key={d} className={`choice ${decision === d ? "selected" : ""}`} style={{ flex: 1 }}>
            <span className="row" style={{ gap: 8 }}>
              <input type="radio" name="decision" checked={decision === d} onChange={() => setDecision(d)} />
              {d === "approve" ? `Approve · award ${rwf(a.amount_requested)}` : isAppeal ? "Deny · closes the case" : "Deny · the applicant can appeal"}
            </span>
          </label>
        ))}
      </div>
      <label className="field">
        Reason (required, kept with the decision)
        <TextArea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What made you decide" />
      </label>
      {override && <div className="callout">This goes against the model's lean ({modelLean}) and is recorded as an override. That is fine — it is what review is for.</div>}
      {overBudget && <div className="callout error">Only {rwf(budget!.remaining)} is left in this cycle; approving would overspend it.</div>}
      {decide.error && <div className="callout error" role="alert">{decide.error.message}</div>}
      <div className="row between wrap">
        <span className="small muted">
          {budget ? `${rwf(Math.max(budget.remaining, 0))} left in this cycle` : "…"}
          {user.role === "admin" && a.caseworker ? ` · recorded for ${a.caseworker}` : ` · recorded as ${user.display_name}`}
        </span>
        <Button kind="primary" disabled={!decision || !notes.trim() || overBudget || decide.isPending}
          onClick={() => decide.mutate({ _id: a.application_id, decision, notes: notes.trim() }, {
            onSuccess: (r) => setDone(`Saved. The application is now ${r.status.replace("_", " ")}. Moving to the next case…`),
          })}>
          {decide.isPending ? "Saving…" : "Confirm decision"}
        </Button>
      </div>
    </Card>
  );
}
