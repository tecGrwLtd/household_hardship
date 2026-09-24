import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useApi } from "../api/hooks";
import type { ApplicationPage } from "../api/types";
import { Button, Card, Check, Empty, ErrorBox, Input, Loading, PageHeader, StatusPill } from "../components/ui";
import { day, GROUP_COLOR, NEED_GROUP, NEED_LABEL, num, shortId, STATUS } from "../lib/format";
import { useUser } from "../state/auth";
import { useFilterChips, usePageFilters, type FilterKey } from "../state/filters";

const KEYS: FilterKey[] = ["month", "support_group", "region", "area_code", "urban_rural"];
const PAGE = 25;
const TAB_ORDER = ["deferred", "appealed", "in_review", "submitted", "auto_approved", "audit_approved", "awarded", "withdrawn", "closed"];

export default function Applications() {
  const user = useUser();
  const params = usePageFilters(KEYS);
  const chips = useFilterChips(KEYS);
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [mine, setMine] = useState(user.role === "caseworker");
  const [page, setPage] = useState(0);

  // Debounce the search box.
  useEffect(() => { const t = setTimeout(() => setSearch(q), 300); return () => clearTimeout(t); }, [q]);
  useEffect(() => setPage(0), [params, status, search, mine]);

  const res = useApi<ApplicationPage>("/applications", { ...params, status, q: search, mine, limit: PAGE, offset: page * PAGE }, { keepPrevious: true });
  const data = res.data;
  const all = data ? Object.values(data.status_counts).reduce((a, b) => a + b, 0) : 0;
  const tabs = data ? TAB_ORDER.filter((s) => data.status_counts[s]) : [];
  const last = data ? Math.max(0, Math.ceil(data.total / PAGE) - 1) : 0;

  function exportCsv() {
    if (!data) return;
    const rows = [["id", "household", "district", "need", "requested_rwf", "status", "caseworker", "submitted"],
      ...data.items.map((a) => [a.application_id, a.household_id, a.area_name, a.need_category, Math.round(a.amount_requested), a.status, a.caseworker ?? "", a.submitted_at.slice(0, 10)])];
    const url = URL.createObjectURL(new Blob([rows.map((r) => r.join(",")).join("\n")], { type: "text/csv" }));
    Object.assign(document.createElement("a"), { href: url, download: "applications.csv" }).click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <PageHeader title="Applications" subtitle="Every application in the filtered period · open one for its score, reasons, history and award"
        chips={chips} actions={<><Button onClick={exportCsv} disabled={!data}>Export this page</Button><Link className="btn primary" to="/applications/new">+ New application</Link></>} />
      <Card>
        <div className="row between wrap">
          <div className="tabs" role="group" aria-label="Status">
            <button type="button" className="tab" aria-pressed={status === ""} onClick={() => setStatus("")}>All <span className="count">{num(all)}</span></button>
            {tabs.map((s) => (
              <button key={s} type="button" className="tab" aria-pressed={status === s} onClick={() => setStatus(s)}>
                {STATUS[s]?.label ?? s} <span className="count">{num(data!.status_counts[s])}</span>
              </button>
            ))}
          </div>
          <div className="row">
            {user.role === "caseworker" && <Check label="Only mine" checked={mine} onChange={(e) => setMine(e.target.checked)} />}
            <Input type="search" placeholder="ID, household or district" aria-label="Search applications" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 260 }} />
          </div>
        </div>
        {res.error ? <ErrorBox error={res.error} /> : !data ? <Loading lines={6} /> : data.items.length === 0 ? <Empty>No applications match.</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>ID</th><th>Household</th><th>District</th><th>Need</th><th className="num">Requested (RWF)</th><th>Status</th><th>Caseworker</th><th>Submitted</th></tr></thead>
              <tbody>
                {data.items.map((a) => (
                  <tr key={a.application_id} className="clickable" onClick={() => navigate(`/applications/${a.application_id}`)}>
                    <td><Link to={`/applications/${a.application_id}`} onClick={(e) => e.stopPropagation()} style={{ fontWeight: 600, textDecoration: "none" }}>#{a.application_id}</Link></td>
                    <td className="mono muted">{shortId(a.household_id)}</td>
                    <td>{a.area_name} <span className="small muted">{a.region}</span></td>
                    <td><span className="row" style={{ gap: 8 }}><i className="dot" style={{ background: GROUP_COLOR[NEED_GROUP[a.need_category]] }} />{NEED_LABEL[a.need_category]}</span></td>
                    <td className="num">{num(a.amount_requested)}</td>
                    <td><StatusPill status={a.status} /></td>
                    <td className="muted">{a.caseworker ?? "—"}</td>
                    <td className="muted">{day(a.submitted_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.total > 0 && (
          <div className="row between small muted">
            <span>{num(page * PAGE + 1)}–{num(Math.min((page + 1) * PAGE, data.total))} of {num(data.total)}</span>
            <div className="row">
              <Button small disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</Button>
              <Button small disabled={page >= last} onClick={() => setPage((p) => p + 1)}>Next</Button>
            </div>
          </div>
        )}
      </Card>
    </>
  );
}
