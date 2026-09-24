import type { ReactNode, SelectHTMLAttributes, InputHTMLAttributes, TextareaHTMLAttributes } from "react";
import { Link } from "react-router-dom";
import { STATUS, type Tone } from "../lib/format";

export function PageHeader({ title, subtitle, actions, chips }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; chips?: string[] }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
        {chips && chips.length > 0 && <div className="chips">{chips.map((c) => <Pill key={c}>{c}</Pill>)}</div>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  );
}

export function Card({ title, note, children, className = "", actions }: { title?: ReactNode; note?: ReactNode; children: ReactNode; className?: string; actions?: ReactNode }) {
  return (
    <section className={`card ${className}`}>
      {(title || note || actions) && (
        <div className="card-title">
          {title && <h2>{title}</h2>}
          <div className="row">{note && <span className="note">{note}</span>}{actions}</div>
        </div>
      )}
      {children}
    </section>
  );
}

export function Kpi({ label, value, sub, warn }: { label: string; value: ReactNode; sub?: ReactNode; warn?: boolean }) {
  return (
    <section className="card tight kpi">
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {sub && <span className={`sub ${warn ? "warn" : ""}`}>{sub}</span>}
    </section>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat stack" style={{ gap: 4 }}>
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {sub && <span className="sub">{sub}</span>}
    </div>
  );
}

export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: Tone }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function StatusPill({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: "neutral" as Tone };
  return <Pill tone={s.tone}>{s.label}</Pill>;
}

export function Legend({ items }: { items: [string, string][] }) {
  return (
    <div className="legend">
      {items.map(([label, color]) => <span key={label}><i className="swatch" style={{ background: color }} />{label}</span>)}
    </div>
  );
}

type ButtonProps = { kind?: "primary" | "secondary" | "ghost" | "danger"; small?: boolean; block?: boolean } & React.ButtonHTMLAttributes<HTMLButtonElement>;
export function Button({ kind = "secondary", small, block, className = "", type = "button", ...rest }: ButtonProps) {
  return <button type={type} className={`btn ${kind === "secondary" ? "" : kind} ${small ? "small" : ""} ${block ? "block" : ""} ${className}`} {...rest} />;
}

export function LinkButton({ to, kind = "secondary", children, block, small }: { to: string; kind?: "primary" | "secondary"; children: ReactNode; block?: boolean; small?: boolean }) {
  return <Link to={to} className={`btn ${kind === "primary" ? "primary" : ""} ${block ? "block" : ""} ${small ? "small" : ""}`}>{children}</Link>;
}

export function Field({ label, hint, children, quiet }: { label: ReactNode; hint?: ReactNode; children: ReactNode; quiet?: boolean }) {
  return (
    <label className={`field ${quiet ? "quiet" : ""}`}>
      {label}
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

export function Select({ options, placeholder, ...rest }: { options: [string, string][]; placeholder?: string } & SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className="select" {...rest}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  );
}

export const Input = (props: InputHTMLAttributes<HTMLInputElement>) => <input className="input" {...props} />;
export const TextArea = (props: TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea className="textarea" {...props} />;

export function Check({ label, ...rest }: { label: ReactNode } & InputHTMLAttributes<HTMLInputElement>) {
  return <label className="check"><input type="checkbox" {...rest} />{label}</label>;
}

export function Loading({ lines = 3 }: { lines?: number }) {
  return <div className="stack" aria-busy="true" aria-label="Loading">{Array.from({ length: lines }, (_, i) => <div key={i} className="skeleton" style={{ width: `${90 - i * 12}%` }} />)}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="callout error" role="alert">{msg}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Modal({ title, children, onClose, actions }: { title: string; children: ReactNode; onClose: () => void; actions: ReactNode }) {
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} onKeyDown={(e) => e.key === "Escape" && onClose()}>
        <h2 className="serif" style={{ fontSize: 22 }}>{title}</h2>
        {children}
        <div className="row" style={{ justifyContent: "flex-end" }}>{actions}</div>
      </div>
    </div>
  );
}
