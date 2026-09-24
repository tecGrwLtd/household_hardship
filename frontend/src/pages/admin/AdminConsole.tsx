// The back office: accounts, caseworkers, platform settings, the activity
// log, system status and the API catalogue. Admin only (the API enforces it).

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import { useApi, useApiMutation } from "../../api/hooks";
import type { AdminCaseworker, AdminUser, AuditEntry, Setting, SystemStatus } from "../../api/types";
import { Button, Card, Empty, ErrorBox, Field, Input, Loading, Modal, PageHeader, Pill, Select, Stat } from "../../components/ui";
import { day, num, pct } from "../../lib/format";
import { useUser } from "../../state/auth";
import { usePageFilters } from "../../state/filters";

const TABS = [["overview", "Overview"], ["users", "Accounts"], ["caseworkers", "Caseworkers"], ["settings", "Settings"], ["log", "Activity log"], ["api", "API"]] as const;
const when = (iso: string | null | undefined) => (iso ? `${day(iso)} ${new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}` : "—");

export default function AdminConsole() {
  usePageFilters([]);
  const [search, setSearch] = useSearchParams();
  const tab = search.get("tab") ?? "overview";
  return (
    <>
      <PageHeader title="Admin console" subtitle="The platform's back office: who can sign in, how it behaves, what has changed, and what the backend offers" />
      <div className="underline-tabs" role="tablist">
        {TABS.map(([k, label]) => <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setSearch({ tab: k })}>{label}</button>)}
      </div>
      {tab === "users" ? <Users /> : tab === "caseworkers" ? <Caseworkers /> : tab === "settings" ? <Settings />
        : tab === "log" ? <ActivityLog /> : tab === "api" ? <ApiCatalogue /> : <Overview go={(t) => setSearch({ tab: t })} />}
    </>
  );
}

// ---------------------------------------------------------------- overview
function Overview({ go }: { go: (tab: string) => void }) {
  const s = useApi<SystemStatus>("/admin/system");
  const log = useApi<{ total: number; items: AuditEntry[] }>("/admin/audit-log", { limit: 6 }).data;
  if (s.error) return <ErrorBox error={s.error} />;
  if (!s.data) return <Loading lines={8} />;
  const d = s.data;
  const tiles: [string, string][] = [["households", "Households"], ["applications", "Applications"], ["awards", "Awards"], ["reviews", "Reviews"],
    ["funding_cycles", "Funding cycles"], ["model_versions", "Model versions"], ["active_users", "Active accounts"], ["audit_entries", "Logged actions"]];
  return (
    <>
      {(d.security.development_secret || d.security.default_admin_password) && (
        <div className="callout error" role="alert">
          Development defaults in use:{d.security.development_secret ? " the token secret (set HARDSHIP_SECRET)" : ""}
          {d.security.development_secret && d.security.default_admin_password ? " and" : ""}
          {d.security.default_admin_password ? " the admin password (set HARDSHIP_ADMIN_PASSWORD, or change it under Accounts)" : ""}. Fine for a demo, not for real data.
        </div>
      )}
      <div className="grid cols-4">
        {tiles.map(([k, label]) => <div key={k} className="card tight"><Stat label={label} value={num(d.counts[k])} /></div>)}
      </div>
      <div className="grid cols-3">
        <Card title="Running">
          <div className="stack" style={{ gap: 8 }}>
            <div className="row between"><span className="muted">API</span><b>v{d.api_version}</b></div>
            <div className="row between"><span className="muted">Database</span><b>{d.database.version.split(" (")[0]} · {d.database.size}</b></div>
            {d.active_models.map((m) => (
              <div key={m.purpose} className="row between"><span className="muted">{m.purpose === "need" ? "Need model" : "Repeat forecast"}</span><span className="mono">{m.model_version}</span></div>
            ))}
            <div className="row between"><span className="muted">Sign-in lasts</span><b>{d.security.token_hours} hours</b></div>
          </div>
        </Card>
        <Card title="Last done">
          {([["cycle.allocate", "Cycle allocated"], ["model.activate", "Model activated"], ["setting.update", "Setting changed"]] as const).map(([k, l]) => (
            <div key={k} className="row between"><span className="muted">{l}</span><b>{when(d.last[k])}</b></div>
          ))}
          <div className="row between"><span className="muted">Drift check</span><b>{d.last_drift_check ? `${when(d.last_drift_check.created_at)} · ${d.last_drift_check.status}` : "never"}</b></div>
        </Card>
        <Card title="Recent activity" actions={<Button small onClick={() => go("log")}>All</Button>}>
          {!log ? <Loading /> : log.items.length === 0 ? <span className="muted">Nothing yet.</span> : log.items.map((e) => (
            <div key={e.log_id} className="stack" style={{ gap: 0, borderTop: "1px solid var(--line)", paddingTop: 6 }}>
              <span className="small"><b>{e.username ?? "system"}</b> · {e.action}{e.target ? ` · ${e.target.slice(0, 18)}` : ""}</span>
              <span className="small muted">{when(e.at)}</span>
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}

// ------------------------------------------------------------------- users
function Users() {
  const me = useUser();
  const users = useApi<AdminUser[]>("/admin/users");
  const cws = useApi<AdminCaseworker[]>("/admin/caseworkers").data;
  const [editing, setEditing] = useState<AdminUser | "new" | null>(null);
  const toggle = useActiveToggle();
  if (users.error) return <ErrorBox error={users.error} />;
  return (
    <Card title="Accounts" note="who can sign in, and as what" actions={<Button kind="primary" small onClick={() => setEditing("new")}>+ New account</Button>}>
      {!users.data ? <Loading lines={5} /> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Name</th><th>Username</th><th>Role</th><th>Caseworker record</th><th>Status</th><th>Created</th><th /></tr></thead>
          <tbody>{users.data.map((u) => (
            <tr key={u.user_id} style={{ opacity: u.active ? 1 : 0.55 }}>
              <td style={{ fontWeight: 600 }}>{u.display_name}{u.user_id === me.user_id && <span className="muted"> (you)</span>}</td>
              <td className="mono">{u.username}</td>
              <td><Pill tone={u.role === "admin" ? "teal" : "neutral"}>{u.role === "admin" ? "Admin" : "Caseworker"}</Pill></td>
              <td>{u.caseworker ?? "—"}</td>
              <td>{u.active ? <Pill tone="teal">Active</Pill> : <Pill>Deactivated</Pill>}</td>
              <td className="muted">{day(u.created_at)}</td>
              <td className="num"><div className="row" style={{ justifyContent: "flex-end" }}>
                <Button small onClick={() => setEditing(u)}>Edit</Button>
                {u.user_id !== me.user_id && <Button small kind="ghost" onClick={() => toggle.run(u)}>{u.active ? "Deactivate" : "Reactivate"}</Button>}
              </div></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {toggle.error ? <ErrorBox error={toggle.error} /> : null}
      <span className="small muted">Passwords are stored as bcrypt hashes and checked inside the database. Nobody, including admins, can read them.</span>
      {editing && <UserModal user={editing === "new" ? null : editing} caseworkers={cws ?? []} onClose={() => setEditing(null)} />}
    </Card>
  );
}

function useActiveToggle() {
  const [error, setError] = useState<unknown>(null);
  const refetch = useApi<AdminUser[]>("/admin/users").refetch;
  return {
    error,
    run: async (u: AdminUser) => {
      setError(null);
      try { await api(`/admin/users/${u.user_id}`, { method: "PATCH", body: { active: !u.active } }); await refetch(); }
      catch (e) { setError(e); }
    },
  };
}

function UserModal({ user, caseworkers, onClose }: { user: AdminUser | null; caseworkers: AdminCaseworker[]; onClose: () => void }) {
  const refetch = useApi<AdminUser[]>("/admin/users").refetch;
  const [f, setF] = useState({ username: user?.username ?? "", display_name: user?.display_name ?? "", role: user?.role ?? "caseworker",
    caseworker_id: user?.caseworker_id ? String(user.caseworker_id) : "", password: "" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f, v: string) => setF((p) => ({ ...p, [k]: v }));
  const valid = f.display_name && (user || (f.username.length >= 3 && f.password.length >= 10)) && (f.role === "admin" || f.caseworker_id) && (!f.password || f.password.length >= 10);

  async function save() {
    setBusy(true); setError(null);
    const cw = f.role === "caseworker" && f.caseworker_id ? Number(f.caseworker_id) : null;
    try {
      if (user) {
        const body: Record<string, unknown> = { display_name: f.display_name, role: f.role, caseworker_id: cw };
        if (f.password) body.password = f.password;
        await api(`/admin/users/${user.user_id}`, { method: "PATCH", body });
      } else {
        await api("/admin/users", { method: "POST", body: { username: f.username.trim(), display_name: f.display_name, role: f.role, caseworker_id: cw, password: f.password } });
      }
      await refetch();
      onClose();
    } catch (e) { setError(e); } finally { setBusy(false); }
  }

  return (
    <Modal title={user ? `Edit ${user.display_name}` : "New account"} onClose={onClose} actions={<>
      <Button onClick={onClose}>Cancel</Button><Button kind="primary" disabled={!valid || busy} onClick={save}>{busy ? "Saving…" : "Save"}</Button>
    </>}>
      <div className="grid cols-2">
        <Field label="Username" hint={user ? "Cannot be changed" : "Lower case, digits, . _ -"}><Input value={f.username} disabled={!!user} onChange={(e) => set("username", e.target.value.toLowerCase())} /></Field>
        <Field label="Display name"><Input value={f.display_name} onChange={(e) => set("display_name", e.target.value)} /></Field>
        <Field label="Role"><Select value={f.role} onChange={(e) => set("role", e.target.value)} options={[["caseworker", "Caseworker"], ["admin", "Admin (programme manager)"]]} /></Field>
        {f.role === "caseworker" && (
          <Field label="Caseworker record" hint="Their applications and decisions are attributed to it">
            <Select value={f.caseworker_id} onChange={(e) => set("caseworker_id", e.target.value)} placeholder="Choose…"
              options={caseworkers.filter((c) => c.active).map((c) => [String(c.caseworker_id), c.display_name])} /></Field>
        )}
      </div>
      <Field label={user ? "New password (leave empty to keep)" : "Password"} hint="At least 10 characters"><Input type="password" autoComplete="new-password" value={f.password} onChange={(e) => set("password", e.target.value)} /></Field>
      {error ? <ErrorBox error={error} /> : null}
    </Modal>
  );
}

// ------------------------------------------------------------- caseworkers
function Caseworkers() {
  const q = useApi<AdminCaseworker[]>("/admin/caseworkers");
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [region, setRegion] = useState("");
  const [error, setError] = useState<unknown>(null);
  async function call(path: string, method: "POST" | "PATCH", body: unknown) {
    setError(null);
    try { await api(path, { method, body }); await q.refetch(); return true; } catch (e) { setError(e); return false; }
  }
  return (
    <Card title="Caseworkers" note="who handles applications, and how their reviews go" actions={<Button kind="primary" small onClick={() => setAdding(true)}>+ New caseworker</Button>}>
      {!q.data ? <Loading lines={6} /> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Name</th><th>Region</th><th className="num">Applications</th><th className="num">Open cases</th><th className="num">Reviews</th><th className="num">Override rate</th><th>Account</th><th /></tr></thead>
          <tbody>{q.data.map((c) => (
            <tr key={c.caseworker_id} style={{ opacity: c.active ? 1 : 0.55 }}>
              <td style={{ fontWeight: 600 }}>{c.display_name}</td><td>{c.region ?? "—"}</td>
              <td className="num">{num(c.applications)}</td><td className="num">{num(c.open_cases)}</td><td className="num">{num(c.reviews)}</td>
              <td className="num">{c.override_rate !== null ? pct(c.override_rate, 1, 1) : "—"}</td>
              <td className="mono small">{c.accounts ?? <span className="muted">none</span>}</td>
              <td className="num"><Button small kind="ghost" onClick={() => call(`/admin/caseworkers/${c.caseworker_id}`, "PATCH", { active: !c.active })}>{c.active ? "Deactivate" : "Reactivate"}</Button></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {error ? <ErrorBox error={error} /> : null}
      <span className="small muted">A caseworker needs an account (Accounts tab) to sign in. Per-caseworker override rates support the spec's review-quality monitoring.</span>
      {adding && (
        <Modal title="New caseworker" onClose={() => setAdding(false)} actions={<>
          <Button onClick={() => setAdding(false)}>Cancel</Button>
          <Button kind="primary" disabled={!name.trim()} onClick={async () => { if (await call("/admin/caseworkers", "POST", { display_name: name.trim(), region: region || null })) { setAdding(false); setName(""); } }}>Add</Button>
        </>}>
          <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Region"><Select value={region} onChange={(e) => setRegion(e.target.value)} placeholder="—" options={["Kigali", "Northern", "Southern", "Eastern", "Western"].map((r) => [r, r])} /></Field>
        </Modal>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- settings
const SETTING_UI: Record<string, { label: string; kind: "percent" | "rwf"; effect: string }> = {
  random_audit_rate: { label: "Random audit rate", kind: "percent", effect: "Used at every allocation. Allowed 3–5%." },
  override_rate_floor: { label: "Override-rate floor", kind: "percent", effect: "Used by monitoring and the dashboard." },
  max_exclusion_error: { label: "Launch threshold · poorest decile deferred", kind: "percent", effect: "Checked when activating a need model." },
  max_subgroup_gap: { label: "Launch threshold · largest group gap", kind: "percent", effect: "Checked when activating a need model." },
  poverty_line: { label: "Poverty line", kind: "rwf", effect: "Used by the next training run." },
};

const SETTING_ORDER = ["random_audit_rate", "override_rate_floor", "max_exclusion_error", "max_subgroup_gap", "poverty_line"];

function Settings() {
  const q = useApi<Setting[]>("/admin/settings");
  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return <Loading lines={6} />;
  return (
    <div className="grid cols-2">
      {[...q.data].sort((a, b) => SETTING_ORDER.indexOf(a.key) - SETTING_ORDER.indexOf(b.key)).map((s) => <SettingCard key={s.key} s={s} onSaved={() => q.refetch()} />)}
    </div>
  );
}

function SettingCard({ s, onSaved }: { s: Setting; onSaved: () => void }) {
  const ui = SETTING_UI[s.key] ?? { label: s.key, kind: "rwf", effect: "" };
  const toInput = (v: number | null) => (v === null ? "" : ui.kind === "percent" ? String(+(v * 100).toFixed(2)) : String(v));
  const [value, setValue] = useState(toInput(s.value));
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => setValue(toInput(s.value)), [s.value]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = value !== toInput(s.value);
  async function save() {
    setError(null); setSaved(false);
    const v = value.trim() === "" ? null : ui.kind === "percent" ? Number(value) / 100 : Number(value);
    try { await api(`/admin/settings/${s.key}`, { method: "PUT", body: { value: v } }); setSaved(true); onSaved(); } catch (e) { setError(e); }
  }
  return (
    <Card title={ui.label} note={ui.effect}>
      <p className="muted" style={{ margin: 0 }}>{s.description}</p>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <Field label={ui.kind === "percent" ? "Value (%)" : "Value (RWF per person a month)"}>
          <Input type="number" step="any" min={0} value={value} placeholder="Not set" onChange={(e) => { setValue(e.target.value); setSaved(false); }} style={{ width: 200 }} />
        </Field>
        <Button kind="primary" disabled={!dirty} onClick={save}>Save</Button>
        {s.value !== null && s.key.startsWith("max_") && <Button onClick={() => setValue("")}>Clear</Button>}
      </div>
      {error ? <ErrorBox error={error} /> : null}
      {saved && <div className="callout info">Saved and logged.</div>}
      <span className="small muted">{s.updated_by ? `Last changed by ${s.updated_by} on ${when(s.updated_at)}` : "Never changed since set-up"}</span>
    </Card>
  );
}

// ------------------------------------------------------------ activity log
const ACTIONS: [string, string][] = [["", "Everything"], ["setting.", "Settings"], ["user.", "Accounts"], ["caseworker.", "Caseworkers"], ["model.", "Models"],
  ["cycle.", "Cycles"], ["review.", "Review decisions"], ["application.", "Applications"], ["household.", "Households"], ["survey.", "Surveys"], ["auth.", "Sign-ins"]];

function ActivityLog() {
  const [action, setAction] = useState("");
  const [page, setPage] = useState(0);
  const q = useApi<{ total: number; items: AuditEntry[] }>("/admin/audit-log", { action, limit: 25, offset: page * 25 }, { keepPrevious: true });
  return (
    <Card title="Activity log" note="who did what, newest first" actions={
      <Select value={action} onChange={(e) => { setAction(e.target.value); setPage(0); }} options={ACTIONS} style={{ width: 200, height: 34 }} />}>
      {q.error ? <ErrorBox error={q.error} /> : !q.data ? <Loading lines={8} /> : q.data.items.length === 0 ? <Empty>Nothing logged yet.</Empty> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Details</th></tr></thead>
          <tbody>{q.data.items.map((e) => (
            <tr key={e.log_id}>
              <td className="muted" style={{ whiteSpace: "nowrap" }}>{when(e.at)}</td>
              <td style={{ fontWeight: 600 }}>{e.username ?? "system"}</td>
              <td><Pill tone={e.action.startsWith("model.") || e.action.startsWith("setting.") ? "amber" : "neutral"}>{e.action}</Pill></td>
              <td className="mono small">{e.target ? (e.target.length > 20 ? `${e.target.slice(0, 8)}…` : e.target) : "—"}</td>
              <td className="small muted">{Object.entries(e.details ?? {}).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ") || "—"}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {q.data && q.data.total > 25 && (
        <div className="row between small muted">
          <span>{num(page * 25 + 1)}–{num(Math.min((page + 1) * 25, q.data.total))} of {num(q.data.total)}</span>
          <div className="row"><Button small disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button><Button small disabled={(page + 1) * 25 >= q.data.total} onClick={() => setPage(page + 1)}>Next</Button></div>
        </div>
      )}
    </Card>
  );
}

// --------------------------------------------------------------------- API
interface OpenApi { info: { title: string; version: string; description?: string }; paths: Record<string, Record<string, { summary?: string; tags?: string[]; description?: string }>> }

function ApiCatalogue() {
  const [spec, setSpec] = useState<OpenApi | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [q, setQ] = useState("");
  useEffect(() => { api<OpenApi>("/openapi.json").then(setSpec).catch(setError); }, []);
  const docsBase = `${window.location.protocol}//${window.location.hostname}:8080`;
  const groups = useMemo(() => {
    const out = new Map<string, { method: string; path: string; summary: string; description: string }[]>();
    for (const [path, ops] of Object.entries(spec?.paths ?? {})) {
      for (const [method, op] of Object.entries(ops)) {
        const text = `${method} ${path} ${op.summary ?? ""}`.toLowerCase();
        if (q && !text.includes(q.toLowerCase())) continue;
        const tag = op.tags?.[0] ?? "other";
        out.set(tag, [...(out.get(tag) ?? []), { method: method.toUpperCase(), path, summary: op.summary ?? "", description: (op.description ?? "").split("\n")[0] }]);
      }
    }
    return [...out.entries()];
  }, [spec, q]);
  const total = groups.reduce((a, [, ops]) => a + ops.length, 0);
  return (
    <>
      <Card title={spec ? `${spec.info.title} · v${spec.info.version}` : "The backend API"} actions={<div className="row">
        <a className="btn small" href={`${docsBase}/docs`} target="_blank" rel="noreferrer">Interactive docs (Swagger) ↗</a>
        <a className="btn small" href={`${docsBase}/redoc`} target="_blank" rel="noreferrer">Reference (ReDoc) ↗</a>
      </div>}>
        <p style={{ margin: 0 }}>Everything the web app does goes through this API, and anything it can do, another system can do too — with the same sign-in and the same role rules.
          The catalogue below is read live from the API's own OpenAPI description, so it is always current.</p>
        <Input type="search" placeholder="Filter endpoints, e.g. allocate, dashboard, models" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter endpoints" />
        <span className="small muted">{num(total)} endpoints in {groups.length} groups</span>
      </Card>
      {error ? <ErrorBox error={error} /> : !spec ? <Loading lines={8} /> : (
        <div className="grid cols-2">
          {groups.map(([tag, ops]) => (
            <Card key={tag} title={tag.replace(/^./, (c) => c.toUpperCase())} note={`${ops.length}`}>
              {ops.map((o) => (
                <div key={o.method + o.path} style={{ display: "grid", gridTemplateColumns: "64px minmax(0,1fr)", gap: 10, borderTop: "1px solid var(--line)", paddingTop: 6 }}>
                  <Pill tone={o.method === "GET" ? "teal" : o.method === "POST" ? "amber" : "blue"}>{o.method}</Pill>
                  <div className="stack" style={{ gap: 0, minWidth: 0 }}>
                    <span className="mono" style={{ overflowWrap: "anywhere" }}>{o.path}</span>
                    <span className="small muted">{o.description || o.summary}</span>
                  </div>
                </div>
              ))}
            </Card>
          ))}
        </div>
      )}
    </>
  );
}

// --------------------------------------------------- change own password
export function PasswordModal({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const change = useApiMutation<{ current_password: string; new_password: string }>("POST", () => "/auth/password");
  const mismatch = again.length > 0 && next !== again;
  return (
    <Modal title="Change your password" onClose={onClose} actions={<>
      <Button onClick={onClose}>{change.isSuccess ? "Close" : "Cancel"}</Button>
      {!change.isSuccess && <Button kind="primary" disabled={!current || next.length < 10 || next !== again || change.isPending}
        onClick={() => change.mutate({ current_password: current, new_password: next })}>Change password</Button>}
    </>}>
      {change.isSuccess ? <div className="callout info">Password changed.</div> : <>
        <Field label="Current password"><Input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></Field>
        <Field label="New password" hint="At least 10 characters"><Input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
        <Field label="New password again" hint={mismatch ? "Does not match" : undefined}><Input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} /></Field>
        {change.error && <ErrorBox error={change.error} />}
      </>}
    </Modal>
  );
}
