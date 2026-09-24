import { useState } from "react";
import { useApi, useApiMutation } from "../api/hooks";
import type { Drift, FairnessRow, ModelHealth, ModelVersion, ModelVersionDetail, OverrideTrendRow } from "../api/types";
import { Meter } from "../components/charts";
import { Button, Card, Check, ErrorBox, Field, Loading, Modal, PageHeader, Pill, Stat, TextArea } from "../components/ui";
import { day, featureLabel, monthLabel, monthShort, NEED_LABEL, num, pct } from "../lib/format";
import { usePageFilters } from "../state/filters";

const groupLabel = (g: string) => NEED_LABEL[g] ?? g.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

const GATE_LABEL: Record<string, string> = {
  coverage_in_range: "Interval coverage within 78–82%",
  exclusion_error_not_worse_than_ridge_pmt: "Excludes no more of the poorest than ridge PMT",
  exclusion_error_not_worse_than_deficit_rank: "Excludes no more of the poorest than deficit ranking",
  spearman_poor_beats_ridge_pmt: "Ranks the poor better than ridge PMT",
  spearman_poor_beats_deficit_rank: "Ranks the poor better than deficit ranking",
  pinball_beats_ridge_pmt: "Lower welfare-weighted error than ridge PMT",
  expected_directions_hold: "Need never rises with income (and similar)",
  beats_base_rate: "Beats the base rate",
  calibrated: "Probabilities are calibrated",
};
const ATTR_LABEL: Record<string, string> = {
  gender_head: "Gender of head", age_band: "Age band", disability: "Disability", ethnicity: "Ethnicity", urban_rural: "Urban / rural",
  region: "Region", need_category: "Need", application_channel: "Channel", referral_source: "Referral",
};

export default function Models() {
  usePageFilters([]);
  const health = useApi<ModelHealth>("/dashboard/model-health");
  const versions = useApi<ModelVersion[]>("/models");
  const drift = useApi<Drift>("/dashboard/drift").data;
  const fairness = useApi<{ rows: FairnessRow[] }>("/dashboard/fairness").data;
  const trend = useApi<OverrideTrendRow[]>("/dashboard/override-trend").data;
  const need = versions.data?.find((v) => v.purpose === "need" && v.status === "active");
  const repeat = versions.data?.find((v) => v.purpose === "repeat" && v.status === "active");
  const needDetail = useApi<ModelVersionDetail>(need ? `/models/${need.model_version}` : null).data;
  const repeatDetail = useApi<ModelVersionDetail>(repeat ? `/models/${repeat.model_version}` : null).data;
  const [activating, setActivating] = useState<ModelVersion | null>(null);

  if (health.error) return <ErrorBox error={health.error} />;
  return (
    <>
      <PageHeader title="Models & monitoring" subtitle="What decides, how well it did before going live, and whether it still fits the people applying" />
      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1.3fr) minmax(0,1fr)" }}>
        <NeedModelCard health={health.data} detail={needDetail} />
        <div className="stack" style={{ gap: 14 }}>
          <DriftCard drift={drift} />
          <FairnessCard rows={fairness?.rows} />
        </div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr) minmax(0,1.6fr)" }}>
        <Card title="Human review" note="override floor 5%">
          {health.data ? <div className="grid cols-2">
            <Stat label="Reviews" value={num(health.data.reviews.reviews)} sub={`${num(health.data.reviews.approved)} approved`} />
            <Stat label="Override rate" value={health.data.reviews.override_rate !== null ? pct(health.data.reviews.override_rate, 1, 1) : "—"}
              sub={health.data.reviews.override_rate_ok === false ? "below the floor: review may be a rubber stamp" : "review is doing real work"} />
          </div> : <Loading />}
          {trend && trend.length > 0 && (
            <div className="columns" style={{ height: 110 }}>
              {trend.slice(-8).map((t) => (
                <div key={t.month} className="col" title={`${monthLabel(t.month)}: ${pct(t.override_rate, 1, 1)} of ${t.reviews}`}>
                  <div style={{ width: 16, height: Math.max(2, t.override_rate * 300), background: t.override_rate_ok ? "var(--teal)" : "var(--amber)", borderRadius: "3px 3px 0 0" }} />
                  <span className="small muted">{monthShort(t.month)}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
        <RepeatCard detail={repeatDetail} />
        <Card title="Versions">
          {!versions.data ? <Loading /> : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Version</th><th>Purpose</th><th>Status</th><th>Checks</th><th /></tr></thead>
              <tbody>
                {versions.data.slice().reverse().map((v) => (
                  <tr key={v.model_version}>
                    <td className="mono" style={{ whiteSpace: "nowrap", fontSize: 12 }}>{v.model_version}</td>
                    <td>{v.purpose === "need" ? "Need" : "Repeat forecast"}</td>
                    <td><Pill tone={v.status === "active" ? "teal" : v.status === "candidate" ? "amber" : "neutral"}>{v.status}</Pill></td>
                    <td className="muted">{v.kind === "rules" ? "Placeholder" : v.gates_passed ? "Passed" : v.gates_passed === false ? "Failed" : "—"}</td>
                    <td className="num">{v.status !== "active" && <Button small onClick={() => setActivating(v)}>Activate</Button>}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
          <span className="small muted">Training runs on the server: <span className="mono">python -m backend.ml train</span>, then activate the new version here.</span>
        </Card>
      </div>
      {activating && <ActivateModal v={activating} onClose={() => setActivating(null)} />}
    </>
  );
}

function NeedModelCard({ health, detail }: { health?: ModelHealth; detail?: ModelVersionDetail }) {
  const a = health?.active_model;
  if (!health) return <Card><Loading lines={8} /></Card>;
  if (!a) return <Card title="Need model"><span className="muted">No active need model.</span></Card>;
  const models = (detail?.metrics as { models?: Record<string, Record<string, number>> } | null)?.models;
  const lgbm = models?.lgbm;
  const gates = Object.entries(a.gates ?? {}).filter(([k, v]) => typeof v === "boolean" && k !== "passed");
  const passed = a.gates?.passed === true;
  return (
    <Card>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack" style={{ gap: 4 }}>
          <span className="side-title">Need model · drives allocation</span>
          <h2 className="serif" style={{ fontSize: 22, fontWeight: 600 }}>{a.model_version}</h2>
          <span className="small muted">{a.kind === "rules" ? "Rule-based placeholder — train and activate a model" : `Trained ${day(a.trained_at)} on ${num(a.training_rows)} funded applications · active since ${day(a.activated_at)}`}</span>
        </div>
        {a.kind !== "rules" && <Pill tone={passed ? "teal" : "amber"}>{passed ? "All checks passed" : "Checks failed"}</Pill>}
      </div>
      {lgbm && (
        <div className="grid cols-3">
          <Stat label="Poorest 10% deferred" value={pct(lgbm.exclusion_error_bottom_decile, 1, 1)} sub="headline measure" />
          <Stat label="Approvals to non-poor" value={pct(lgbm.inclusion_error, 1)} sub={models?.ridge_pmt ? `ridge PMT: ${pct(models.ridge_pmt.inclusion_error, 1)}` : undefined} />
          <Stat label="Ranking of the poor" value={num(lgbm.spearman_poor, 3)} sub={models?.ridge_pmt ? `ridge PMT: ${num(models.ridge_pmt.spearman_poor, 3)}` : undefined} />
        </div>
      )}
      {gates.length > 0 && (
        <div className="stack" style={{ gap: 8 }}>
          {gates.map(([k, v]) => (
            <div key={k} className="row" style={{ gap: 10 }}>
              <span className="avatar" style={{ width: 20, height: 20, fontSize: 12, background: v ? "var(--teal-soft)" : "var(--amber-soft)", color: v ? "var(--teal)" : "var(--amber-text)" }}>{v ? "✓" : "!"}</span>
              {GATE_LABEL[k] ?? k}
            </div>
          ))}
        </div>
      )}
      {a.kind !== "rules" && lgbm && <span className="small muted">Evaluated out of fold on areas each model never saw. Synthetic data: a pipeline check, not a forecast of real accuracy.</span>}
    </Card>
  );
}

function DriftCard({ drift }: { drift?: Drift }) {
  const r = drift?.latest;
  const tone = r?.status === "ok" ? "teal" : r ? "amber" : "neutral";
  return (
    <Card title={r ? `Drift · ${monthLabel(r.window_end)}` : "Drift"} note="PSI" actions={<Pill tone={tone}>{r ? { ok: "Stable", watch: "Watch", alert: "Watch closely" }[r.status] : "Not checked"}</Pill>}>
      {!drift ? <Loading /> : !r ? <span className="muted">{drift.note}</span> : <>
        {[...r.report.features_shifted.slice(0, 4).map((f) => [featureLabel(f.feature), f.psi] as [string, number]), ["Score", r.report.score_psi] as [string, number]].map(([label, v]) => (
          <div key={label} style={{ display: "grid", gridTemplateColumns: "190px 1fr 44px", gap: 10, alignItems: "center" }}>
            <span className="small">{label}</span>
            <Meter value={Math.min(v, 0.4)} max={0.4} color={v > 0.2 ? "var(--amber)" : "var(--teal)"} track="var(--chip)" />
            <b style={{ textAlign: "right" }}>{v.toFixed(2)}</b>
          </div>
        ))}
        <p className="small muted" style={{ margin: 0 }}>
          {num(r.applications)} applications, {day(r.window_start)} – {day(r.window_end)}. {Math.round(r.report.shap_stability.overlap * 10)} of the top 10 reasons unchanged.
          {r.report.interval_coverage ? ` Coverage on new outcomes ${pct(r.report.interval_coverage.coverage, 1)}.` : " No new outcomes to check coverage yet."} Above 0.2 is a shift.
        </p>
      </>}
    </Card>
  );
}

function FairnessCard({ rows }: { rows?: FairnessRow[] }) {
  const byAttr = new Map<string, FairnessRow[]>();
  for (const r of rows ?? []) byAttr.set(r.attribute, [...(byAttr.get(r.attribute) ?? []), r]);
  return (
    <Card title="Fairness" note="poorest decile deferred, by group">
      {!rows ? <Loading /> : rows.length === 0 ? <span className="muted">No audit for the active model.</span> : (
        <div>
          {[...byAttr.entries()].slice(0, 6).map(([attr, rs]) => (
            <div key={attr} className="row between small" style={{ borderTop: "1px solid var(--line)", padding: "7px 0", gap: 12 }}>
              <span style={{ fontWeight: 500 }}>{ATTR_LABEL[attr] ?? attr}</span>
              <span className="muted" style={{ textAlign: "right" }}>{rs.map((r) => `${groupLabel(r.group_value)} ${pct(Number(r.exclusion_error), 1, 1)}`).join(" · ")}</span>
            </div>
          ))}
          <span className="small muted">Groups with fewer than 20 of the poorest are not shown.</span>
        </div>
      )}
    </Card>
  );
}

function RepeatCard({ detail }: { detail?: ModelVersionDetail }) {
  const m = detail?.metrics as { best?: string; brier_base_rate?: number; models?: Record<string, { brier: number; pr_auc: number }> } | undefined;
  const best = m?.best ? m.models?.[m.best] : undefined;
  return (
    <Card title="Repeat forecast" note="planning only">
      {!detail ? <span className="muted">No repeat forecaster active.</span> : <>
        <div className="grid cols-2">
          <Stat label="Model" value={m?.best === "repeat_history" ? "History" : "LightGBM"} sub={m?.best === "repeat_history" ? "4 inputs · beat LightGBM" : "all features"} />
          <Stat label="Brier score" value={best ? num(best.brier, 3) : "—"} sub={m?.brier_base_rate ? `base rate ${num(m.brier_base_rate, 3)}` : undefined} />
        </div>
        <span className="small muted">Forecasts who will apply again within a year, for planning. Never used to decide who is helped.</span>
      </>}
    </Card>
  );
}

function ActivateModal({ v, onClose }: { v: ModelVersion; onClose: () => void }) {
  const failed = v.kind !== "rules" && !v.gates_passed;
  const [force, setForce] = useState(false);
  const [reason, setReason] = useState("");
  const act = useApiMutation<{ _v: string; force: boolean; reason: string | null }>("POST", (b) => `/models/${b._v}/activate`, ["/models", "/dashboard", "/applications", "/reviews"]);
  return (
    <Modal title={`Activate ${v.model_version}?`} onClose={onClose} actions={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button kind="primary" disabled={act.isPending || (failed && (!force || !reason.trim()))}
        onClick={() => act.mutate({ _v: v.model_version, force, reason: reason.trim() || null }, { onSuccess: onClose })}>Activate</Button>
    </>}>
      <p style={{ margin: 0 }}>It replaces the active {v.purpose === "need" ? "need model for new allocations" : "repeat forecaster"}. Applications already allocated keep their scores.</p>
      {failed && <>
        <div className="callout error">This version failed its evaluation checks.</div>
        <Check label="Activate anyway" checked={force} onChange={(e) => setForce(e.target.checked)} />
        <Field label="Reason (recorded on the version)"><TextArea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      </>}
      {act.error && <ErrorBox error={act.error} />}
    </Modal>
  );
}
