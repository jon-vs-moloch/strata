/**
 * Overview: one screen that shows what Strata is doing and whether it's working.
 *
 *   Refinery line    where each worker lane is in the task pipeline right now
 *   Lanes            what each lane is working on, step by step
 *   Improvement loop candidates proposed by each tier, their measured effect,
 *                    promotion decisions, and the secondary-ignition check
 *   Verifier         how strictly outputs are being checked, and why
 *   Context cost     which artifacts are eating the small model's context
 *   Evals, transport accuracy trends and provider pacing
 *
 * Every number comes from the API (or from recorded sample data in demo mode).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  Activity, ArrowRight, Check, CircleDashed, Cpu, Gauge, Layers, RefreshCw, ShieldCheck, Sparkles, X,
} from 'lucide-react';
import '../styles/overview.css';

const POLL_MS = 8000;

const STAGES = [
  { id: 'intake', label: 'Intake', hint: 'Chat and queued tasks', steps: ['intake', 'queued'] },
  { id: 'decompose', label: 'Decompose', hint: 'Split into attemptable steps', steps: ['decomposition', 'planning', 'plan_review'] },
  { id: 'research', label: 'Research', hint: 'Gather evidence and context', steps: ['research', 'browsing', 'context'] },
  { id: 'implement', label: 'Implement', hint: 'Produce the artifact', steps: ['implementation', 'synthesis', 'tool', 'proposal'] },
  { id: 'verify', label: 'Verify', hint: 'Judge, verify, evaluate', steps: ['verification', 'judging', 'evaluation', 'review'] },
  { id: 'promote', label: 'Promote', hint: 'Keep what measurably helped', steps: ['promotion'] },
];

const POOLS = [
  { id: 'trainer', label: 'Trainer', sub: 'cloud · improves the agent', tone: 'trainer' },
  { id: 'local_agent', label: 'Local agent', sub: 'local 4B model · does the work', tone: 'agent' },
  { id: 'remote_agent', label: 'Remote agent', sub: 'cloud fallback · same role', tone: 'agent' },
];

const fmtTokens = (value) => {
  const n = Number(value) || 0;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
};

const fmtPct = (value, digits = 0) => `${(Number(value || 0) * 100).toFixed(digits)}%`;

const parseTs = (value) => {
  if (!value) return null;
  const raw = String(value);
  const t = Date.parse(/(?:Z|[+-]\d{2}:\d{2})$/.test(raw) ? raw : `${raw}Z`);
  return Number.isNaN(t) ? null : t;
};

const stageForStep = (step) => {
  const key = String(step || '').toLowerCase();
  if (!key) return null;
  const found = STAGES.find((stage) => stage.steps.some((s) => key.includes(s)));
  return found ? found.id : 'implement';
};

const shortId = (identifier) => {
  const value = String(identifier || '');
  const parts = value.split('/');
  return parts.length > 2 ? `…/${parts.slice(-2).join('/')}` : value;
};

const hostOf = (key) => {
  const raw = String(key || '').split('|').pop();
  try {
    const url = new URL(raw);
    return `${url.hostname}${url.port ? `:${url.port}` : ''}`;
  } catch {
    return raw;
  }
};

/* ── Small building blocks ──────────────────────────────────────────────── */

const Panel = ({ icon: Icon, title, subtitle, action, children, className = '' }) => (
  <section className={`ov-panel ${className}`}>
    <header className="ov-panel-head">
      <div className="ov-panel-title">
        {Icon && <Icon size={15} aria-hidden="true" />}
        <h2>{title}</h2>
      </div>
      {action}
    </header>
    {subtitle && <p className="ov-panel-sub">{subtitle}</p>}
    {children}
  </section>
);

const ActionButton = ({ onClick, children, busy }) => (
  <button type="button" className="ov-action" onClick={onClick} disabled={busy}>
    {busy ? <RefreshCw size={13} className="ov-spin" aria-hidden="true" /> : null}
    {children}
  </button>
);

const Empty = ({ children }) => <div className="ov-empty">{children}</div>;

const Kpi = ({ label, value, detail, tone }) => (
  <div className="ov-kpi">
    <div className="ov-kpi-label">{label}</div>
    <div className={`ov-kpi-value ${tone ? `tone-${tone}` : ''}`}>{value}</div>
    {detail && <div className="ov-kpi-detail">{detail}</div>}
  </div>
);

const Meter = ({ label, value, max, marks = [], format = (v) => v, tone = 'neutral', hint }) => {
  const clamped = Math.max(0, Math.min(1, (Number(value) || 0) / max));
  return (
    <div className="ov-meter" title={hint}>
      <div className="ov-meter-row">
        <span>{label}</span>
        <span className="ov-mono">{format(value)}</span>
      </div>
      <div className="ov-meter-track">
        <div className={`ov-meter-fill tone-${tone}`} style={{ width: `${clamped * 100}%` }} />
        {marks.map((mark) => (
          <span
            key={mark.at}
            className="ov-meter-mark"
            style={{ left: `${Math.min(1, mark.at / max) * 100}%` }}
            title={mark.label}
          />
        ))}
      </div>
    </div>
  );
};

const Sparkline = ({ values = [], tone = 'agent', dashed = false, label }) => {
  const width = 132;
  const height = 34;
  if (!values.length) return <svg width={width} height={height} aria-hidden="true" />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => [
    (i / Math.max(1, values.length - 1)) * (width - 6) + 3,
    height - 4 - ((v - min) / span) * (height - 8),
  ]);
  const d = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const [lx, ly] = pts[pts.length - 1];
  return (
    <svg width={width} height={height} role="img" aria-label={label} className="ov-spark">
      <title>{label}</title>
      <path d={d} fill="none" stroke={`var(--${tone})`} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" strokeDasharray={dashed ? '4 3' : undefined} />
      <circle cx={lx} cy={ly} r="3.5" fill={`var(--${tone})`} stroke="var(--surface-1)" strokeWidth="2" />
    </svg>
  );
};

/* ── Sections ───────────────────────────────────────────────────────────── */

const RefineryLine = ({ tasks, pools, promotions }) => {
  const counts = useMemo(() => {
    const out = { intake: 0, decompose: 0, research: 0, implement: 0, verify: 0, promote: promotions };
    for (const task of tasks || []) {
      const status = String(task.status || '').toLowerCase();
      const type = String(task.type || '').toLowerCase();
      if (status === 'pending' && type !== 'judge') out.intake += 1;
      else if (status === 'decomposed') out.decompose += 1;
      else if (status === 'working' || status === 'blocked') {
        if (type === 'research') out.research += 1;
        else if (type === 'judge') out.verify += 1;
        else if (type === 'decomp') out.decompose += 1;
        else out.implement += 1;
      } else if (status === 'pending' && type === 'judge') out.verify += 1;
    }
    return out;
  }, [tasks, promotions]);

  const pucks = POOLS.map((pool) => {
    const detail = pools?.[pool.id] || {};
    const mode = String(detail.activity_mode || detail.status || '').toUpperCase();
    const active = Boolean(detail.current_task_id) && !['IDLE', 'STOPPED', 'OFFLINE'].includes(mode);
    return {
      ...pool,
      active,
      paused: mode === 'PAUSED',
      stage: active ? stageForStep(detail.step) || 'implement' : null,
      title: detail.current_task_title,
      step: detail.step_label,
    };
  });

  return (
    <div className="ov-flow" role="list" aria-label="Refinery pipeline">
      {STAGES.map((stage, index) => {
        const here = pucks.filter((puck) => puck.stage === stage.id);
        return (
          <div key={stage.id} className={`ov-stage ${here.length ? 'is-live' : ''}`} role="listitem">
            <div className="ov-stage-index">{String(index + 1).padStart(2, '0')}</div>
            <div className="ov-stage-name">{stage.label}</div>
            <div className="ov-stage-hint">{stage.hint}</div>
            <div className="ov-stage-count">
              <span className="ov-mono">{counts[stage.id]}</span>
              <span>{stage.id === 'promote' ? 'promoted' : counts[stage.id] === 1 ? 'task' : 'tasks'}</span>
            </div>
            <div className="ov-pucks">
              {here.map((puck) => (
                <span
                  key={puck.id}
                  className={`ov-puck tone-${puck.tone} ${puck.paused ? 'is-paused' : ''}`}
                  title={`${puck.label}: ${puck.step || 'working'} — ${puck.title || ''}`}
                >
                  <span className="ov-puck-dot" aria-hidden="true" />
                  {puck.label}
                </span>
              ))}
            </div>
            {index < STAGES.length - 1 && <ArrowRight className="ov-stage-arrow" size={14} aria-hidden="true" />}
          </div>
        );
      })}
    </div>
  );
};

const LaneCard = ({ pool, detail }) => {
  const mode = String(detail?.activity_mode || detail?.status || 'IDLE').toUpperCase();
  const live = !['IDLE', 'STOPPED', 'OFFLINE', 'PAUSED'].includes(mode) && detail?.current_task_id;
  const steps = Array.isArray(detail?.recent_steps) ? detail.recent_steps.slice(-5) : [];
  return (
    <div className={`ov-lane tone-${pool.tone}`}>
      <div className="ov-lane-head">
        <span className={`ov-status-dot ${live ? 'is-live' : mode === 'PAUSED' ? 'is-paused' : ''}`} aria-hidden="true" />
        <div className="ov-lane-name">
          <strong>{pool.label}</strong>
          <span>{pool.sub}</span>
        </div>
        <span className="ov-lane-mode">{mode === 'GENERATING' ? 'Working' : mode.charAt(0) + mode.slice(1).toLowerCase()}</span>
      </div>
      <div className="ov-lane-task">{detail?.current_task_title || 'No active task'}</div>
      {detail?.step_label && (
        <div className="ov-lane-step">
          <span>{detail.step_label}</span>
          {detail.step_detail && <span className="ov-lane-step-detail">{detail.step_detail}</span>}
        </div>
      )}
      <div className="ov-trail" aria-label="Recent steps">
        {steps.map((step, index) => (
          <span key={`${step.at}-${index}`} className={`ov-trail-seg ${index === steps.length - 1 ? 'is-current' : ''}`} title={`${step.label}${step.detail ? `: ${step.detail}` : ''}`} />
        ))}
      </div>
      <div className="ov-lane-meta">
        <span>queue {detail?.queue_depth ?? 0}</span>
        <span>heartbeat {detail?.heartbeat_age_s != null ? `${detail.heartbeat_age_s}s` : '—'}</span>
        <span>{detail?.progress_label || ''}</span>
      </div>
    </div>
  );
};

const RECOMMENDATION = {
  promote: { label: 'Promoted', tone: 'ok', Icon: Check },
  reject: { label: 'Rejected', tone: 'danger', Icon: X },
  insufficient_signal: { label: 'Not enough signal', tone: 'warn', Icon: CircleDashed },
};

const LoopTimeline = ({ reports, current }) => {
  const maxAbs = Math.max(0.02, ...reports.map((r) => Math.abs(Number(r?.deltas?.structured_eval_harness_accuracy) || 0)));
  return (
    <ol className="ov-loop">
      {reports.map((report) => {
        const tier = report?.proposal_metadata?.proposer_tier === 'agent' ? 'agent' : 'trainer';
        const delta = Number(report?.deltas?.structured_eval_harness_accuracy) || 0;
        const rec = RECOMMENDATION[report.recommendation] || { label: report.recommendation || 'pending', tone: 'neutral', Icon: CircleDashed };
        const width = (Math.abs(delta) / maxAbs) * 50;
        return (
          <li key={report.candidate_change_id} className={`ov-loop-row tone-${tier}`}>
            <span className="ov-loop-band" aria-hidden="true" />
            <div className="ov-loop-main">
              <div className="ov-loop-title">
                <span className="ov-mono">{report.candidate_change_id}</span>
                <span className={`ov-chip tone-${tier}`}>{tier}</span>
                {current === report.candidate_change_id && <span className="ov-chip tone-accent">live</span>}
              </div>
              <div className="ov-loop-why">{report?.proposal_metadata?.rationale || report?.diagnostic_review?.recommended_fix || 'No rationale recorded.'}</div>
            </div>
            <div className="ov-delta" title={`Change in structured eval accuracy vs baseline over ${report.run_count || 'several'} runs`}>
              <div className="ov-delta-track">
                <span className="ov-delta-zero" aria-hidden="true" />
                <span
                  className={`ov-delta-bar ${delta >= 0 ? 'is-pos' : 'is-neg'}`}
                  style={delta >= 0 ? { left: '50%', width: `${width}%` } : { right: '50%', width: `${width}%` }}
                />
              </div>
              <span className="ov-mono ov-delta-label">{delta >= 0 ? '+' : '−'}{Math.abs(delta * 100).toFixed(1)} pt</span>
            </div>
            <span className={`ov-verdict tone-${rec.tone}`}>
              <rec.Icon size={12} aria-hidden="true" />
              {rec.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
};

const RatingBars = ({ ratings }) => {
  const rows = Object.entries(ratings || {})
    .map(([id, entry]) => ({ id, rating: Number(entry?.rating) || 1500, matches: entry?.matches || 0 }))
    .sort((a, b) => b.rating - a.rating)
    .slice(0, 6);
  if (!rows.length) return <Empty>No head-to-head ratings yet.</Empty>;
  const span = Math.max(20, ...rows.map((r) => Math.abs(r.rating - 1500)));
  return (
    <div className="ov-ratings">
      {rows.map((row) => {
        const diff = row.rating - 1500;
        const width = (Math.abs(diff) / span) * 50;
        return (
          <div key={row.id} className="ov-rating" title={`${row.id}: ${row.rating.toFixed(0)} after ${row.matches} matchups`}>
            <span className="ov-mono ov-rating-id">{row.id.replace('harness:', '')}</span>
            <div className="ov-delta-track">
              <span className="ov-delta-zero" aria-hidden="true" />
              <span className={`ov-delta-bar ${diff >= 0 ? 'is-pos' : 'is-neg'}`} style={diff >= 0 ? { left: '50%', width: `${width}%` } : { right: '50%', width: `${width}%` }} />
            </div>
            <span className="ov-mono ov-rating-val">{row.rating.toFixed(0)}</span>
          </div>
        );
      })}
    </div>
  );
};

const JudgeTrust = ({ trust }) => {
  const rows = Object.entries(trust?.by_tier || {});
  if (!rows.length) return null;
  return (
    <div className="ov-trust">
      <div className="ov-trust-head">Judge calibration</div>
      <p>Before each experiment the judging tier predicts whether the change will help. Scored predictions build (or erode) trust in that judge.</p>
      {rows.map(([tier, entry]) => (
        <Meter key={tier} label={`${tier} predictions · ${entry.count || 0} scored`} value={entry.trust || 0} max={1}
          format={(v) => fmtPct(v)} tone={tier === 'agent' ? 'agent' : 'trainer'} />
      ))}
    </div>
  );
};

const CADENCE_COPY = { 1: 'every output', 2: 'every 2nd output', 4: 'every 4th output' };

const explainCadence = (policy, tiers) => {
  if (!policy) return '';
  const strict = tiers?.[0];
  const mid = tiers?.[1];
  if (policy.cadence === 1 && strict) {
    if (policy.sample_count < strict.min_samples) return `Only ${policy.sample_count} recent samples; needs ${strict.min_samples} before relaxing.`;
    if (policy.recent_error_rate >= strict.error_rate) return `Recent error rate ${fmtPct(policy.recent_error_rate)} is above ${fmtPct(strict.error_rate)}.`;
    if (policy.verifier_issue_rate >= strict.issue_rate) return `Verifier flags ${fmtPct(policy.verifier_issue_rate)} of outputs (limit ${fmtPct(strict.issue_rate)}).`;
    return 'Verifier mechanism failures are too frequent to relax.';
  }
  if (policy.cadence === 2 && mid) {
    if (policy.sample_count < mid.min_samples) return `Clean so far; relaxes further after ${mid.min_samples} samples (has ${policy.sample_count}).`;
    return `Error rate ${fmtPct(policy.recent_error_rate)} is between ${fmtPct(mid.error_rate)} and ${fmtPct(strict?.error_rate)}.`;
  }
  return 'Consistently clean: spot-checking only.';
};

const VerifierCard = ({ data }) => {
  const tiers = data?.tiers || [];
  const lanes = [
    { id: 'agent', label: 'Agent', tone: 'agent' },
    { id: 'trainer', label: 'Trainer', tone: 'trainer' },
  ];
  if (!data?.policy) return <Empty>Verifier policy unavailable.</Empty>;
  const strict = tiers[0] || {};
  const mid = tiers[1] || {};
  return (
    <div className="ov-verifier">
      {lanes.map((lane) => {
        const policy = data.policy[lane.id];
        if (!policy) return null;
        return (
          <div key={lane.id} className={`ov-ver-lane tone-${lane.tone}`}>
            <div className="ov-ver-head">
              <strong>{lane.label}</strong>
              <span>checks {CADENCE_COPY[policy.cadence] || `1 in ${policy.cadence}`}</span>
            </div>
            <div className="ov-ladder" role="img" aria-label={`Verification cadence ${policy.cadence}`}>
              {[1, 2, 4].map((cadence) => (
                <div key={cadence} className={`ov-rung ${policy.cadence === cadence ? 'is-current' : ''}`}>
                  <span className="ov-rung-dots" aria-hidden="true">
                    {[0, 1, 2, 3].map((i) => (
                      <i key={i} className={i % cadence === 0 ? 'is-checked' : ''} />
                    ))}
                  </span>
                  <span>{cadence === 1 ? 'Strict' : cadence === 2 ? 'Relaxed' : 'Spot-check'}</span>
                </div>
              ))}
            </div>
            <p className="ov-ver-why">{explainCadence(policy, tiers)}</p>
            <Meter label="Recent samples" value={policy.sample_count} max={32} tone={lane.tone}
              marks={[{ at: strict.min_samples || 8, label: 'relax to every 2nd' }, { at: mid.min_samples || 24, label: 'relax to every 4th' }]} />
            <Meter label="Recent error rate" value={policy.recent_error_rate} max={0.5} format={(v) => fmtPct(v)}
              tone={policy.recent_error_rate >= (strict.error_rate || 0.18) ? 'danger' : policy.recent_error_rate >= (mid.error_rate || 0.08) ? 'warn' : 'ok'}
              marks={[{ at: mid.error_rate || 0.08, label: 'relaxed threshold' }, { at: strict.error_rate || 0.18, label: 'strict threshold' }]} />
            <Meter label="Verifier issue rate" value={policy.verifier_issue_rate} max={0.5} format={(v) => fmtPct(v)}
              tone={policy.verifier_issue_rate >= (strict.issue_rate || 0.22) ? 'danger' : policy.verifier_issue_rate >= (mid.issue_rate || 0.12) ? 'warn' : 'ok'}
              marks={[{ at: mid.issue_rate || 0.12, label: 'relaxed threshold' }, { at: strict.issue_rate || 0.22, label: 'strict threshold' }]} />
          </div>
        );
      })}
    </div>
  );
};

const ContextCost = ({ context }) => {
  const threshold = Number(context?.policy?.warning_estimated_tokens) || 1800;
  const artifacts = Object.values(context?.stats?.artifacts || {})
    .sort((a, b) => (b.recent_total_estimated_tokens || 0) - (a.recent_total_estimated_tokens || 0))
    .slice(0, 7);
  const totals = context?.stats?.totals || {};
  const max = Math.max(1, ...artifacts.map((a) => a.recent_total_estimated_tokens || 0));
  const oversizedFiles = (context?.file_scan?.warnings || []).length;
  return (
    <>
      <div className="ov-mini-kpis">
        <Kpi label="Tokens loaded" value={fmtTokens(totals.recent_estimated_tokens)} detail={`${totals.recent_load_count || 0} loads`} />
        <Kpi label="Oversized loads" value={(context?.warnings || []).length} detail={`> ${fmtTokens(threshold)} tok each`} tone={(context?.warnings || []).length ? 'warn' : undefined} />
        <Kpi label="Oversized files" value={oversizedFiles} detail="from the last scan" />
      </div>
      {artifacts.length ? (
        <div className="ov-bars" role="list">
          {artifacts.map((artifact) => {
            const heavy = (artifact.avg_estimated_tokens || 0) > threshold;
            return (
              <div key={`${artifact.artifact_type}:${artifact.identifier}`} className="ov-bar-row" role="listitem"
                title={`${artifact.identifier}: ${artifact.recent_load_count} loads, ~${Math.round(artifact.avg_estimated_tokens)} tokens each`}>
                <div className="ov-bar-label">
                  <span className="ov-mono">{shortId(artifact.identifier)}</span>
                  <span className="ov-bar-type">{String(artifact.artifact_type).replace('_', ' ')} · {artifact.recent_load_count}×</span>
                </div>
                <div className="ov-bar-track">
                  <span className={`ov-bar-fill ${heavy ? 'is-heavy' : ''}`} style={{ width: `${((artifact.recent_total_estimated_tokens || 0) / max) * 100}%` }} />
                </div>
                <span className="ov-mono ov-bar-val">{fmtTokens(artifact.recent_total_estimated_tokens)}</span>
              </div>
            );
          })}
          <div className="ov-legend">
            <span><i className="ov-swatch" /> total tokens loaded</span>
            <span><i className="ov-swatch is-heavy" /> average load above the {fmtTokens(threshold)}-token warning line</span>
          </div>
        </div>
      ) : (
        <Empty>No context loads recorded yet.</Empty>
      )}
    </>
  );
};

const EvalTrends = ({ variants }) => {
  const rows = (variants || []).slice(0, 4);
  if (!rows.length) return <Empty>No eval samples yet.</Empty>;
  return (
    <div className="ov-evals">
      {rows.map((variant) => {
        const metric = variant?.metrics?.eval_sample_tick_accuracy || variant?.metrics?.eval_matrix_accuracy || {};
        const tone = variant.mode === 'trainer' ? 'trainer' : 'agent';
        const delta = Number(metric.delta) || 0;
        return (
          <div key={variant.variant_id} className="ov-eval">
            <div className="ov-eval-name">
              <strong>{variant.mode === 'trainer' ? 'Trainer' : 'Agent'}</strong>
              <span>{variant.include_context ? 'with context' : 'no context'} · {variant.suite_name}</span>
            </div>
            <Sparkline values={metric.values || []} tone={tone} dashed={!variant.include_context}
              label={`${variant.variant_id} accuracy trend`} />
            <div className="ov-eval-val">
              <span className="ov-mono">{fmtPct(metric.latest)}</span>
              <span className={`ov-mono ${delta >= 0 ? 'tone-ok' : 'tone-danger'}`}>{delta >= 0 ? '+' : '−'}{Math.abs(delta * 100).toFixed(0)} pt</span>
            </div>
          </div>
        );
      })}
    </div>
  );
};

const Transport = ({ providers }) => {
  const rows = Object.entries(providers || {}).slice(0, 4);
  if (!rows.length) return <Empty>No provider traffic recorded yet.</Empty>;
  return (
    <table className="ov-table">
      <thead>
        <tr><th>Endpoint</th><th>Requests</th><th>OK</th><th>Latency</th><th>Paced wait</th></tr>
      </thead>
      <tbody>
        {rows.map(([key, stats]) => (
          <tr key={key}>
            <td className="ov-mono">{hostOf(key)}</td>
            <td className="ov-mono">{stats.request_count ?? '—'}</td>
            <td className="ov-mono">{stats.request_count ? fmtPct((stats.success_count || 0) / stats.request_count) : '—'}</td>
            <td className="ov-mono">{Math.round(stats.avg_latency_ms || 0)} ms</td>
            <td className="ov-mono">{Math.round(stats.avg_wait_ms || 0)} ms</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};

/* ── View ───────────────────────────────────────────────────────────────── */

const ENDPOINTS = {
  dashboard: '/admin/dashboard?limit=6',
  history: '/admin/experiments/history',
  ignition: '/admin/experiments/secondary_ignition',
  ratings: '/admin/variants/ratings',
  context: '/admin/context/telemetry',
  verifier: '/admin/verifier/policy',
  providers: '/admin/providers/telemetry',
  trust: '/admin/predictions/trust',
};

export default function OverviewView({ apiBase, tasks, workPoolDetails, demoMode, onQueueBootstrap, onContextScan, onOpenView }) {
  const [data, setData] = useState({});
  const [failed, setFailed] = useState([]);
  const [busy, setBusy] = useState('');

  const load = useCallback(async () => {
    const entries = Object.entries(ENDPOINTS);
    const results = await Promise.allSettled(entries.map(([, path]) => axios.get(`${apiBase}${path}`)));
    const next = {};
    const errors = [];
    results.forEach((result, index) => {
      const [key] = entries[index];
      if (result.status === 'fulfilled') next[key] = result.value.data;
      else errors.push(key);
    });
    setData((prev) => ({ ...prev, ...next }));
    setFailed(errors);
  }, [apiBase]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const run = async (key, fn) => {
    if (!fn) return;
    setBusy(key);
    try {
      await fn();
      await load();
    } finally {
      setBusy('');
    }
  };

  const dashboard = data.dashboard?.dashboard || {};
  const overview = dashboard.overview || {};
  const promotions = dashboard.promotion_counts || {};
  const reports = useMemo(
    () => [...(data.history?.reports || [])].sort((a, b) => (parseTs(b.recorded_at) || 0) - (parseTs(a.recorded_at) || 0)).slice(0, 6),
    [data.history],
  );
  const ignition = data.ignition || {};
  const ratingDomains = data.ratings?.ratings?.by_domain || {};
  const primaryDomain = Object.keys(ratingDomains).sort((a, b) => Object.keys(ratingDomains[b]).length - Object.keys(ratingDomains[a]).length)[0];
  const context = data.context?.context;

  return (
    <div className="ov-root">
      <section className="ov-hero">
        <div className="ov-hero-copy">
          <div className="ov-eyebrow">
            <Layers size={13} aria-hidden="true" /> The token refinery
            {demoMode && <span className="ov-chip tone-warn">Demo data</span>}
          </div>
          <h1>Small models in. Reliable work out.</h1>
          <p>
            Strata wraps a small local model in decomposition, memory, verification and evals, then measures
            whether the system is getting better and whether the small model is starting to improve itself.
          </p>
        </div>
        <div className="ov-hero-kpis">
          <Kpi label="Attempt success" value={overview.total_attempts ? `${Math.round(overview.success_rate || 0)}%` : '—'}
            detail={`${overview.succeeded_attempts || 0} of ${overview.total_attempts || 0} attempts`} />
          <Kpi label="Promotions" value={promotions.total_history ?? 0}
            detail={`trainer ${promotions.trainer || 0} · agent ${promotions.agent || 0}`} />
          <Kpi label="Context loaded" value={fmtTokens(context?.stats?.totals?.recent_estimated_tokens)}
            detail={`${context?.stats?.totals?.recent_load_count || 0} loads costed`} />
          <Kpi label="Secondary ignition" value={ignition.detected ? 'Detected' : 'Not yet'} tone={ignition.detected ? 'ok' : undefined}
            detail="weak tier improving itself" />
        </div>
      </section>

      {failed.length > 0 && !demoMode && (
        <div className="ov-alert" role="status">
          Some telemetry is unavailable ({failed.join(', ')}). Is the API running at <span className="ov-mono">{apiBase}</span>?
        </div>
      )}

      <Panel icon={Activity} title="Refinery line" subtitle="Where each worker lane is in the pipeline right now. Counts are tasks currently at each stage."
        action={<button type="button" className="ov-link" onClick={() => onOpenView?.('tasks')}>All tasks <ArrowRight size={12} aria-hidden="true" /></button>}>
        <RefineryLine tasks={tasks} pools={workPoolDetails} promotions={promotions.total_history || 0} />
        <div className="ov-lanes">
          {POOLS.map((pool) => <LaneCard key={pool.id} pool={pool} detail={workPoolDetails?.[pool.id]} />)}
        </div>
      </Panel>

      <div className="ov-grid">
        <Panel className="ov-span-2" icon={Sparkles} title="Self-improvement loop"
          subtitle="Both tiers propose changes to the eval harness. Each candidate is scored against the baseline over repeated runs and promoted only if it measurably helps."
          action={<ActionButton busy={busy === 'bootstrap'} onClick={() => run('bootstrap', onQueueBootstrap)}>Queue bootstrap cycle</ActionButton>}>
          <div className={`ov-ignition ${ignition.detected ? 'is-on' : ''}`}>
            <div className="ov-ignition-mark" aria-hidden="true">{ignition.detected ? <Check size={14} /> : <CircleDashed size={14} />}</div>
            <div>
              <strong>Secondary ignition {ignition.detected ? 'detected' : 'not detected yet'}</strong>
              <span>
                {ignition.detected
                  ? `${ignition.candidate_change_id}, proposed by the agent tier, was promoted and improved agent-tier evals.`
                  : 'The open question: has a change proposed by the weak tier been promoted and made the weak tier measurably better?'}
              </span>
            </div>
          </div>
          {reports.length ? <LoopTimeline reports={reports} current={data.history?.current_promoted_candidate} /> : <Empty>No experiment reports yet. Queue a bootstrap cycle to start the loop.</Empty>}
          <div className="ov-legend">
            <span><i className="ov-swatch tone-trainer" /> trainer proposal</span>
            <span><i className="ov-swatch tone-agent" /> agent proposal</span>
            <span>bar: change in eval accuracy vs baseline</span>
          </div>
        </Panel>

        <Panel icon={Gauge} title="Variant ratings" subtitle={primaryDomain ? `Head-to-head rating on ${primaryDomain.split(':').pop()}, relative to the 1500 baseline.` : 'Head-to-head ratings between harness variants.'}>
          <RatingBars ratings={primaryDomain ? ratingDomains[primaryDomain] : null} />
          <JudgeTrust trust={data.trust?.trust} />
        </Panel>

        <Panel icon={ShieldCheck} title="Annealing verifier"
          subtitle="Verification tightens when a lane is failing and relaxes as clean evidence accumulates. No per-tier hard-coding.">
          <VerifierCard data={data.verifier} />
        </Panel>

        <Panel className="ov-span-2" icon={Layers} title="Context cost"
          subtitle="Every spec, file, memory and history load is costed in estimated tokens, so oversized artifacts show up before they quietly tax a 4B model."
          action={<ActionButton busy={busy === 'scan'} onClick={() => run('scan', onContextScan)}>Rescan files</ActionButton>}>
          <ContextCost context={context} />
        </Panel>

        <Panel icon={Activity} title="Eval accuracy" subtitle="Sampled eval ticks per variant. Dashed: no context loaded.">
          <EvalTrends variants={dashboard.eval_profiles?.variants} />
        </Panel>

        <Panel className="ov-span-2" icon={Cpu} title="Transport" subtitle="Each endpoint carries its own pacing, so free tiers and laptop fans both survive.">
          <Transport providers={data.providers?.providers} />
        </Panel>
      </div>
    </div>
  );
}
