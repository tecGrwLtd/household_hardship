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

// ---------------------------------------------------------------------------
// SVG charts
// ---------------------------------------------------------------------------

/** Tiny trend line for KPI tiles. */
export function Sparkline({ values, color = "var(--teal)", label }: { values: number[]; color?: string; label: string }) {
  if (values.length < 2) return null;
  const w = 120, h = 30, max = Math.max(...values), min = Math.min(...values), span = max - min || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - 3 - ((v - min) / span) * (h - 6)]);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const [lx, ly] = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img" aria-label={`${label} trend`} preserveAspectRatio="none" style={{ overflow: "visible" }}>
      <path d={`${d} L${w},${h} L0,${h} Z`} fill={color} opacity={0.12} />
      <path d={d} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      <circle cx={lx} cy={ly} r={3} fill={color} />
    </svg>
  );
}

export interface Series { name: string; color: string; values: (number | null)[]; dashed?: boolean; area?: boolean }

/** Multi-series line chart with a y axis, gridlines and month labels. */
export function LineChart({ labels, series, height = 220, format = (v: number) => num(v) }: {
  labels: string[]; series: Series[]; height?: number; format?: (v: number) => string;
}) {
  const W = 640, H = height, L = 56, R = 12, T = 12, B = 26;
  const all = series.flatMap((s) => s.values.filter((v): v is number => v !== null));
  const max = Math.max(1, ...all) * 1.08;
  const x = (i: number) => L + (labels.length > 1 ? (i / (labels.length - 1)) * (W - L - R) : (W - L - R) / 2);
  const y = (v: number) => T + (1 - v / max) * (H - T - B);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const every = Math.ceil(labels.length / 8);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" style={{ display: "block" }}
      aria-label={series.map((s) => `${s.name}: ${s.values.map((v) => (v === null ? "none" : format(v))).join(", ")}`).join("; ")}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--line)" />
          <text x={L - 8} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--muted)">{format(t)}</text>
        </g>
      ))}
      {labels.map((l, i) => i % every === 0 || i === labels.length - 1 ? (
        <text key={l + i} x={x(i)} y={H - 6} textAnchor="middle" fontSize={11} fill="var(--muted)">{l}</text>
      ) : null)}
      {series.map((s) => {
        const pts = s.values.map((v, i) => (v === null ? null : [x(i), y(v)] as [number, number])).filter(Boolean) as [number, number][];
        if (!pts.length) return null;
        const d = pts.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
        return (
          <g key={s.name}>
            {s.area && <path d={`${d} L${pts[pts.length - 1][0]},${y(0)} L${pts[0][0]},${y(0)} Z`} fill={s.color} opacity={0.12} />}
            <path d={d} fill="none" stroke={s.color} strokeWidth={2.2} strokeDasharray={s.dashed ? "6 5" : undefined} strokeLinejoin="round" />
            {pts.map(([px, py], i) => (
              <circle key={i} cx={px} cy={py} r={3.2} fill="var(--card)" stroke={s.color} strokeWidth={2}>
                <title>{`${s.name}, ${labels[i]}: ${format(s.values[i] ?? 0)}`}</title>
              </circle>
            ))}
          </g>
        );
      })}
    </svg>
  );
}

/** Donut with the total in the middle. */
export function Donut({ parts, size = 170, center, sub }: { parts: { label: string; value: number; color: string }[]; size?: number; center: string; sub?: string }) {
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  const r = 60, c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <svg viewBox="0 0 160 160" width={size} height={size} role="img" aria-label={parts.map((p) => `${p.label} ${p.value}`).join(", ")}>
      <circle cx={80} cy={80} r={r} fill="none" stroke="var(--chip)" strokeWidth={22} />
      {parts.filter((p) => p.value > 0).map((p) => {
        const len = (p.value / total) * c;
        const el = (
          <circle key={p.label} cx={80} cy={80} r={r} fill="none" stroke={p.color} strokeWidth={22}
            strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offset} transform="rotate(-90 80 80)">
            <title>{`${p.label}: ${num(p.value)} (${((p.value / total) * 100).toFixed(0)}%)`}</title>
          </circle>
        );
        offset += len;
        return el;
      })}
      <text x={80} y={sub ? 78 : 86} textAnchor="middle" fontSize={24} fontWeight={600} fill="var(--ink)" fontFamily="'Source Serif 4', Georgia, serif">{center}</text>
      {sub && <text x={80} y={98} textAnchor="middle" fontSize={11} fill="var(--muted)">{sub}</text>}
    </svg>
  );
}

/** Rows x columns of counts, shaded by size. */
export function Heatmap({ rows, cols, value, colLabel = (c) => c, total }: {
  rows: string[]; cols: string[]; value: (r: string, c: string) => number; colLabel?: (c: string) => ReactNode; total?: (r: string) => ReactNode;
}) {
  const max = Math.max(1, ...rows.flatMap((r) => cols.map((c) => value(r, c))));
  return (
    <div role="table" aria-label="Heatmap" style={{ display: "grid", gridTemplateColumns: `84px repeat(${cols.length}, minmax(0,1fr))${total ? " 52px" : ""}`, gap: 4 }}>
      <span role="columnheader" />
      {cols.map((c) => <span key={c} role="columnheader" className="small muted" style={{ textAlign: "center", fontWeight: 600, fontSize: 11.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{colLabel(c)}</span>)}
      {total && <span role="columnheader" className="small muted" style={{ textAlign: "right", fontWeight: 600 }}>Total</span>}
      {rows.map((r) => (
        <div key={r} role="row" style={{ display: "contents" }}>
          <span role="rowheader" style={{ fontWeight: 500, alignSelf: "center" }}>{r}</span>
          {cols.map((c) => {
            const v = value(r, c), p = Math.round((v / max) * 100);
            return (
              <span key={c} role="cell" title={`${r} · ${colLabel(c)}: ${num(v)}`} style={{
                background: `color-mix(in srgb, var(--teal) ${Math.max(p, 4)}%, var(--card))`, color: p > 55 ? "var(--on-teal)" : "var(--ink)",
                borderRadius: 6, padding: "10px 4px", textAlign: "center", fontWeight: 600, fontSize: 13,
              }}>{num(v)}</span>
            );
          })}
          {total && <span role="cell" style={{ textAlign: "right", alignSelf: "center", fontWeight: 600 }}>{total(r)}</span>}
        </div>
      ))}
    </div>
  );
}
