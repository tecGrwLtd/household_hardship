// Every trained version as a model card: what it is, what it was trained on,
// how it was judged against the baselines, and what drives it.

import { useState } from "react";
import { useApi } from "../../api/hooks";
import type { ModelCard, ModelVersion } from "../../api/types";
import { DriverBars, LineChart, Meter, Stacked } from "../../components/charts";
import { Card, Empty, Legend, Loading, Pill, Stat } from "../../components/ui";
import { BAND, day, featureLabel, num, pct } from "../../lib/format";

export const KIND_LABEL: Record<string, { name: string; what: string }> = {
  lgbm_quantile: {
    name: "LightGBM need model",
    what: "Three gradient-boosted models on the welfare-weighted poverty gap: 10th and 90th percentile models give the likely range, and a monotone-constrained Huber model gives the estimate that ranks applicants. Validated by area (GroupKFold); the range is widened by cross-conformal calibration to cover 80%.",
  },
  rules: { name: "Rule-based placeholder", what: "A hand-written formula, live until a trained model passes its checks. Every term is visible below; a caseworker can recompute any score by hand." },
  repeat_history: { name: "History model (repeat forecast)", what: "Logistic regression on four numbers — earlier applications, whether it is the first, years since the last, and whether it was funded. Chosen over LightGBM because it forecast better." },
  repeat_lgbm: { name: "LightGBM repeat forecast", what: "A heavily regularised gradient-boosted classifier on survey and application features." },
};

const CANDIDATE_LABEL: Record<string, string> = { lgbm: "LightGBM (this model)", ridge_pmt: "Ridge PMT", deficit_rank: "Deficit only", rules_placeholder: "Placeholder rules" };
const METRICS: { key: string; label: string; better: "low" | "high" | "target"; fmt: (v: number) => string; note: string }[] = [
  { key: "exclusion_error_bottom_decile", label: "Poorest 10% deferred", better: "low", fmt: (v) => pct(v, 1, 1), note: "The headline measure" },
  { key: "inclusion_error", label: "Approvals to non-poor households", better: "low", fmt: (v) => pct(v, 1), note: "Reported, never optimised at the expense of the above" },
  { key: "spearman_poor", label: "Ranking of the poor (rank correlation)", better: "high", fmt: (v) => num(v, 3), note: "How well it orders households below the poverty line" },
  { key: "weighted_pinball_mid", label: "Welfare-weighted error", better: "low", fmt: (v) => num(v), note: "Training objective; errors on the needier cost more" },
  { key: "coverage", label: "Interval coverage", better: "target", fmt: (v) => pct(v, 1, 1), note: "Target 80% (78–82% passes): closest wins" },
];

export default function ModelCards() {
  const versions = useApi<ModelVersion[]>("/models").data;
  const [picked, setPicked] = useState<string | null>(null);
  if (!versions) return <Loading lines={8} />;
  const ordered = [...versions].sort((a, b) => (a.status === "active" ? -1 : 0) - (b.status === "active" ? -1 : 0) || String(b.trained_at).localeCompare(String(a.trained_at)));
  const current = picked ?? ordered[0]?.model_version;
  return (
    <div className="split">
      <div className="stack" style={{ gap: 8 }}>
        {ordered.map((v) => (
          <button key={v.model_version} type="button" className={`item ${v.model_version === current ? "selected" : ""}`} onClick={() => setPicked(v.model_version)}>
            <span className="title"><span className="mono" style={{ fontSize: 12 }}>{v.model_version}</span>
              <Pill tone={v.status === "active" ? "teal" : v.status === "candidate" ? "amber" : "neutral"}>{v.status}</Pill></span>
            <span className="meta">{v.purpose === "need" ? "Need model" : "Repeat forecast"} · {KIND_LABEL[v.kind]?.name ?? v.kind}{v.trained_at ? ` · ${day(v.trained_at)}` : ""}</span>
          </button>
        ))}
      </div>
      {current ? <CardView key={current} version={current} /> : <Empty>No model versions.</Empty>}
    </div>
  );
}

function CardView({ version }: { version: string }) {
  const card = useApi<ModelCard>(`/models/${version}/card`);
  if (!card.data) return <Card><Loading lines={10} /></Card>;
  const c = card.data;
  const v = c.version;
  const meta = c.metadata as Record<string, unknown>;
  const kind = KIND_LABEL[v.kind] ?? { name: v.kind, what: "" };
  const metrics = (v.metrics ?? {}) as Record<string, unknown>;
  const gates = (metrics.gates ?? {}) as Record<string, unknown>;
  return (
    <div className="stack" style={{ gap: 14 }}>
      <Card>
        <div className="row between" style={{ alignItems: "flex-start" }}>
          <div className="stack" style={{ gap: 4 }}>
            <span className="side-title">{v.purpose === "need" ? "Need model · drives allocation" : "Repeat forecast · planning only"}</span>
            <h2 className="serif" style={{ fontSize: 24, fontWeight: 600 }}>{kind.name}</h2>
            <span className="mono muted">{v.model_version}</span>
          </div>
          <div className="row">
            <Pill tone={v.status === "active" ? "teal" : "neutral"}>{v.status}</Pill>
            {v.kind !== "rules" && <Pill tone={gates.passed ? "teal" : "amber"}>{gates.passed ? "Checks passed" : "Checks failed"}</Pill>}
          </div>
        </div>
        <p style={{ margin: 0 }}>{kind.what}</p>
        <div className="grid cols-4">
          <Stat label="Trained" value={v.trained_at ? day(v.trained_at) : "—"} sub={v.activated_at ? `active since ${day(v.activated_at)}` : undefined} />
          <Stat label="Training rows" value={num(v.training_rows)} sub={v.purpose === "need" ? "funded or audited applications" : "a year of follow-up"} />
          {meta.poverty_line ? <Stat label="Poverty line" value={num(meta.poverty_line as number)} sub="RWF per person a month" /> : <Stat label="Features" value={num(c.features?.length ?? 0)} />}
          {meta.conformal !== undefined ? <Stat label="Range widened by" value={num(meta.conformal as number)} sub="conformal calibration" /> : <Stat label="Horizon" value={meta.horizon_days ? `${meta.horizon_days} days` : "—"} />}
        </div>
        {Boolean(meta.data_hash || meta.git_commit) && (
          <span className="small muted">Reproducible: training data {String(meta.data_hash ?? "").slice(0, 12)}… · code {String(meta.git_commit ?? "—")}</span>
        )}
      </Card>

      {v.kind === "rules" && c.terms && (
        <Card title="The formula" note="RWF per person per month, per unit">
          {c.terms.map((t) => (
            <div key={t.feature} className="row between" style={{ borderTop: "1px solid var(--line)", padding: "8px 0" }}>
              <span>{featureLabel(t.feature)}</span><b>{t.weight > 0 ? "+" : "−"}{num(Math.abs(t.weight))}</b>
            </div>
          ))}
        </Card>
      )}

      {v.purpose === "need" && v.kind !== "rules" && <NeedEvaluation metrics={metrics} />}
      {v.purpose === "repeat" && <RepeatEvaluation metrics={metrics} />}

      <div className="grid cols-2">
        {c.importance.length > 0 && (
          <Card title={v.kind === "repeat_history" ? "Coefficients" : "What drives it"} note={v.kind === "repeat_history" ? "log-odds per unit" : "average influence on the score"}>
            {v.kind === "repeat_history"
              ? <DriverBars drivers={c.importance.map((i) => [i.feature, i.value])} />
              : c.importance.map((i) => {
                const peak = c.importance[0].value || 1;
                return (
                  <div key={i.feature} style={{ display: "grid", gridTemplateColumns: "190px minmax(0,1fr) 56px", gap: 10, alignItems: "center" }}>
                    <span className="small">{featureLabel(i.feature)}</span><Meter value={i.value} max={peak} />
                    <span className="small muted" style={{ textAlign: "right" }}>{i.value < 1 ? num(i.value, 3) : num(i.value)}</span>
                  </div>
                );
              })}
          </Card>
        )}
        <SettingsCard meta={meta} gates={gates} />
      </div>
    </div>
  );
}

function NeedEvaluation({ metrics }: { metrics: Record<string, unknown> }) {
  const models = (metrics.models ?? {}) as Record<string, Record<string, number>>;
  const names = Object.keys(models);
  if (!names.length) return <Card title="Evaluation"><span className="muted">No evaluation stored for this version.</span></Card>;
  const fairness = (metrics.fairness_pooled ?? []) as { attribute: string; group: string; n: number; exclusion_error: number }[];
  const bands = models.lgbm?.bands as unknown as Record<string, number> | undefined;
  return (
    <>
      <Card title="Against the baselines" note="out of fold, on areas each model never saw">
        <div className="grid cols-2" style={{ gap: 18 }}>
          {METRICS.filter((m) => names.some((n) => models[n][m.key] !== undefined)).map((m) => {
            const vals = names.filter((n) => models[n][m.key] !== undefined).map((n) => [n, models[n][m.key]] as [string, number]);
            const best = m.better === "low" ? Math.min(...vals.map((x) => x[1]))
              : m.better === "high" ? Math.max(...vals.map((x) => x[1]))
              : vals.reduce((a, x) => (Math.abs(x[1] - 0.8) < Math.abs(a - 0.8) ? x[1] : a), vals[0][1]);
            const peak = Math.max(...vals.map((x) => Math.abs(x[1])), 1e-9);
            return (
              <div key={m.key} className="stack" style={{ gap: 6 }}>
                <div className="row between"><b style={{ fontSize: 13 }}>{m.label}</b><span className="small muted">{m.better === "low" ? "lower is better" : m.better === "high" ? "higher is better" : "closest to 80%"}</span></div>
                {vals.map(([n, val]) => (
                  <div key={n} style={{ display: "grid", gridTemplateColumns: "150px minmax(0,1fr) 64px", gap: 8, alignItems: "center" }}>
                    <span className="small" style={{ fontWeight: n === "lgbm" ? 600 : 400 }}>{CANDIDATE_LABEL[n] ?? n}</span>
                    <Meter value={Math.abs(val)} max={peak} color={val === best ? "var(--teal)" : "var(--grey)"} track="var(--chip)" />
                    <span className="small" style={{ textAlign: "right", fontWeight: val === best ? 700 : 400 }}>{m.fmt(val)}</span>
                  </div>
                ))}
                <span className="small muted">{m.note}</span>
              </div>
            );
          })}
        </div>
        <span className="small muted">Synthetic data: a pipeline check, not a forecast of real-world accuracy.</span>
      </Card>
      <div className="grid cols-2">
        {bands && (
          <Card title="How it would band applicants" note="out-of-fold allocation">
            <Stacked label="Bands" height={16} parts={Object.entries(BAND).map(([k, b]) => ({ label: b.label, value: bands[k] ?? 0, color: b.color }))} />
            <Legend items={Object.entries(BAND).map(([k, b]) => [`${b.label} ${pct(bands[k] ?? 0, 1)}`, b.color])} />
            <span className="small muted">The spec plans for 25–40% auto, 15–30% review, 35–55% defer; the synthetic data defers more because many non-poor households apply.</span>
          </Card>
        )}
        {fairness.length > 0 && (
          <Card title="Fairness" note="poorest decile deferred, by group">
            <div className="table-wrap" style={{ maxHeight: 260, overflowY: "auto" }}>
              <table className="table">
                <thead><tr><th>Group</th><th className="num">People</th><th className="num">Deferred</th></tr></thead>
                <tbody>{fairness.map((f) => (
                  <tr key={f.attribute + f.group}><td>{f.attribute.replace(/_/g, " ")} · {f.group.replace(/_/g, " ")}</td><td className="num">{num(f.n)}</td><td className="num">{pct(f.exclusion_error, 1, 1)}</td></tr>
                ))}</tbody>
              </table>
            </div>
          </Card>
        )}
      </div>
    </>
  );
}

function RepeatEvaluation({ metrics }: { metrics: Record<string, unknown> }) {
  const models = (metrics.models ?? {}) as Record<string, { brier: number; brier_raw: number; pr_auc: number; roc_auc: number; ece: number; calibrated: boolean; calibration: { bin: string; n: number; predicted: number; observed: number }[] }>;
  const best = metrics.best as string | undefined;
  const chosen = best ? models[best] : undefined;
  return (
    <div className="grid cols-2">
      <Card title="Candidates compared" note={`base rate ${pct(Number(metrics.base_rate ?? 0), 1)} · base-rate Brier ${num(Number(metrics.brier_base_rate ?? 0), 3)}`}>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Model</th><th className="num">Brier ↓</th><th className="num">PR-AUC ↑</th><th className="num">ROC-AUC ↑</th><th className="num">Calibration error ↓</th></tr></thead>
          <tbody>{Object.entries(models).map(([n, m]) => (
            <tr key={n} style={{ fontWeight: n === best ? 600 : 400 }}>
              <td>{KIND_LABEL[n]?.name ?? n}{n === best && <> <Pill tone="teal">chosen</Pill></>}</td>
              <td className="num">{num(m.brier, 4)}</td><td className="num">{num(m.pr_auc, 3)}</td><td className="num">{num(m.roc_auc, 3)}</td><td className="num">{num(m.ece, 3)}</td>
            </tr>
          ))}</tbody>
        </table></div>
        <span className="small muted">Lowest Brier score wins. Isotonic calibration is kept only where it helps on held-out data.</span>
      </Card>
      {chosen && chosen.calibration?.length > 1 && (
        <Card title="Calibration" note="predicted vs what happened">
          <Legend items={[["Predicted", "var(--teal)"], ["Observed", "var(--amber)"]]} />
          <LineChart labels={chosen.calibration.map((b) => b.bin)} height={200} format={(v) => pct(v, 1)} series={[
            { name: "Predicted", color: "var(--teal)", values: chosen.calibration.map((b) => b.predicted) },
            { name: "Observed", color: "var(--amber)", values: chosen.calibration.map((b) => b.observed), dashed: true },
          ]} />
          <span className="small muted">When the lines agree, "80% likely" means about 80 in 100 households come back.</span>
        </Card>
      )}
    </div>
  );
}

function SettingsCard({ meta, gates }: { meta: Record<string, unknown>; gates: Record<string, unknown> }) {
  const params = (meta.params ?? {}) as Record<string, unknown>;
  const mono = (meta.monotone_constraints ?? {}) as Record<string, number>;
  const checks = Object.entries(gates).filter(([k, v]) => typeof v === "boolean" && k !== "passed");
  return (
    <Card title="Settings and checks">
      {checks.length > 0 && (
        <div className="stack" style={{ gap: 6 }}>
          {checks.map(([k, v]) => (
            <div key={k} className="row small" style={{ gap: 8 }}><Pill tone={v ? "teal" : "amber"}>{v ? "✓" : "✗"}</Pill>{checkLabel(k)}</div>
          ))}
        </div>
      )}
      {Object.keys(mono).length > 0 && (
        <div className="stack" style={{ gap: 4 }}>
          <span className="side-title">Directions enforced</span>
          <span className="small">{[
            ...Object.entries(mono).filter(([f]) => !/^shock_.*_12m$/.test(f)).map(([f, d]) => `${featureLabel(f)} ${d > 0 ? "↑ raises" : "↓ lowers"} need`),
            ...(Object.keys(mono).some((f) => /^shock_.*_12m$/.test(f)) ? ["Every kind of shock ↑ raises need"] : []),
          ].join(" · ")}</span>
        </div>
      )}
      {Object.keys(params).length > 0 && (
        <div className="stack" style={{ gap: 4 }}>
          <span className="side-title">Parameters</span>
          <span className="small mono">{Object.entries(params).map(([k, v]) => `${k}=${v}`).join("  ")}</span>
        </div>
      )}
      {!checks.length && !Object.keys(params).length && <span className="muted">No stored settings.</span>}
    </Card>
  );
}

const CHECK_LABEL: Record<string, string> = {
  coverage_in_range: "Range covers 78–82% of real outcomes",
  pinball_beats_ridge_pmt: "Range is tighter than ridge PMT",
  expected_directions_hold: "Every constrained feature moves need the expected way",
  spearman_poor_beats_ridge_pmt: "Ranks the poor better than ridge PMT",
  spearman_poor_beats_deficit_rank: "Ranks the poor better than the deficit ranking",
  exclusion_error_not_worse_than_ridge_pmt: "Defers no more of the poorest than ridge PMT",
  exclusion_error_not_worse_than_deficit_rank: "Defers no more of the poorest than the deficit ranking",
};
function checkLabel(k: string) {
  return CHECK_LABEL[k] ?? k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
