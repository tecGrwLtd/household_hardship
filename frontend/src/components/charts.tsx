// Small, theme-aware charts drawn with HTML: each is a bar list, a stacked
// bar or a column chart, with the numbers printed next to the marks.

import type { ReactNode } from "react";
import type { Drivers } from "../api/types";
import { featureLabel, need, num } from "../lib/format";

export function Meter({ value, max, color = "var(--teal)", track = "var(--teal-soft)" }: { value: number; max: number; color?: string; track?: string }) {
  const w = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return <div className="bar-track" style={{ background: track }}><div className="bar-fill" style={{ width: `${w}%`, background: color }} /></div>;
}

export function Stacked({ parts, total, height = 14, label }: { parts: { value: number; color: string; label: string }[]; total?: number; height?: number; label: string }) {
  const sum = total ?? parts.reduce((a, p) => a + p.value, 0);
  return (
    <div className="stacked" style={{ height }} role="img" aria-label={`${label}: ${parts.map((p) => `${p.label} ${p.value}`).join(", ")}`}>
      {parts.map((p) => <div key={p.label} title={`${p.label}: ${num(p.value)}`} style={{ width: `${sum ? (p.value / sum) * 100 : 0}%`, background: p.color }} />)}
    </div>
  );
}

export interface Column { key: string; label: string; parts: { value: number; color: string; label: string }[]; current?: boolean }

export function Columns({ columns, height = 150 }: { columns: Column[]; height?: number }) {
  const peak = Math.max(1, ...columns.map((c) => c.parts.reduce((a, p) => a + p.value, 0)));
  return (
    <div className="columns" style={{ height: height + 40 }}>
      {columns.map((c) => {
        const total = c.parts.reduce((a, p) => a + p.value, 0);
        return (
          <div key={c.key} className={`col ${c.current ? "current" : ""}`}>
            <span className="small muted">{num(total)}</span>
            <div className="stack-col" role="img" aria-label={`${c.label}: ${total}`}>
              {c.parts.map((p) => <div key={p.label} title={`${p.label}: ${p.value}`} style={{ height: Math.round((p.value / peak) * height), background: p.color }} />)}
            </div>
            <span className="small" style={{ fontWeight: c.current ? 700 : 400 }}>{c.label}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Where a case's likely range sits against the budget cutoff. */
export function RangeChart({ lo, mid, hi, cutoff }: { lo: number; mid: number; hi: number; cutoff: number | null }) {
  // Zoom to the interval and cutoff, with a margin, so the part a reviewer
  // reads is not squeezed against one edge.
  const points = [lo, mid, hi, cutoff ?? mid];
  const span = Math.max(...points) - Math.min(...points) || Math.abs(mid) || 1;
  const min = Math.min(...points) - span * 0.35;
  const max = Math.max(...points) + span * 0.35;
  const x = (v: number) => `${((v - min) / (max - min)) * 100}%`;
  return (
    <div className="range-chart" role="img"
      aria-label={`Estimated need ${need(mid)}, likely between ${need(lo)} and ${need(hi)}${cutoff !== null ? `; cutoff ${num(cutoff)}` : ""}`}>
      <div style={{ position: "absolute", left: 0, right: 0, top: 36, height: 2, background: "var(--line)" }} />
      <div style={{ position: "absolute", left: x(lo), width: `calc(${x(hi)} - ${x(lo)})`, top: 30, height: 14, borderRadius: 999, background: "var(--teal-soft)", border: "1px solid var(--teal-mid)" }} />
      <div style={{ position: "absolute", left: `calc(${x(mid)} - 7px)`, top: 30, width: 14, height: 14, borderRadius: 999, background: "var(--teal)" }} />
      {cutoff !== null && <>
        <div style={{ position: "absolute", left: x(cutoff), top: 8, width: 2, height: 56, background: "var(--amber)" }} />
        <span className="small" style={{ position: "absolute", left: `calc(${x(cutoff)} + 6px)`, top: 2, color: "var(--amber-text)", fontWeight: 600 }}>Cutoff {num(cutoff)}</span>
      </>}
      <span className="small" style={{ position: "absolute", left: `calc(${x(mid)} - 40px)`, top: 52, fontWeight: 600 }}>Estimate {need(mid)}</span>
    </div>
  );
}

/** Reasons behind a score: left lowers need, right raises it. */
export function DriverBars({ drivers }: { drivers: Drivers }) {
  const peak = Math.max(1, ...drivers.map(([, v]) => Math.abs(v)));
  return (
    <div className="stack" style={{ gap: 8 }}>
      {drivers.map(([f, v]) => (
        <div key={f} className="driver">
          <span style={{ paddingRight: 10 }}>{featureLabel(f)}</span>
          <div className="neg">{v < 0 && <div style={{ width: `${(-v / peak) * 100}%`, background: "var(--neg)", borderRadius: "3px 0 0 3px" }} />}</div>
          <div className="pos">{v > 0 && <div style={{ width: `${(v / peak) * 100}%`, background: "var(--teal)", borderRadius: "0 3px 3px 0" }} />}</div>
          <span className="muted" style={{ textAlign: "right" }}>{v > 0 ? "+" : "−"}{num(Math.abs(v))}</span>
        </div>
      ))}
    </div>
  );
}

export function BarList({ rows }: { rows: { key: string; label: ReactNode; value: number; sub?: ReactNode }[] }) {
  const peak = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="stack" style={{ gap: 10 }}>
      {rows.map((r) => (
        <div key={r.key} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 100px 44px", gap: 10, alignItems: "center" }}>
          <span>{r.label}{r.sub && <span className="small muted"> {r.sub}</span>}</span>
          <Meter value={r.value} max={peak} />
          <span style={{ textAlign: "right", fontWeight: 600 }}>{num(r.value)}</span>
        </div>
      ))}
    </div>
  );
}
