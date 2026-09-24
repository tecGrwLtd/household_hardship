import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { PasswordModal } from "../pages/admin/AdminConsole";
import { useApi } from "../api/hooks";
import type { Queue } from "../api/types";
import { GROUP_LABEL, initials, monthLabel } from "../lib/format";
import { useAuth, useUser } from "../state/auth";
import { FiltersProvider, useFilters, type FilterKey } from "../state/filters";
import { useTheme } from "../state/theme";
import { Pill } from "./ui";

const ADMIN_NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/applications", label: "Applications" },
  { to: "/review", label: "Review queue", badge: true },
  { to: "/households", label: "Households" },
  { to: "/cycles", label: "Funding cycles" },
  { to: "/models", label: "Models & monitoring" },
  { to: "/admin", label: "Admin console" },
];
const CASEWORKER_NAV = [
  { to: "/", label: "My work", end: true },
  { to: "/applications", label: "Applications" },
  { to: "/review", label: "Review queue", badge: true },
  { to: "/households", label: "Households" },
];

export default function Layout() {
  return (
    <FiltersProvider>
      <div className="app">
        <Sidebar />
        <main className="main" id="main"><Outlet /></main>
      </div>
    </FiltersProvider>
  );
}

function Sidebar() {
  const user = useUser();
  const { logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const nav = user.role === "admin" ? ADMIN_NAV : CASEWORKER_NAV;
  const [changingPassword, setChangingPassword] = useState(false);
  const queue = useApi<Queue>("/reviews/queue", { limit: 1 }).data;
  const badge = queue ? (user.role === "admin" ? queue.counts.all : queue.counts.mine) : null;

  return (
    <nav className="sidebar" aria-label="Main">
      <Link to="/" className="brand"><b>Hardship Fund</b><span>Allocation platform</span></Link>
      <div style={{ paddingBottom: 12 }}><Link to="/applications/new" className="btn primary block">+ New application</Link></div>
      <div className="nav stack" style={{ gap: 3 }}>
        {nav.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end}>
            <span>{n.label}</span>
            {n.badge && badge ? <Pill tone="amber">{badge}</Pill> : null}
          </NavLink>
        ))}
      </div>
      <GlobalFilters />
      <div style={{ flex: 1 }} />
      <div className="theme-toggle" role="group" aria-label="Theme">
        <button type="button" aria-pressed={theme === "light"} onClick={() => setTheme("light")}>Light</button>
        <button type="button" aria-pressed={theme === "dark"} onClick={() => setTheme("dark")}>Dark</button>
      </div>
      <div className="user">
        <span className="avatar" aria-hidden="true">{initials(user.display_name)}</span>
        <div className="stack" style={{ gap: 0, flex: 1, minWidth: 0 }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>{user.display_name}</span>
          <span className="small muted">{user.role === "admin" ? "Admin" : "Caseworker"} · <button type="button" className="btn ghost small"
            style={{ height: "auto", padding: 0, fontSize: 12 }} onClick={() => setChangingPassword(true)}>Password</button></span>
        </div>
        <button type="button" className="btn ghost small" onClick={logout}>Sign out</button>
      </div>
      {changingPassword && <PasswordModal onClose={() => setChangingPassword(false)} />}
    </nav>
  );
}

function GlobalFilters() {
  const { values, set, reset, options, active } = useFilters();
  const on = (k: FilterKey) => active.includes(k);
  const districts = (options?.districts ?? []).filter((d) => !values.region || d.region === values.region);
  const unused = active.length === 0;

  const select = (k: FilterKey, label: string, all: string, opts: [string, string][]) => (
    <label className="field quiet" style={{ opacity: on(k) ? 1 : 0.5 }}>
      {label}
      <select className="select" value={values[k]} onChange={(e) => set(k, e.target.value)}
        aria-describedby={on(k) ? undefined : "filters-note"} style={{ height: 36 }}>
        <option value="">{all}</option>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );

  return (
    <section className="side-section" aria-label="Filters for every page">
      <div className="row between">
        <span className="side-title">Filters · every page</span>
        <button type="button" className="btn ghost small" style={{ height: 24, padding: 0 }} onClick={reset}>Reset</button>
      </div>
      {select("month", "Month", "All months", (options?.months ?? []).map((m) => [m, monthLabel(m)]))}
      {select("support_group", "Support type", "All types", (options?.support_groups ?? []).map((g) => [g, GROUP_LABEL[g] ?? g]))}
      {select("region", "Region", "All regions", (options?.regions ?? []).map((r) => [r, r]))}
      {select("area_code", "District", "All districts", districts.map((d) => [d.area_code, d.area_name]))}
      {select("urban_rural", "Area", "Urban and rural", [["urban", "Urban only"], ["rural", "Rural only"]])}
      {(unused || active.length < 5) && (
        <span id="filters-note" className="side-note">
          {unused ? "This page does not use the filters." : "Greyed-out filters do not apply on this page."}
        </span>
      )}
    </section>
  );
}
