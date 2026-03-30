import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { BookOpen, Pencil, Plus, Search } from 'lucide-react';
import TaskCard from '../components/TaskCard';

const parseTimestamp = (dateString) => {
  if (!dateString) return null;
  const raw = String(dateString).trim();
  if (!raw) return null;
  const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/.test(raw);
  const normalized = hasTimezone ? raw : `${raw}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
};

const formatAbsoluteTime = (dateString) => {
  if (!dateString) return '—';
  const date = parseTimestamp(dateString);
  if (!date) return '—';
  return date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};

const formatRelativeTime = (dateString) => {
  if (!dateString) return 'never';
  const date = parseTimestamp(dateString);
  if (!date) return 'unknown';
  const deltaMs = Date.now() - date.getTime();
  const minutes = Math.max(0, Math.floor(deltaMs / 60000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
};

const formatAbsoluteWithRelative = (dateString) => {
  if (!dateString) return '—';
  return `${formatAbsoluteTime(dateString)} (${formatRelativeTime(dateString)})`;
};

const summarizeBootstrapReasons = (items = []) => {
  const counts = new Map();
  items.forEach((item) => {
    const key = String(item?.reason || item?.resolution?.decision || 'unknown');
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([reason, count]) => `${reason.replace(/_/g, ' ')} ×${count}`);
};

const TelemetryCell = ({ value, label }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
    <div style={{ fontSize: '16px', fontWeight: 800, color: '#edeeef', fontFamily: "'JetBrains Mono', monospace" }}>{value}</div>
    <div style={{ fontSize: '9px', color: '#444', fontWeight: 700, letterSpacing: '0.1em' }}>{label}</div>
  </div>
);

const DashboardPanel = ({ title, children }) => (
  <div style={{ background: '#141418', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '14px', padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
    <div style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.12em' }}>{title}</div>
    {children}
  </div>
);

const RoutePill = ({ label, value, tone = 'neutral' }) => {
  const tones = {
    neutral: { bg: 'rgba(255,255,255,0.04)', border: 'rgba(255,255,255,0.08)', text: '#c7c8d6' },
    success: { bg: 'rgba(0,242,148,0.08)', border: 'rgba(0,242,148,0.18)', text: '#9df7d0' },
    warning: { bg: 'rgba(255,184,77,0.08)', border: 'rgba(255,184,77,0.18)', text: '#ffd39b' },
    danger: { bg: 'rgba(255,92,92,0.08)', border: 'rgba(255,92,92,0.18)', text: '#ffb3b3' },
  };
  const theme = tones[tone] || tones.neutral;
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '6px 10px', borderRadius: '999px', background: theme.bg, border: `1px solid ${theme.border}`, minWidth: 0 }}>
      <span style={{ fontSize: '10px', letterSpacing: '0.08em', color: '#6f7183', fontWeight: 800 }}>{label}</span>
      <span style={{ fontSize: '11px', color: theme.text, fontFamily: "'JetBrains Mono', monospace", whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{value}</span>
    </div>
  );
};

const Sparkline = ({ values, color = '#8257e5' }) => {
  const points = Array.isArray(values) ? values : [];
  if (points.length < 2) {
    return <div style={{ fontSize: '11px', color: '#666' }}>—</div>;
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const width = 120;
  const height = 28;
  const polyline = points.map((value, index) => {
    const x = (index / Math.max(1, points.length - 1)) * width;
    const y = height - (((value - min) / span) * height);
    return `${x},${y}`;
  }).join(' ');
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ overflow: 'visible' }}>
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="2"
        points={polyline}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
};

const TasksView = ({
  currentScope,
  activeTasks,
  finishedTasks,
  workerStatus,
  laneStatuses,
  scopeOperationalMetrics,
  scopeAttemptMetrics,
  onArchiveTask,
  nowMs,
}) => {
  const [showFinished, setShowFinished] = useState(false);
  const scopeLabel = currentScope === 'home' ? 'Global' : currentScope === 'trainer' ? 'Trainer' : 'Agent';
  const scopeHealth = currentScope === 'home' ? workerStatus : (laneStatuses?.[currentScope] || 'IDLE');

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '18px' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.8fr) minmax(280px, 0.95fr)', gap: '18px', alignItems: 'start' }}>
        <DashboardPanel title="TASK QUEUE">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            <RoutePill label="SCOPE" value={scopeLabel} tone="neutral" />
            <RoutePill label="HEALTH" value={scopeHealth} tone={scopeHealth === 'RUNNING' ? 'success' : scopeHealth === 'PAUSED' ? 'warning' : 'neutral'} />
            <RoutePill label="ACTIVE" value={String(activeTasks.length)} tone="success" />
            <RoutePill label="RECENT" value={String(finishedTasks.length)} tone="neutral" />
          </div>
          <div style={{ color: '#8d8ea1', fontSize: '13px', lineHeight: 1.7 }}>
            This is the canonical task surface: active work stays visible here, recent completions remain easy to inspect, and the lane-specific task rail can stay focused on chat context.
          </div>
        </DashboardPanel>

        <DashboardPanel title="TASK TELEMETRY">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '12px' }}>
            <TelemetryCell value={scopeOperationalMetrics.working || '—'} label="RUNNING" />
            <TelemetryCell value={scopeOperationalMetrics.queued || '—'} label="QUEUED" />
            <TelemetryCell value={scopeOperationalMetrics.blocked || '—'} label="BLOCKED" />
            <TelemetryCell value={scopeOperationalMetrics.needsYou || '—'} label="NEEDS YOU" />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '12px' }}>
            <TelemetryCell value={scopeAttemptMetrics.success10 || '—'} label="LAST 10" />
            <TelemetryCell value={scopeAttemptMetrics.success50 || '—'} label="LAST 50" />
            <TelemetryCell value={scopeAttemptMetrics.averageDurationLabel || '—'} label="AVG ATTEMPT" />
          </div>
        </DashboardPanel>
      </div>

      <DashboardPanel title="ACTIVE TASKS">
        {activeTasks.length ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {activeTasks.map((task) => (
              <TaskCard key={task.id} task={task} onArchive={() => onArchiveTask(task.id)} nowMs={nowMs} />
            ))}
          </div>
        ) : (
          <div style={{ fontSize: '13px', color: '#8d8ea1', lineHeight: 1.7 }}>
            No active tasks in this scope right now.
          </div>
        )}
      </DashboardPanel>

      <DashboardPanel title="RECENT COMPLETIONS">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
          <div style={{ fontSize: '13px', color: '#8d8ea1' }}>
            Finished, abandoned, and cancelled work stays here until you archive it.
          </div>
          <button
            type="button"
            onClick={() => setShowFinished((value) => !value)}
            style={{
              background: 'rgba(255,255,255,0.04)',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: '10px',
              color: '#d7d9e6',
              fontSize: '11px',
              fontWeight: 800,
              letterSpacing: '0.06em',
              padding: '8px 10px',
              cursor: 'pointer',
              textTransform: 'uppercase',
            }}
          >
            {showFinished ? 'Hide' : 'Show'} {finishedTasks.length}
          </button>
        </div>
        {showFinished ? (
          finishedTasks.length ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {finishedTasks.map((task) => (
                <TaskCard key={task.id} task={task} onArchive={() => onArchiveTask(task.id)} nowMs={nowMs} />
              ))}
            </div>
          ) : (
            <div style={{ fontSize: '13px', color: '#8d8ea1', lineHeight: 1.7 }}>
              No recent finished tasks yet.
            </div>
          )
        ) : null}
      </DashboardPanel>
    </div>
  );
};

const KnowledgeView = ({
  pages,
  query,
  selectedPage,
  selectedSlug,
  onQueryChange,
  onSelectSlug,
  onCreatePage,
  onEditPage,
  onQueueSource,
}) => {
  const [knowledgeMode, setKnowledgeMode] = useState('wiki');
  const relatedPages = selectedPage?.related_pages || [];

  return (
    <div style={{ flex: 1, overflow: 'hidden', display: 'grid', gridTemplateColumns: '320px minmax(0, 1fr)', gap: '0', minHeight: 0 }}>
      <div style={{ borderRight: '1px solid rgba(255,255,255,0.05)', background: '#0d0d11', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div style={{ padding: '20px', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ fontSize: '11px', color: '#7f8091', letterSpacing: '0.12em', fontWeight: 800 }}>
            {knowledgeMode === 'wiki' ? 'KNOWLEDGE WIKI' : 'KNOWLEDGE SOURCES'}
          </div>
          <div style={{ display: 'inline-flex', borderRadius: '14px', padding: '4px', background: '#141418', border: '1px solid rgba(255,255,255,0.08)', gap: '4px' }}>
            {[
              { id: 'wiki', label: 'Wiki' },
              { id: 'sources', label: 'Sources' },
            ].map((mode) => {
              const active = knowledgeMode === mode.id;
              return (
                <button
                  key={mode.id}
                  type="button"
                  onClick={() => setKnowledgeMode(mode.id)}
                  style={{
                    background: active ? 'rgba(130,87,229,0.18)' : 'transparent',
                    border: 'none',
                    borderRadius: '10px',
                    color: active ? '#f1e8ff' : '#8d8ea1',
                    padding: '8px 12px',
                    fontSize: '12px',
                    fontWeight: 800,
                    cursor: 'pointer',
                  }}
                >
                  {mode.label}
                </button>
              );
            })}
          </div>
          <div style={{ fontSize: '12px', color: '#8d8ea1' }}>
            {knowledgeMode === 'wiki'
              ? (pages.length ? `${pages.length} page${pages.length === 1 ? '' : 's'} visible` : 'No indexed pages yet')
              : 'Source uploads and raw material should land here before integration.'}
          </div>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: '10px' }}>
          {knowledgeMode === 'wiki' && pages.map((page) => {
            const active = page.slug === selectedSlug;
            return (
              <button
                key={page.slug}
                onClick={() => onSelectSlug(page.slug)}
                style={{
                  width: '100%',
                  textAlign: 'left',
                  background: active ? 'rgba(130,87,229,0.14)' : 'transparent',
                  border: active ? '1px solid rgba(130,87,229,0.28)' : '1px solid transparent',
                  borderRadius: '12px',
                  padding: '12px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '6px',
                  cursor: 'pointer',
                  marginBottom: '8px'
                }}
              >
                <div style={{ color: active ? '#f2ecff' : '#edeeef', fontSize: '13px', fontWeight: 700 }}>
                  {page.title || page.slug}
                </div>
                <div style={{ color: '#8d8ea1', fontSize: '12px', lineHeight: 1.5 }}>
                  {page.summary || 'No summary available yet.'}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  <RoutePill label="DOMAIN" value={page.domain || 'project'} tone={active ? 'success' : 'neutral'} />
                  <RoutePill label="UPDATED" value={page.last_updated ? formatAbsoluteTime(page.last_updated) : '—'} tone="neutral" />
                </div>
              </button>
            );
          })}

          {knowledgeMode === 'wiki' && !pages.length && (
            <div style={{ padding: '18px 12px', color: '#8d8ea1', fontSize: '12px', lineHeight: 1.6 }}>
              Strata supports synthesized knowledge pages, but there are no indexed wiki pages yet. Once pages are compacted or written into the knowledge store, they will show up here.
            </div>
          )}

          {knowledgeMode === 'sources' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', padding: '10px' }}>
              <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: '14px', padding: '14px' }}>
                <div style={{ color: '#edeeef', fontSize: '13px', fontWeight: 700, marginBottom: '6px' }}>Raw source staging</div>
                <div style={{ color: '#8d8ea1', fontSize: '12px', lineHeight: 1.6 }}>
                  Uploaded files, notes, and unstructured artifacts should land here first. Integration into the canonical wiki should be queued, reviewed, and compacted separately.
                </div>
              </div>
              <button
                type="button"
                onClick={onQueueSource}
                style={{
                  background: 'rgba(255,255,255,0.04)',
                  border: '1px solid rgba(255,255,255,0.08)',
                  borderRadius: '12px',
                  padding: '12px 14px',
                  color: '#edeeef',
                  fontSize: '12px',
                  fontWeight: 700,
                  cursor: 'pointer',
                  textAlign: 'left',
                }}
              >
                Add source note and queue integration
              </button>
            </div>
          )}
        </div>
      </div>

      <div style={{ minWidth: 0, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '18px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 'min(460px, 100%)', flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', background: '#141418', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '14px', padding: '12px 14px', minWidth: 'min(460px, 100%)', flex: 1 }}>
              <Search size={15} color="#696a7b" />
              <input
                type="text"
                value={query}
                onChange={(event) => onQueryChange(event.target.value)}
                placeholder={knowledgeMode === 'wiki' ? 'Search titles, tags, aliases...' : 'Search sources coming soon...'}
                disabled={knowledgeMode !== 'wiki'}
                style={{ flex: 1, background: 'transparent', border: 'none', outline: 'none', color: '#edeeef', fontSize: '13px', opacity: knowledgeMode === 'wiki' ? 1 : 0.5 }}
              />
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {knowledgeMode === 'wiki' ? (
              <>
                <button
                  type="button"
                  onClick={onCreatePage}
                  style={{ background: 'rgba(130,87,229,0.18)', border: '1px solid rgba(130,87,229,0.28)', color: '#f1e8ff', borderRadius: '12px', padding: '10px 14px', fontSize: '12px', fontWeight: 800, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px' }}
                >
                  <Plus size={14} />
                  Add Page
                </button>
                <button
                  type="button"
                  onClick={onEditPage}
                  disabled={!selectedPage}
                  style={{ background: selectedPage ? 'rgba(255,255,255,0.05)' : 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.08)', color: selectedPage ? '#edeeef' : '#666a78', borderRadius: '12px', padding: '10px 14px', fontSize: '12px', fontWeight: 800, cursor: selectedPage ? 'pointer' : 'default', display: 'flex', alignItems: 'center', gap: '8px' }}
                >
                  <Pencil size={14} />
                  Edit Page
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={onQueueSource}
                style={{ background: 'rgba(0,187,145,0.16)', border: '1px solid rgba(0,187,145,0.28)', color: '#d9fff6', borderRadius: '12px', padding: '10px 14px', fontSize: '12px', fontWeight: 800, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px' }}
              >
                <Plus size={14} />
                Add Source
              </button>
            )}
          </div>
        </div>

        {knowledgeMode === 'wiki' && selectedPage ? (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                <RoutePill label="SLUG" value={selectedPage.slug || '—'} tone="neutral" />
                <RoutePill label="DOMAIN" value={selectedPage.domain || 'project'} tone="success" />
                <RoutePill label="CONF" value={typeof selectedPage.confidence === 'number' ? selectedPage.confidence.toFixed(2) : '—'} tone="neutral" />
                <RoutePill label="UPDATED" value={selectedPage.last_updated ? formatAbsoluteTime(selectedPage.last_updated) : '—'} tone="neutral" />
              </div>
              <div>
                <h2 style={{ margin: 0, color: '#edeeef', fontSize: '28px', lineHeight: 1.1 }}>{selectedPage.title || selectedPage.slug}</h2>
                <div style={{ marginTop: '10px', color: '#a9aaba', fontSize: '14px', lineHeight: 1.6 }}>
                  {selectedPage.summary || 'No summary available for this page yet.'}
                </div>
              </div>
            </div>

            {!!selectedPage.tags?.length && (
              <DashboardPanel title="TAGS">
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {selectedPage.tags.map((tag) => (
                    <RoutePill key={tag} label="TAG" value={tag} tone="neutral" />
                  ))}
                </div>
              </DashboardPanel>
            )}

            {!!selectedPage.aliases?.length && (
              <DashboardPanel title="ALIASES">
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {selectedPage.aliases.map((alias) => (
                    <RoutePill key={alias} label="ALIAS" value={alias} tone="neutral" />
                  ))}
                </div>
              </DashboardPanel>
            )}

            <div style={{ background: '#141418', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '16px', padding: '22px' }}>
              <div className="markdown-body" style={{ fontSize: '14px', lineHeight: '1.75', color: '#edeeef' }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {selectedPage.body || selectedPage.summary || 'No page body available yet.'}
                </ReactMarkdown>
              </div>
            </div>

            <DashboardPanel title="RELATED PAGES">
              {relatedPages.length ? (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {relatedPages.map((slug) => (
                    <button
                      key={slug}
                      onClick={() => onSelectSlug(slug)}
                      style={{
                        background: 'rgba(255,255,255,0.04)',
                        border: '1px solid rgba(255,255,255,0.08)',
                        color: '#e7e8ef',
                        borderRadius: '999px',
                        padding: '7px 12px',
                        fontSize: '11px',
                        fontWeight: 700,
                        cursor: 'pointer'
                      }}
                    >
                      {slug}
                    </button>
                  ))}
                </div>
              ) : (
                <div style={{ fontSize: '12px', color: '#666' }}>No related pages linked yet.</div>
              )}
            </DashboardPanel>
          </>
        ) : knowledgeMode === 'wiki' ? (
          <div style={{ margin: 'auto 0', padding: '48px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '14px', textAlign: 'center' }}>
            <BookOpen size={34} color="#2f3040" />
            <div style={{ fontSize: '18px', fontWeight: 700, color: '#c7c8d6' }}>Knowledge wiki is ready</div>
            <div style={{ maxWidth: '520px', fontSize: '14px', lineHeight: 1.7, color: '#8d8ea1' }}>
              This view is wired up, but the indexed knowledge store is currently empty. Once Strata writes or compacts pages into the knowledge base, they will be navigable here like a wiki.
            </div>
          </div>
        ) : (
          <div style={{ margin: 'auto 0', padding: '48px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '14px', textAlign: 'center' }}>
            <BookOpen size={34} color="#2f3040" />
            <div style={{ fontSize: '18px', fontWeight: 700, color: '#c7c8d6' }}>Sources staging area</div>
            <div style={{ maxWidth: '620px', fontSize: '14px', lineHeight: 1.7, color: '#8d8ea1' }}>
              Treat sources as the raw intake layer: files, notes, and references land here first, then a queued integration pass distills them into canonical knowledge pages. File upload is the natural next step for this surface.
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

const DashboardView = ({
  telemetry,
  dashboard,
  providerTelemetry,
  loadedContext,
  tiers,
  routingSummary,
  currentScope,
  chatLane,
  activeChatRoute,
  scopeTasks,
  scopeOperationalMetrics,
  scopeAttemptMetrics,
  specsSnapshot,
  specProposalSnapshot,
  knowledgePagesSnapshot,
  retentionSnapshot,
  variantRatingsSnapshot,
  predictionTrustSnapshot,
  proposalConfigSnapshot,
  evalJobsSnapshot,
  operatorNotice,
  onRunRetention,
  onCompactKnowledge,
  onContextScan,
  onQueueBootstrap,
  onQueueSampleTick,
  onResolveSpecProposal,
}) => {
  const scopeLabel = currentScope === 'home' ? 'Global' : currentScope === 'trainer' ? 'Trainer' : 'Agent';
  const scopeHealthLabel = currentScope === 'home'
    ? (routingSummary?.supervision?.active_jobs?.length ? 'Supervising' : 'Idle')
    : activeChatRoute?.status || 'unknown';
  const primaryDomainRatings = Object.entries(variantRatingsSnapshot?.by_domain?.['eval_harness_full_eval:bootstrap_mcq_v1'] || {})
    .sort((a, b) => (b[1]?.rating || 0) - (a[1]?.rating || 0))
    .slice(0, 5);
  const strongTrust = predictionTrustSnapshot?.by_tier?.trainer;
  const bootstrapPolicy = proposalConfigSnapshot?.bootstrap || {};
  const bootstrapInference = proposalConfigSnapshot?.inference || {};
  const bootstrapResolution = proposalConfigSnapshot?.resolution || {};
  const recentBootstrapJobs = (evalJobsSnapshot || [])
    .filter((job) => job?.system_job?.kind === 'bootstrap_cycle')
    .slice(0, 5);

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <DashboardPanel title={`${scopeLabel.toUpperCase()} HEALTH`}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
          <TelemetryCell value={scopeHealthLabel} label="SCOPE HEALTH" />
          <TelemetryCell value={scopeOperationalMetrics?.working || '—'} label="TASKS RUNNING NOW" />
          <TelemetryCell value={scopeOperationalMetrics?.queued || '—'} label="TASKS QUEUED NEXT" />
          <TelemetryCell value={scopeOperationalMetrics?.blocked || '—'} label="TASKS BLOCKED" />
        </div>
      </DashboardPanel>

      <DashboardPanel title={`${scopeLabel.toUpperCase()} THROUGHPUT`}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
          <TelemetryCell value={scopeAttemptMetrics?.success10 || '—'} label="SUCCEEDED OF LAST 10 ATTEMPTS" />
          <TelemetryCell value={scopeAttemptMetrics?.success50 || '—'} label="SUCCEEDED OF LAST 50 ATTEMPTS" />
          <TelemetryCell value={scopeAttemptMetrics?.averageDurationLabel || '—'} label="AVERAGE TIME PER ATTEMPT" />
          <TelemetryCell value={scopeTasks?.length || '—'} label="TASKS IN THIS SCOPE" />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
          <TelemetryCell value={scopeOperationalMetrics?.needsYou || '—'} label="TASKS WAITING ON YOU" />
          <TelemetryCell value={scopeOperationalMetrics?.pausedTasks || '—'} label="TASKS PAUSED" />
          <TelemetryCell value={`${scopeOperationalMetrics?.loadedContextCount || 0}/${scopeOperationalMetrics?.loadedContextBudget || 0}`} label="CONTEXT FILES / TOKENS" />
        </div>
      </DashboardPanel>

      <DashboardPanel title="EVAL SNAPSHOTS">
        {(dashboard?.eval_profiles?.variants?.length ? dashboard.eval_profiles.variants : []).slice(0, 8).map((variant) => {
          const accuracySeries = variant?.metrics?.eval_sample_tick_accuracy?.values || variant?.metrics?.eval_matrix_accuracy?.values || [];
          const accuracyLatest = variant?.metrics?.eval_sample_tick_accuracy?.latest ?? variant?.metrics?.eval_matrix_accuracy?.latest ?? 0;
          const accuracyDelta = variant?.metrics?.eval_sample_tick_accuracy?.delta ?? variant?.metrics?.eval_matrix_accuracy?.delta ?? 0;
          return (
            <div key={variant.variant_id} style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 1.2fr) 100px 100px 130px', gap: '12px', alignItems: 'center' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
                <span style={{ color: '#e7e8ef', fontSize: '12px', fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {variant.variant_id}
                </span>
                <span style={{ color: '#77798b', fontSize: '11px' }}>
                  {variant.suite_name || 'suite'} · {variant.include_context ? 'context' : 'no-context'}
                </span>
              </div>
              <div style={{ fontSize: '12px', color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
                {Math.round(accuracyLatest * 100)}% {accuracyDelta ? `(${accuracyDelta > 0 ? '+' : ''}${Math.round(accuracyDelta * 100)}pt)` : ''}
              </div>
              <div style={{ fontSize: '12px', color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
                {variant.latest_latency_s ? `${variant.latest_latency_s}s` : '—'}
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <Sparkline values={accuracySeries} color={variant.mode === 'trainer' ? '#00f294' : '#8257e5'} />
              </div>
            </div>
          );
        })}
        {!(dashboard?.eval_profiles?.variants?.length) && <div style={{ fontSize: '12px', color: '#666' }}>No eval snapshot data yet.</div>}
      </DashboardPanel>

      <DashboardPanel title="RECENT PROMOTIONS">
        {(dashboard?.reports?.slice(0, 5) || []).map((report) => (
          <div key={report.candidate_change_id} style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', fontSize: '12px' }}>
            <span style={{ color: '#8d8ea1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {(report.proposal_metadata?.proposer_tier || 'unknown')} · {report.candidate_change_id}
            </span>
            <span style={{ color: report.recommendation === 'promote' ? '#00f294' : '#ffb84d', fontFamily: "'JetBrains Mono', monospace" }}>
              {report.recommendation}
            </span>
          </div>
        ))}
        {!(dashboard?.reports?.length) && <div style={{ fontSize: '12px', color: '#666' }}>No recent promotion reports.</div>}
      </DashboardPanel>

      <DashboardPanel title="TRANSPORT">
        {Object.entries(providerTelemetry || {}).slice(0, 4).map(([key, stats]) => (
          <div key={key} style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', fontSize: '12px' }}>
            <span style={{ color: '#8d8ea1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{key}</span>
            <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
              {stats.avg_latency_ms}ms · {stats.avg_wait_ms}ms wait
            </span>
          </div>
        ))}
      </DashboardPanel>

      <DashboardPanel title="ROUTING">
        <div style={{ display: 'grid', gridTemplateColumns: '140px 1fr', gap: '10px', fontSize: '12px', alignItems: 'start' }}>
          <span style={{ color: '#8d8ea1' }}>Chat default</span>
          <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
            {activeChatRoute?.error ? activeChatRoute.error : `${chatLane} · ${activeChatRoute?.transport || activeChatRoute?.mode || '—'} · ${activeChatRoute?.provider || '—'} · ${activeChatRoute?.selected_model || activeChatRoute?.model || '—'}`}
          </span>
          <span style={{ color: '#8d8ea1' }}>Trainer tier</span>
          <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
            {routingSummary?.trainer?.error ? routingSummary.trainer.error : `${routingSummary?.trainer?.transport || '—'} · ${routingSummary?.trainer?.provider || '—'} · ${routingSummary?.trainer?.selected_model || routingSummary?.trainer?.model || '—'} (${routingSummary?.trainer?.status || 'unknown'})`}
          </span>
          <span style={{ color: '#8d8ea1' }}>Agent tier</span>
          <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
            {routingSummary?.agent?.error ? routingSummary.agent.error : `${routingSummary?.agent?.transport || '—'} · ${routingSummary?.agent?.provider || '—'} · ${routingSummary?.agent?.selected_model || routingSummary?.agent?.model || '—'} (${routingSummary?.agent?.status || 'unknown'})`}
          </span>
          <span style={{ color: '#8d8ea1' }}>Supervision</span>
          <span style={{ color: '#e7e8ef' }}>
            {routingSummary?.supervision?.active_jobs?.length
              ? `${routingSummary.supervision.active_jobs.length} supervision job${routingSummary.supervision.active_jobs.length > 1 ? 's' : ''}`
              : 'No bootstrap jobs queued'}
          </span>
        </div>
      </DashboardPanel>

      <DashboardPanel title="BOOTSTRAP POLICY">
        <div style={{ display: 'grid', gridTemplateColumns: '150px 1fr', gap: '10px', fontSize: '12px', alignItems: 'start' }}>
          <span style={{ color: '#8d8ea1' }}>Continuous tiers</span>
          <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
            {(bootstrapPolicy.continuous_proposer_tiers || []).join(' + ') || '—'}
          </span>
          <span style={{ color: '#8d8ea1' }}>Default tiers</span>
          <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
            {(bootstrapPolicy.default_proposer_tiers || []).join(' + ') || '—'}
          </span>
          <span style={{ color: '#8d8ea1' }}>Run count</span>
          <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
            continuous {bootstrapPolicy.continuous_run_count ?? '—'} · default {bootstrapPolicy.default_run_count ?? '—'}
          </span>
          <span style={{ color: '#8d8ea1' }}>Proposal temps</span>
          <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
            agent {bootstrapInference?.agent?.temperature ?? '—'} · trainer {bootstrapInference?.trainer?.temperature ?? '—'}
          </span>
          <span style={{ color: '#8d8ea1' }}>Novelty retry</span>
          <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
            {bootstrapInference.novelty_retry_count ?? '—'} retry · +{bootstrapInference.novelty_temperature_step ?? '—'} temp · cap {bootstrapInference.novelty_max_temperature ?? '—'}
          </span>
          <span style={{ color: '#8d8ea1' }}>Resolution</span>
          <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
            {bootstrapResolution.use_llm_for_ambiguous ? 'hybrid' : 'deterministic'} · {bootstrapResolution.adjudicator_tier || '—'} judge · {bootstrapResolution.vote_count ?? '—'} vote
          </span>
        </div>
      </DashboardPanel>

      <DashboardPanel title="SUPERVISION">
        <div style={{ fontSize: '12px', color: '#a9aaba' }}>
          {routingSummary?.supervision?.active_jobs?.length
            ? `${routingSummary.supervision.active_jobs.length} active supervision job${routingSummary.supervision.active_jobs.length > 1 ? 's' : ''}`
            : 'No active supervision jobs'}
        </div>
        {(routingSummary?.supervision?.active_jobs || []).map((job) => (
          <div key={job.task_id} style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 1.2fr) 100px 120px', gap: '12px', fontSize: '12px', alignItems: 'center' }}>
            <span style={{ color: '#8d8ea1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {job.title}
            </span>
            <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>{job.kind}</span>
            <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>{job.state}</span>
          </div>
        ))}
      </DashboardPanel>

      <DashboardPanel title="BOOTSTRAP RESOLUTION">
        {recentBootstrapJobs.length ? recentBootstrapJobs.map((job) => {
          const result = job?.system_job_result?.result || {};
          const skipped = result?.skipped || [];
          const evaluated = result?.evaluated || [];
          const promoted = result?.promoted || [];
          const reasonSummary = summarizeBootstrapReasons(skipped);
          return (
            <div key={job.task_id} style={{ display: 'flex', flexDirection: 'column', gap: '6px', padding: '10px 12px', borderRadius: '10px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.05)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', fontSize: '12px' }}>
                <span style={{ color: '#e7e8ef', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {job.title}
                </span>
                <span style={{ color: '#8d8ea1', fontFamily: "'JetBrains Mono', monospace" }}>
                  {job.state}
                </span>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', fontSize: '11px', color: '#a9aaba' }}>
                <span>tiers {(job?.system_job?.payload?.proposer_tiers || []).join(' + ') || '—'}</span>
                <span>evaluated {evaluated.length}</span>
                <span>skipped {skipped.length}</span>
                <span>promoted {promoted.length}</span>
              </div>
              {!!reasonSummary.length && (
                <div style={{ fontSize: '11px', color: '#c7c8d6', lineHeight: 1.6 }}>
                  {reasonSummary.join(' · ')}
                </div>
              )}
              {evaluated[0]?.resolution?.decision && (
                <div style={{ fontSize: '11px', color: '#8d8ea1' }}>
                  Latest evaluation path: {evaluated[0].resolution.decision.replace(/_/g, ' ')}
                </div>
              )}
              <div style={{ fontSize: '11px', color: '#77798b' }}>
                {job.updated_at ? formatAbsoluteWithRelative(job.updated_at) : '—'}
              </div>
            </div>
          );
        }) : (
          <div style={{ fontSize: '12px', color: '#666' }}>No recent bootstrap resolution records.</div>
        )}
      </DashboardPanel>

      <DashboardPanel title="CONTEXT">
        <div style={{ fontSize: '12px', color: '#a9aaba' }}>
          Loaded files: {loadedContext?.files?.length || 0} · budget {loadedContext?.budget_tokens || 0} tokens · recent load volume {dashboard?.context_pressure?.recent_estimated_tokens || 0}t
        </div>
        {(dashboard?.context_pressure?.top_artifacts?.slice(0, 6) || []).map((artifact) => (
          <div key={`${artifact.artifact_type}-${artifact.identifier}`} style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 1.2fr) 100px 110px 90px', gap: '12px', fontSize: '12px', alignItems: 'center' }}>
            <span style={{ color: '#8d8ea1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {artifact.artifact_type} · {artifact.identifier}
            </span>
            <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
              {artifact.token_share_pct}% total
            </span>
            <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
              {artifact.recent_token_share_pct}% recent
            </span>
            <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
              {artifact.peak_sigma ? `${artifact.peak_sigma}σ` : '—'}
            </span>
          </div>
        ))}
      </DashboardPanel>

      <DashboardPanel title="SPEC LINEAGE">
        {(dashboard?.spec_governance?.recent_proposals?.slice(0, 6) || []).map((proposal) => (
          <div key={proposal.proposal_id} style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', fontSize: '12px' }}>
            <span style={{ color: '#8d8ea1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {proposal.scope} · {proposal.summary || proposal.proposal_id}
            </span>
            <span style={{ color: proposal.status === 'approved' ? '#00f294' : proposal.status === 'needs_clarification' ? '#ffb84d' : '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
              {proposal.status}
            </span>
          </div>
        ))}
      </DashboardPanel>

      <DashboardPanel title="OPERATOR SURFACES">
        <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr', gap: '10px', fontSize: '12px', alignItems: 'start' }}>
          <span style={{ color: '#8d8ea1' }}>Constitution</span>
          <span style={{ color: '#e7e8ef' }}>
            {specsSnapshot?.constitution ? 'Loaded in durable state' : 'Unavailable'}
          </span>
          <span style={{ color: '#8d8ea1' }}>Project spec</span>
          <span style={{ color: '#e7e8ef' }}>
            {specsSnapshot?.project_spec ? 'Loaded in durable state' : 'Unavailable'}
          </span>
          <span style={{ color: '#8d8ea1' }}>Spec proposals</span>
          <span style={{ color: '#e7e8ef' }}>
            {specProposalSnapshot.length ? `${specProposalSnapshot.length} visible proposal records` : 'No recent proposal records'}
          </span>
          <span style={{ color: '#8d8ea1' }}>Knowledge pages</span>
          <span style={{ color: '#e7e8ef' }}>
            {knowledgePagesSnapshot.length ? `${knowledgePagesSnapshot.length} recent knowledge pages visible` : 'No recent knowledge pages visible'}
          </span>
          <span style={{ color: '#8d8ea1' }}>Retention</span>
          <span style={{ color: '#e7e8ef' }}>
            {retentionSnapshot?.runtime?.last_run_at ? `Last run ${formatAbsoluteWithRelative(retentionSnapshot.runtime.last_run_at)}` : 'No retention runtime snapshot'}
          </span>
          <span style={{ color: '#8d8ea1' }}>Worker controls</span>
          <span style={{ color: '#e7e8ef' }}>
            Visible in header: pause, resume, stop, reboot, routing, settings
          </span>
        </div>
      </DashboardPanel>

      <DashboardPanel title="OPERATIONS">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
          {[
            ['Queue bootstrap', onQueueBootstrap],
            ['Queue sample tick', onQueueSampleTick],
            ['Run retention', onRunRetention],
            ['Compact knowledge', onCompactKnowledge],
            ['Scan context', onContextScan],
          ].map(([label, action]) => (
            <button
              key={label}
              onClick={action}
              style={{
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(255,255,255,0.08)',
                color: '#e7e8ef',
                borderRadius: '999px',
                padding: '7px 12px',
                fontSize: '11px',
                fontWeight: 700,
                cursor: 'pointer'
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <div style={{ fontSize: '12px', color: operatorNotice ? '#c7c8d6' : '#666' }}>
          {operatorNotice || 'Operator actions are now available from the dashboard instead of remaining backend-only.'}
        </div>
      </DashboardPanel>

      <DashboardPanel title="SPEC REVIEW">
        {(specProposalSnapshot || []).slice(0, 6).map((proposal) => (
          <div key={proposal.proposal_id} style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '10px 12px', borderRadius: '10px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.05)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
              <span style={{ color: '#e7e8ef', fontSize: '12px', fontWeight: 600 }}>{proposal.scope} · {proposal.status}</span>
              <span style={{ color: '#8d8ea1', fontSize: '11px' }}>{proposal.updated_at ? formatAbsoluteTime(proposal.updated_at) : '—'}</span>
            </div>
            <div style={{ color: '#a9aaba', fontSize: '12px', lineHeight: 1.5 }}>
              {proposal.summary || proposal.proposed_change || proposal.proposal_id}
            </div>
            {proposal.status !== 'approved' && proposal.status !== 'rejected' && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {[
                  ['Approve', 'approved'],
                  ['Reject', 'rejected'],
                  ['Ask clarification', 'needs_clarification'],
                ].map(([label, resolution]) => (
                  <button
                    key={label}
                    onClick={() => onResolveSpecProposal(proposal, resolution)}
                    style={{
                      background: 'rgba(255,255,255,0.04)',
                      border: '1px solid rgba(255,255,255,0.08)',
                      color: '#e7e8ef',
                      borderRadius: '999px',
                      padding: '6px 10px',
                      fontSize: '11px',
                      cursor: 'pointer'
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
        {!specProposalSnapshot.length && <div style={{ fontSize: '12px', color: '#666' }}>No recent spec proposals.</div>}
      </DashboardPanel>

      <DashboardPanel title="RECENT KNOWLEDGE">
        {(knowledgePagesSnapshot || []).slice(0, 6).map((page) => (
          <div key={page.slug} style={{ display: 'grid', gridTemplateColumns: 'minmax(160px, 1.1fr) 100px 120px', gap: '12px', fontSize: '12px', alignItems: 'center' }}>
            <span style={{ color: '#8d8ea1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {page.title || page.slug}
            </span>
            <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
              {page.domain || 'project'}
            </span>
            <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
              {page.updated_at ? formatAbsoluteTime(page.updated_at) : '—'}
            </span>
          </div>
        ))}
        {!knowledgePagesSnapshot.length && <div style={{ fontSize: '12px', color: '#666' }}>No recent knowledge pages.</div>}
      </DashboardPanel>

      <DashboardPanel title="RETENTION">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
          <TelemetryCell value={retentionSnapshot?.policy?.cooldown_minutes ?? '—'} label="COOLDOWN" />
          <TelemetryCell value={retentionSnapshot?.policy?.message_keep_per_session ?? '—'} label="MSG KEEP" />
          <TelemetryCell value={retentionSnapshot?.runtime?.last_summary?.metrics?.archived_metrics ?? '—'} label="ARCH METRICS" />
          <TelemetryCell value={retentionSnapshot?.runtime?.last_summary?.attempts?.archived_attempts ?? '—'} label="ARCH ATTEMPTS" />
        </div>
        <div style={{ fontSize: '12px', color: '#a9aaba' }}>
          {retentionSnapshot?.runtime?.last_run_at
            ? `Last retention run ${formatAbsoluteWithRelative(retentionSnapshot.runtime.last_run_at)}`
            : 'No retention run recorded'}
        </div>
      </DashboardPanel>

      <DashboardPanel title="VARIANT RATINGS">
        {primaryDomainRatings.map(([variantId, rating]) => (
          <div key={variantId} style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 1fr) 90px 80px', gap: '12px', fontSize: '12px', alignItems: 'center' }}>
            <span style={{ color: '#8d8ea1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{variantId}</span>
            <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>{Math.round(rating.rating || 0)}</span>
            <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>{rating.matches || 0} m</span>
          </div>
        ))}
        {!primaryDomainRatings.length && <div style={{ fontSize: '12px', color: '#666' }}>No variant ratings loaded.</div>}
      </DashboardPanel>

      <DashboardPanel title="PREDICTION TRUST">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
          <TelemetryCell value={strongTrust ? strongTrust.trust.toFixed(3) : '—'} label="TRAINER TRUST" />
          <TelemetryCell value={strongTrust?.count ?? '—'} label="JUDGMENTS" />
          <TelemetryCell value={Object.keys(predictionTrustSnapshot?.by_failure_family || {}).length || '—'} label="FAIL FAMILIES" />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {Object.entries(predictionTrustSnapshot?.by_failure_family || {}).slice(0, 5).map(([family, stats]) => (
            <div key={family} style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 1fr) 90px 80px', gap: '12px', fontSize: '12px', alignItems: 'center' }}>
              <span style={{ color: '#8d8ea1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{family}</span>
              <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>{Number(stats.trust || 0).toFixed(3)}</span>
              <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>{stats.count || 0} n</span>
            </div>
          ))}
        </div>
      </DashboardPanel>
    </div>
  );
};

export default function NonChatContent({
  activeNav,
  dashboardProps,
  knowledgeProps,
  tasksProps,
}) {
  if (activeNav === 'dashboard') {
    return <DashboardView {...dashboardProps} />;
  }
  if (activeNav === 'knowledge') {
    return <KnowledgeView {...knowledgeProps} />;
  }
  if (activeNav === 'tasks') {
    return <TasksView {...tasksProps} />;
  }
  return null;
}
