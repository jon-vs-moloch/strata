import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Activity, BookOpen, GitBranch, Pencil, Plus, Play, Wrench } from 'lucide-react';
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

const stringifyJson = (value) => {
  try {
    return JSON.stringify(value, null, 2);
  } catch (err) {
    return String(value);
  }
};

const stripInlineMarkdown = (content) => {
  const raw = String(content || '').trim();
  if (!raw) return '';
  return raw
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/\*(.*?)\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/\n+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
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

const HistoryEventCard = ({ event, defaultExpanded = false, onOpenTask, onOpenProcedure, onOpenSession, onOpenWorkbench }) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const actionButtons = [];
  if (event.taskId && onOpenTask) {
    actionButtons.push(
      <button
        key="task"
        type="button"
        onClick={() => onOpenTask(event.taskId)}
        style={{
          background: 'rgba(255,255,255,0.04)',
          border: '1px solid rgba(255,255,255,0.08)',
          color: '#d7d9e6',
          borderRadius: '999px',
          padding: '6px 10px',
          fontSize: '10px',
          fontWeight: 800,
          cursor: 'pointer',
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
        }}
      >
        Open Task
      </button>
    );
  }
  if (event.procedureId && onOpenProcedure) {
    actionButtons.push(
      <button
        key="procedure"
        type="button"
        onClick={() => onOpenProcedure(event.procedureId)}
        style={{
          background: 'rgba(130,87,229,0.14)',
          border: '1px solid rgba(130,87,229,0.28)',
          color: '#ece3ff',
          borderRadius: '999px',
          padding: '6px 10px',
          fontSize: '10px',
          fontWeight: 800,
          cursor: 'pointer',
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
        }}
      >
        Open Procedure
      </button>
    );
  }
  if (event.sessionId && onOpenSession) {
    actionButtons.push(
      <button
        key="session"
        type="button"
        onClick={() => onOpenSession(event.sessionId)}
        style={{
          background: 'rgba(85,149,255,0.14)',
          border: '1px solid rgba(85,149,255,0.24)',
          color: '#e6f0ff',
          borderRadius: '999px',
          padding: '6px 10px',
          fontSize: '10px',
          fontWeight: 800,
          cursor: 'pointer',
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
        }}
      >
        Open Session
      </button>
    );
  }
  if (onOpenWorkbench) {
    actionButtons.push(
      <button
        key="workbench"
        type="button"
        onClick={() => onOpenWorkbench(event)}
        style={{
          background: 'rgba(214,173,113,0.14)',
          border: '1px solid rgba(214,173,113,0.24)',
          color: '#f3ddbf',
          borderRadius: '999px',
          padding: '6px 10px',
          fontSize: '10px',
          fontWeight: 800,
          cursor: 'pointer',
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
        }}
      >
        Open in Workbench
      </button>
    );
  }

  return (
    <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: '14px', padding: '14px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', minWidth: 0 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            <RoutePill label="TYPE" value={event.kind} tone={event.tone || 'neutral'} />
            {event.scope ? <RoutePill label="SCOPE" value={event.scope} tone="neutral" /> : null}
            {event.lane ? <RoutePill label="LANE" value={event.lane} tone="neutral" /> : null}
          </div>
          <div style={{ color: '#edeeef', fontSize: '14px', fontWeight: 700, lineHeight: 1.4 }}>
            {event.title}
          </div>
          {event.summary ? (
            <div style={{ color: '#9a9caf', fontSize: '12px', lineHeight: 1.7 }}>
              {event.summary}
            </div>
          ) : null}
        </div>
        <div style={{ flexShrink: 0, color: '#7f8091', fontSize: '11px', fontFamily: "'JetBrains Mono', monospace" }}>
          {formatAbsoluteWithRelative(event.at)}
        </div>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
          {actionButtons}
        </div>
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          style={{
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: '10px',
            color: '#d7d9e6',
            fontSize: '10px',
            fontWeight: 800,
            letterSpacing: '0.06em',
            padding: '6px 10px',
            cursor: 'pointer',
            textTransform: 'uppercase',
          }}
        >
          {expanded ? 'Hide' : 'Show'} Details
        </button>
      </div>
      {expanded ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {event.detail ? (
            <div style={{ color: '#c9cad8', fontSize: '12px', lineHeight: 1.7 }}>
              {event.detail}
            </div>
          ) : null}
          {event.metadata ? (
            <pre style={{ margin: 0, padding: '12px', borderRadius: '12px', background: '#0f1014', border: '1px solid rgba(255,255,255,0.06)', color: '#b8bbca', fontSize: '11px', lineHeight: 1.6, overflowX: 'auto', fontFamily: "'JetBrains Mono', monospace" }}>
              {stringifyJson(event.metadata)}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};

const flattenTaskTree = (tasks = []) => {
  const out = [];
  const visit = (task, depth = 0) => {
    if (!task) return;
    out.push({ ...task, __depth: depth });
    (Array.isArray(task.children) ? task.children : []).forEach((child) => visit(child, depth + 1));
  };
  tasks.forEach((task) => visit(task, 0));
  return out;
};

const buildHistoryEvents = ({
  currentScope,
  activeTasks,
  finishedTasks,
  messages,
  procedures,
  specProposals,
  evalJobs,
  laneDetails,
}) => {
  const scopeLabel = currentScope === 'home' ? 'global' : currentScope;
  const events = [];

  flattenTaskTree([...(activeTasks || []), ...(finishedTasks || [])]).forEach((task) => {
    events.push({
      id: `task-${task.id}`,
      at: task.updated_at || task.created_at,
      kind: 'task',
      tone: ['working', 'complete'].includes(String(task.status || '').toLowerCase()) ? 'success' : ['blocked', 'abandoned', 'cancelled'].includes(String(task.status || '').toLowerCase()) ? 'warning' : 'neutral',
      scope: scopeLabel,
      lane: task.lane || '',
      title: task.title || 'Untitled task',
      summary: task.description || '',
      detail: `Task status: ${task.status || 'unknown'}${task.parent_id ? ` · spawned from ${task.parent_id}` : ''}`,
      metadata: {
        task_id: task.id,
        status: task.status,
        lane: task.lane,
        parent_id: task.parent_id,
        depth: task.__depth,
        created_at: task.created_at,
        updated_at: task.updated_at,
        pending_question: task.pending_question || null,
      },
      taskId: task.id,
    });
    (Array.isArray(task.attempts) ? task.attempts : []).forEach((attempt, index) => {
      events.push({
        id: `attempt-${attempt.id || `${task.id}-${index}`}`,
        at: attempt.ended_at || attempt.started_at || task.updated_at || task.created_at,
        kind: 'attempt',
        tone: String(attempt.outcome || '').toLowerCase() === 'succeeded' ? 'success' : String(attempt.outcome || '').toLowerCase() === 'failed' ? 'danger' : 'neutral',
        scope: scopeLabel,
        lane: task.lane || '',
        title: `${task.title || 'Task'} · Attempt ${attempt.label || index + 1}`,
        summary: attempt.reason || attempt.outcome || 'Attempt metadata available',
        detail: attempt.reason || '',
        metadata: {
          attempt_id: attempt.id,
          outcome: attempt.outcome,
          started_at: attempt.started_at,
          ended_at: attempt.ended_at,
          resolution: attempt.resolution,
          reason: attempt.reason,
          artifacts: attempt.artifacts || null,
          evidence: attempt.evidence || null,
          plan_review: attempt.plan_review || null,
        },
        taskId: task.id,
      });
    });
  });

  (Array.isArray(messages) ? messages : []).forEach((message, index) => {
    const metadata = message?.message_metadata || {};
    events.push({
      id: `message-${message.id || index}`,
      at: message.created_at,
      kind: 'message',
      tone: message.role === 'user' ? 'neutral' : 'info',
      scope: scopeLabel,
      lane: metadata.lane || '',
      title: `${String(message.role || 'message').toUpperCase()} · ${stripInlineMarkdown(message.content || '').slice(0, 72) || 'Message'}`,
      summary: stripInlineMarkdown(message.content || '').slice(0, 180),
      detail: stripInlineMarkdown(message.content || ''),
      metadata: {
        message_id: message.id,
        role: message.role,
        session_id: message.session_id,
        created_at: message.created_at,
        message_metadata: metadata,
      },
      sessionId: message.session_id,
    });
  });

  (Array.isArray(procedures) ? procedures : []).forEach((procedure) => {
    const normalized = normalizeProcedureRecord(procedure);
    if (!normalized) return;
    events.push({
      id: `procedure-${normalized.procedure_id}`,
      at: normalized?.stats?.last_run_at || normalized?.stats?.tested_at,
      kind: 'procedure',
      tone: procedureLifecycleTone(normalized.lifecycle_state),
      scope: scopeLabel,
      lane: '',
      title: normalized.title,
      summary: normalized.summary || `Procedure is ${normalized.lifecycle_state}.`,
      detail: normalized.description || '',
      metadata: normalized,
      procedureId: normalized.procedure_id,
    });
  });

  (Array.isArray(specProposals) ? specProposals : []).forEach((proposal) => {
    events.push({
      id: `spec-${proposal.proposal_id}`,
      at: proposal.updated_at || proposal.created_at,
      kind: 'spec proposal',
      tone: String(proposal.status || '').toLowerCase().includes('clarification') ? 'warning' : proposal.status === 'approved' ? 'success' : 'neutral',
      scope: scopeLabel,
      lane: '',
      title: `${proposal.scope || 'spec'} · ${proposal.status || 'pending'}`,
      summary: proposal.summary || proposal.proposed_change || proposal.proposal_id,
      detail: proposal.proposed_change || '',
      metadata: proposal,
    });
  });

  (Array.isArray(evalJobs) ? evalJobs : []).forEach((job) => {
    events.push({
      id: `eval-${job.task_id}`,
      at: job.updated_at || job.created_at,
      kind: 'eval job',
      tone: String(job.state || '').toLowerCase() === 'working' ? 'info' : String(job.state || '').toLowerCase() === 'complete' ? 'success' : 'neutral',
      scope: scopeLabel,
      lane: 'trainer',
      title: job.title || 'Eval job',
      summary: job.system_job?.kind || '',
      detail: job.system_job_result?.result ? 'Structured result attached.' : '',
      metadata: job,
      taskId: job.task_id,
    });
  });

  Object.entries(laneDetails || {}).forEach(([lane, detail]) => {
    (detail?.recent_steps || []).forEach((step, index) => {
      events.push({
        id: `lane-${lane}-${step.at || index}-${step.step || 'step'}`,
        at: step.at,
        kind: 'lane step',
        tone: lane === 'trainer' ? 'warning' : 'neutral',
        scope: currentScope === 'home' ? 'global' : lane,
        lane,
        title: `${lane.toUpperCase()} · ${step.label || 'Step update'}`,
        summary: step.detail || '',
        detail: step.detail || '',
        metadata: {
          lane,
          ...detail,
          step,
        },
      });
    });
  });

  return events
    .filter((event) => Boolean(event.at))
    .sort((a, b) => String(b.at || '').localeCompare(String(a.at || '')));
};

const HistoryView = ({
  currentScope,
  activeTasks,
  finishedTasks,
  messages,
  procedures,
  specProposals,
  evalJobs,
  laneDetails,
  onOpenTask,
  onOpenProcedure,
  onOpenSession,
  onOpenWorkbench,
}) => {
  const events = buildHistoryEvents({
    currentScope,
    activeTasks,
    finishedTasks,
    messages,
    procedures,
    specProposals,
    evalJobs,
    laneDetails,
  });
  const [selectedKind, setSelectedKind] = useState('all');
  const filteredEvents = selectedKind === 'all'
    ? events
    : events.filter((event) => event.kind === selectedKind);
  const buckets = [
    ['all', 'All'],
    ['task', 'Tasks'],
    ['attempt', 'Attempts'],
    ['lane step', 'Lane'],
    ['message', 'Messages'],
    ['procedure', 'Procedures'],
    ['eval job', 'Eval'],
  ];

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '18px' }}>
      <DashboardPanel title="HISTORY">
        <div style={{ color: '#8d8ea1', fontSize: '13px', lineHeight: 1.7 }}>
          History is the scoped event log for runtime work. It is meant to support autopsies, trace inspection, and operator intervention by showing what happened in chronological order with expandable metadata.
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
          {buckets.map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setSelectedKind(id)}
              style={{
                background: selectedKind === id ? 'rgba(130,87,229,0.16)' : 'rgba(255,255,255,0.04)',
                border: selectedKind === id ? '1px solid rgba(130,87,229,0.28)' : '1px solid rgba(255,255,255,0.08)',
                color: selectedKind === id ? '#ede4ff' : '#d7d9e6',
                borderRadius: '999px',
                padding: '7px 12px',
                fontSize: '11px',
                fontWeight: 800,
                cursor: 'pointer',
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </DashboardPanel>

      {filteredEvents.length ? (
        filteredEvents.slice(0, 120).map((event, index) => (
          <HistoryEventCard
            key={event.id}
            event={event}
            defaultExpanded={index < 2}
            onOpenTask={onOpenTask}
            onOpenProcedure={onOpenProcedure}
            onOpenSession={onOpenSession}
            onOpenWorkbench={onOpenWorkbench}
          />
        ))
      ) : (
        <DashboardPanel title="NO EVENTS">
          <div style={{ color: '#8d8ea1', fontSize: '13px', lineHeight: 1.7 }}>
            No history events are available for this scope and filter yet.
          </div>
        </DashboardPanel>
      )}
    </div>
  );
};

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

const TaskSection = ({ title, subtitle = '', tasks, onArchiveTask, nowMs, laneDetails, emptyLabel, defaultOpen = true }) => {
  const [expanded, setExpanded] = useState(defaultOpen);
  return (
    <DashboardPanel title={title}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
        <div style={{ fontSize: '13px', color: '#8d8ea1', lineHeight: 1.7 }}>
          {subtitle}
        </div>
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
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
          {expanded ? 'Hide' : 'Show'} {tasks.length}
        </button>
      </div>
      {expanded ? (
        tasks.length ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {tasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                onArchive={() => onArchiveTask(task.id)}
                nowMs={nowMs}
                laneDetail={laneDetails?.[task.lane] || null}
                detailLevel="full"
              />
            ))}
          </div>
        ) : (
          <div style={{ fontSize: '13px', color: '#8d8ea1', lineHeight: 1.7 }}>
            {emptyLabel}
          </div>
        )
      ) : null}
    </DashboardPanel>
  );
};

const TasksView = ({
  currentScope,
  activeTasks,
  queuedTasks,
  finishedTasks,
  workerStatus,
  laneStatuses,
  laneDetails,
  laneCurrentTaskTitles,
  scopeOperationalMetrics,
  scopeAttemptMetrics,
  onArchiveTask,
  nowMs,
}) => {
  const scopeLabel = currentScope === 'home' ? 'Global' : currentScope === 'trainer' ? 'Trainer' : 'Agent';
  const scopeLaneDetail = currentScope === 'home' ? null : (laneDetails?.[currentScope] || null);
  const scopeHealth = currentScope === 'home'
    ? `trainer ${String(laneDetails?.trainer?.activity_label || 'Idle').toLowerCase()} · agent ${String(laneDetails?.agent?.activity_label || 'Idle').toLowerCase()}`
    : (scopeLaneDetail?.activity_label || laneStatuses?.[currentScope] || 'IDLE');
  const scopeHeartbeat = currentScope === 'home'
    ? 'shared runtime'
    : scopeLaneDetail?.heartbeat_age_s == null
    ? (scopeLaneDetail?.activity_mode === 'GENERATING' ? 'starting' : 'no heartbeat')
    : `${scopeLaneDetail?.heartbeat_state || 'unknown'} · ${Math.round(Number(scopeLaneDetail?.heartbeat_age_s || 0))}s ago`;
  const scopeCurrentTask = currentScope === 'home'
    ? (laneCurrentTaskTitles?.agent || laneCurrentTaskTitles?.trainer || 'no active task')
    : (laneCurrentTaskTitles?.[currentScope] || 'no active task');

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '18px' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.8fr) minmax(280px, 0.95fr)', gap: '18px', alignItems: 'start' }}>
        <DashboardPanel title="TASK QUEUE">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            <RoutePill label="SCOPE" value={scopeLabel} tone="neutral" />
            <RoutePill label="MODE" value={scopeHealth} tone={String(scopeHealth).toUpperCase().includes('GENERATING') ? 'success' : String(scopeHealth).toUpperCase().includes('BLOCKED') || String(scopeHealth).toUpperCase().includes('STALLED') ? 'warning' : 'neutral'} />
            <RoutePill label="ACTIVE" value={String(activeTasks.length)} tone="success" />
            <RoutePill label="QUEUED" value={String((queuedTasks || []).length)} tone="warning" />
            <RoutePill label="RECENT" value={String(finishedTasks.length)} tone="neutral" />
            <RoutePill label="HEARTBEAT" value={scopeHeartbeat} tone="neutral" />
          </div>
          <div style={{ color: '#8d8ea1', fontSize: '13px', lineHeight: 1.7 }}>
            This is the canonical task surface: active work stays visible here, recent completions remain easy to inspect, and the lane-specific task rail can stay focused on chat context. Current task: {scopeCurrentTask}.
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

      <TaskSection
        title="PRESENT TASKS"
        subtitle="Live work stays visible here with its nested attempts and child work."
        tasks={activeTasks}
        onArchiveTask={onArchiveTask}
        nowMs={nowMs}
        laneDetails={laneDetails}
        emptyLabel="No active tasks in this scope right now."
      />

      <TaskSection
        title="QUEUED / PENDING"
        subtitle="Runnable backlog lives here so operator triage does not depend on the chat rail."
        tasks={queuedTasks || []}
        onArchiveTask={onArchiveTask}
        nowMs={nowMs}
        laneDetails={laneDetails}
        emptyLabel="No queued tasks in this scope right now."
        defaultOpen={false}
      />

      <TaskSection
        title="RECENTLY COMPLETED"
        subtitle="Finished, abandoned, and cancelled work stays here until it ages into archival surfaces."
        tasks={finishedTasks}
        onArchiveTask={onArchiveTask}
        nowMs={nowMs}
        laneDetails={laneDetails}
        emptyLabel="No recent finished tasks yet."
        defaultOpen={false}
      />
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
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'flex-end', gap: '12px' }}>
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

const ProcedureBadge = ({ label, value, tone = 'neutral' }) => {
  const tones = {
    neutral: { bg: 'rgba(255,255,255,0.04)', border: 'rgba(255,255,255,0.08)', text: '#c7c8d6' },
    success: { bg: 'rgba(0,242,148,0.08)', border: 'rgba(0,242,148,0.18)', text: '#9df7d0' },
    warning: { bg: 'rgba(255,184,77,0.08)', border: 'rgba(255,184,77,0.18)', text: '#ffd39b' },
    danger: { bg: 'rgba(255,92,92,0.08)', border: 'rgba(255,92,92,0.18)', text: '#ffb3b3' },
    info: { bg: 'rgba(85,149,255,0.08)', border: 'rgba(85,149,255,0.18)', text: '#b9d5ff' },
  };
  const theme = tones[tone] || tones.neutral;
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '6px 10px', borderRadius: '999px', background: theme.bg, border: `1px solid ${theme.border}`, minWidth: 0 }}>
      <span style={{ fontSize: '10px', letterSpacing: '0.08em', color: '#6f7183', fontWeight: 800 }}>{label}</span>
      <span style={{ fontSize: '11px', color: theme.text, fontFamily: "'JetBrains Mono', monospace", whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{value}</span>
    </div>
  );
};

const procedureLifecycleTone = (state) => {
  switch (String(state || '').toLowerCase()) {
    case 'vetted':
      return 'success';
    case 'tested':
      return 'info';
    case 'draft':
      return 'warning';
    case 'retired':
      return 'danger';
    default:
      return 'neutral';
  }
};

const groupProceduresByLifecycle = (procedures = []) => {
  const groups = {
    draft: [],
    tested: [],
    vetted: [],
    retired: [],
  };
  procedures.forEach((procedure) => {
    const key = String(procedure?.lifecycle_state || 'draft').toLowerCase();
    if (!groups[key]) groups[key] = [];
    groups[key].push(procedure);
  });
  Object.values(groups).forEach((items) => {
    items.sort((a, b) => String(b?.stats?.last_run_at || b?.stats?.tested_at || '').localeCompare(String(a?.stats?.last_run_at || a?.stats?.tested_at || '')));
  });
  return groups;
};

const normalizeProcedureChecklistItem = (item, index = 0) => {
  if (typeof item === 'string') {
    const title = item.trim();
    return {
      id: `step_${index + 1}`,
      title: title || `Step ${index + 1}`,
      verification: '',
    };
  }
  if (item && typeof item === 'object') {
    return {
      id: String(item.id || `step_${index + 1}`),
      title: String(item.title || item.label || item.name || `Step ${index + 1}`),
      verification: String(item.verification || item.description || ''),
    };
  }
  return {
    id: `step_${index + 1}`,
    title: `Step ${index + 1}`,
    verification: '',
  };
};

const normalizeProcedureRecord = (procedure) => {
  if (!procedure || typeof procedure !== 'object') return null;
  return {
    ...procedure,
    procedure_id: String(procedure.procedure_id || ''),
    title: String(procedure.title || procedure.procedure_id || 'Untitled Procedure'),
    summary: String(procedure.summary || ''),
    description: typeof procedure.description === 'string' ? procedure.description : '',
    lifecycle_state: String(procedure.lifecycle_state || 'draft').toLowerCase(),
    lineage_id: procedure.lineage_id ? String(procedure.lineage_id) : null,
    variant_of: procedure.variant_of ? String(procedure.variant_of) : null,
    mutable: Boolean(procedure.mutable),
    stats: procedure.stats && typeof procedure.stats === 'object' ? procedure.stats : {},
    checklist: Array.isArray(procedure.checklist)
      ? procedure.checklist.map((item, index) => normalizeProcedureChecklistItem(item, index))
      : [],
  };
};

const ProceduresView = ({
  procedures,
  selectedProcedure,
  selectedProcedureId,
  onSelectProcedure,
  onQueueProcedure,
  onOpenWorkbench,
}) => {
  const safeProcedures = Array.isArray(procedures)
    ? procedures.map((procedure) => normalizeProcedureRecord(procedure)).filter(Boolean)
    : [];
  const safeSelectedProcedure = normalizeProcedureRecord(selectedProcedure);
  const groupedProcedures = groupProceduresByLifecycle(safeProcedures);
  const sections = [
    { id: 'draft', label: 'Draft Procedures', subtitle: 'Novel or still-being-learned workflows.' },
    { id: 'tested', label: 'Tested Procedures', subtitle: 'Completed successfully at least once, still evolvable.' },
    { id: 'vetted', label: 'Vetted Procedures', subtitle: 'Stable operator-facing workflows.' },
    { id: 'retired', label: 'Retired Procedures', subtitle: 'Historical or superseded workflows kept for lineage.' },
  ];

  return (
    <div style={{ flex: 1, overflow: 'hidden', display: 'grid', gridTemplateColumns: '340px minmax(0, 1fr)', gap: '0', minHeight: 0 }}>
      <div style={{ borderRight: '1px solid rgba(255,255,255,0.05)', background: '#0d0d11', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div style={{ padding: '20px', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <div style={{ fontSize: '11px', color: '#7f8091', letterSpacing: '0.12em', fontWeight: 800 }}>PROCEDURES</div>
          <div style={{ fontSize: '12px', color: '#8d8ea1', lineHeight: 1.6 }}>
            Strata is always executing a Procedure. This surface makes the draft-to-vetted lifecycle inspectable and queueable.
          </div>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: '10px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {sections.map((section) => {
            const items = groupedProcedures[section.id] || [];
            return (
              <div key={section.id} style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <div style={{ padding: '8px 10px 0' }}>
                  <div style={{ fontSize: '11px', fontWeight: 800, letterSpacing: '0.1em', color: '#7f8091', textTransform: 'uppercase' }}>
                    {section.label} · {items.length}
                  </div>
                  <div style={{ marginTop: '4px', fontSize: '12px', color: '#666a78', lineHeight: 1.5 }}>
                    {section.subtitle}
                  </div>
                </div>
                {items.length ? items.map((procedure) => {
                  const active = procedure.procedure_id === selectedProcedureId;
                  const checklist = Array.isArray(procedure.checklist) ? procedure.checklist : [];
                  return (
                    <button
                      key={procedure.procedure_id}
                      type="button"
                      onClick={() => onSelectProcedure(procedure.procedure_id)}
                      style={{
                        width: '100%',
                        textAlign: 'left',
                        background: active ? 'rgba(130,87,229,0.14)' : 'transparent',
                        border: active ? '1px solid rgba(130,87,229,0.28)' : '1px solid rgba(255,255,255,0.05)',
                        borderRadius: '12px',
                        padding: '12px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '8px',
                        cursor: 'pointer',
                      }}
                    >
                      <div style={{ color: active ? '#f2ecff' : '#edeeef', fontSize: '13px', fontWeight: 700 }}>
                        {procedure.title || procedure.procedure_id}
                      </div>
                      <div style={{ color: '#8d8ea1', fontSize: '12px', lineHeight: 1.5 }}>
                        {procedure.summary || 'No summary available yet.'}
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                        <ProcedureBadge label="STATE" value={procedure.lifecycle_state || 'draft'} tone={procedureLifecycleTone(procedure.lifecycle_state)} />
                        <ProcedureBadge label="STEPS" value={String(checklist.length)} tone="neutral" />
                        <ProcedureBadge label="RUNS" value={String(procedure?.stats?.run_count || 0)} tone="neutral" />
                      </div>
                    </button>
                  );
                }) : (
                  <div style={{ padding: '0 10px 10px', fontSize: '12px', color: '#666a78', lineHeight: 1.6 }}>
                    No procedures in this lifecycle bucket yet.
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div style={{ minWidth: 0, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '18px' }}>
        {safeSelectedProcedure ? (
          <>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px', flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', minWidth: 0 }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  <ProcedureBadge label="STATE" value={safeSelectedProcedure.lifecycle_state || 'draft'} tone={procedureLifecycleTone(safeSelectedProcedure.lifecycle_state)} />
                  <ProcedureBadge label="ID" value={safeSelectedProcedure.procedure_id || '—'} tone="neutral" />
                  <ProcedureBadge label="LINEAGE" value={safeSelectedProcedure.lineage_id || '—'} tone="neutral" />
                  <ProcedureBadge label="VARIANT OF" value={safeSelectedProcedure.variant_of || '—'} tone="neutral" />
                </div>
                <div>
                  <h2 style={{ margin: 0, color: '#edeeef', fontSize: '28px', lineHeight: 1.1 }}>{safeSelectedProcedure.title || safeSelectedProcedure.procedure_id}</h2>
                  <div style={{ marginTop: '10px', color: '#a9aaba', fontSize: '14px', lineHeight: 1.6 }}>
                    {safeSelectedProcedure.summary || 'No summary available for this Procedure yet.'}
                  </div>
                </div>
              </div>
              <button
                type="button"
                onClick={() => onQueueProcedure(safeSelectedProcedure.procedure_id)}
                style={{
                  background: 'rgba(85,149,255,0.16)',
                  border: '1px solid rgba(85,149,255,0.28)',
                  color: '#e6f0ff',
                  borderRadius: '12px',
                  padding: '10px 14px',
                  fontSize: '12px',
                  fontWeight: 800,
                  cursor: 'pointer',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '8px',
                }}
              >
                <Play size={14} />
                Queue Procedure
              </button>
              {onOpenWorkbench ? (
                <button
                  type="button"
                  onClick={() => onOpenWorkbench({
                    kind: 'procedure',
                    procedureId: safeSelectedProcedure.procedure_id,
                    title: safeSelectedProcedure.title,
                    summary: safeSelectedProcedure.summary,
                    metadata: safeSelectedProcedure,
                  })}
                  style={{
                    background: 'rgba(214,173,113,0.14)',
                    border: '1px solid rgba(214,173,113,0.24)',
                    color: '#f3ddbf',
                    borderRadius: '12px',
                    padding: '10px 14px',
                    fontSize: '12px',
                    fontWeight: 800,
                    cursor: 'pointer',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '8px',
                  }}
                >
                  <Wrench size={14} />
                  Open in Workbench
                </button>
              ) : null}
            </div>

            <DashboardPanel title="PROCEDURE METADATA">
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: '12px' }}>
                <TelemetryCell value={safeSelectedProcedure?.stats?.run_count ?? '—'} label="RUNS" />
                <TelemetryCell value={safeSelectedProcedure?.stats?.success_count ?? '—'} label="SUCCESSES" />
                <TelemetryCell value={safeSelectedProcedure?.stats?.failure_count ?? '—'} label="FAILURES" />
                <TelemetryCell value={safeSelectedProcedure.mutable ? 'yes' : 'no'} label="MUTABLE" />
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                <ProcedureBadge label="TESTED AT" value={formatAbsoluteWithRelative(safeSelectedProcedure?.stats?.tested_at)} tone="neutral" />
                <ProcedureBadge label="LAST RUN" value={formatAbsoluteWithRelative(safeSelectedProcedure?.stats?.last_run_at)} tone="neutral" />
                <ProcedureBadge label="SOURCE TASK" value={safeSelectedProcedure?.stats?.last_source_task_id || '—'} tone="neutral" />
              </div>
            </DashboardPanel>

            <DashboardPanel title="CHECKLIST">
              {Array.isArray(safeSelectedProcedure.checklist) && safeSelectedProcedure.checklist.length ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  {safeSelectedProcedure.checklist.map((item, index) => (
                    <div key={`${safeSelectedProcedure.procedure_id}-step-${item.id || index}`} style={{ display: 'flex', gap: '12px', alignItems: 'flex-start', padding: '10px 0', borderBottom: index === safeSelectedProcedure.checklist.length - 1 ? 'none' : '1px solid rgba(255,255,255,0.05)' }}>
                      <div style={{ width: '24px', height: '24px', borderRadius: '999px', background: 'rgba(255,255,255,0.06)', color: '#c7c8d6', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', fontWeight: 800, flexShrink: 0 }}>
                        {index + 1}
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: 0 }}>
                        <div style={{ color: '#e7e8ef', fontSize: '13px', lineHeight: 1.6 }}>
                          {item.title}
                        </div>
                        {item.verification ? (
                          <div style={{ color: '#8d8ea1', fontSize: '12px', lineHeight: 1.6 }}>
                            {item.verification}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ fontSize: '12px', color: '#666a78', lineHeight: 1.6 }}>
                  No checklist has been recorded for this Procedure yet.
                </div>
              )}
            </DashboardPanel>

            {safeSelectedProcedure.description && (
              <div style={{ background: '#141418', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '16px', padding: '22px' }}>
                <div className="markdown-body" style={{ fontSize: '14px', lineHeight: '1.75', color: '#edeeef' }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {safeSelectedProcedure.description}
                  </ReactMarkdown>
                </div>
              </div>
            )}
          </>
        ) : (
          <div style={{ margin: 'auto 0', padding: '48px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '14px', textAlign: 'center' }}>
            <GitBranch size={34} color="#2f3040" />
            <div style={{ fontSize: '18px', fontWeight: 700, color: '#c7c8d6' }}>Procedure registry is ready</div>
            <div style={{ maxWidth: '620px', fontSize: '14px', lineHeight: 1.7, color: '#8d8ea1' }}>
              Select a Procedure to inspect its lifecycle, lineage, checklist, and run history. Draft Procedures represent live work-in-progress search; tested and vetted Procedures are the more stable descendants of that search.
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

const workbenchActionButtonStyle = {
  background: 'rgba(255,255,255,0.04)',
  border: '1px solid rgba(255,255,255,0.08)',
  color: '#e7e8ef',
  borderRadius: '999px',
  padding: '8px 12px',
  fontSize: '11px',
  fontWeight: 800,
  cursor: 'pointer',
};

const buildWorkbenchPrompt = (action, target, taskMatch, procedureMatch) => {
  const label = target?.taskTitle || target?.title || taskMatch?.title || procedureMatch?.title || 'this target';
  const taskId = target?.taskId || taskMatch?.id || '';
  const procedureId = target?.procedureId || procedureMatch?.procedure_id || '';
  const sessionId = target?.sessionId || '';
  const contextBits = [
    taskId ? `task_id=${taskId}` : '',
    procedureId ? `procedure_id=${procedureId}` : '',
    sessionId ? `session_id=${sessionId}` : '',
  ].filter(Boolean);
  const contextLine = contextBits.length ? `Context: ${contextBits.join(' · ')}.` : '';

  switch (action) {
    case 'explain':
      return `Explain what ${label} is doing, what inputs it depends on, and what downstream state or outputs it affects.\n\n${contextLine}`.trim();
    case 'verify':
      return `Verify ${label}. Tell me whether its current behavior is correct, what evidence supports that, and what should change if it is not.\n\n${contextLine}`.trim();
    case 'audit':
      return `Audit ${label} end-to-end. Focus on hidden risks, observability gaps, regressions, and anything that could make the system lie about its own state.\n\n${contextLine}`.trim();
    case 'fix':
      return `Help fix ${label}. First identify the most likely source of the problem from the current context, then propose and, if appropriate, make the smallest concrete change that moves it forward.\n\n${contextLine}`.trim();
    default:
      return '';
  }
};

const WorkbenchView = ({
  target,
  activeTasks,
  finishedTasks,
  procedures,
  messages,
  onOpenTask,
  onOpenProcedure,
  onOpenSession,
  onSendWorkbenchPrompt,
}) => {
  const [draftPrompt, setDraftPrompt] = useState('');
  const [sendingPrompt, setSendingPrompt] = useState(false);
  const [responseMode, setResponseMode] = useState('thinking');
  const allTasks = flattenTaskTree([...(activeTasks || []), ...(finishedTasks || [])]);
  const taskMatch = target?.taskId ? allTasks.find((task) => String(task.id) === String(target.taskId)) : null;
  const procedureMatch = target?.procedureId
    ? (Array.isArray(procedures) ? procedures.map((procedure) => normalizeProcedureRecord(procedure)).find((procedure) => procedure?.procedure_id === target.procedureId) : null)
    : null;
  const sessionMatch = target?.sessionId
    ? (Array.isArray(messages) ? messages.filter((message) => String(message.session_id) === String(target.sessionId)).slice(-5) : [])
    : [];
  const title = target?.title || taskMatch?.title || procedureMatch?.title || 'Workbench target';
  const summary = target?.summary || taskMatch?.description || procedureMatch?.summary || 'Inspect, replay, and branch this flow from here.';
  const metadata = target?.metadata || taskMatch || procedureMatch || null;
  const hasTarget = Boolean(target || taskMatch || procedureMatch);

  const applyWorkbenchAction = (action) => {
    setDraftPrompt(buildWorkbenchPrompt(action, target, taskMatch, procedureMatch));
  };

  const handleSendPrompt = async () => {
    const normalized = String(draftPrompt || '').trim();
    if (!normalized || !onSendWorkbenchPrompt) return;
    setSendingPrompt(true);
    try {
      await onSendWorkbenchPrompt({
        prompt: normalized,
        responseMode,
        target,
        task: taskMatch,
        procedure: procedureMatch,
      });
      setDraftPrompt('');
    } finally {
      setSendingPrompt(false);
    }
  };

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '18px' }}>
      <DashboardPanel title="WORKBENCH">
        <div style={{ color: '#8d8ea1', fontSize: '13px', lineHeight: 1.7 }}>
          Workbench is the universal debugger surface for Strata. This first slice focuses on target-oriented inspection and cross-linking so you can jump directly from Procedures, History, and runtime artifacts into a dedicated experimentation surface.
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
          <RoutePill label="TARGET" value={String(target?.kind || 'none')} tone="neutral" />
          {target?.taskId ? <RoutePill label="TASK" value={String(target.taskId)} tone="neutral" /> : null}
          {target?.procedureId ? <RoutePill label="PROCEDURE" value={String(target.procedureId)} tone="success" /> : null}
          {target?.sessionId ? <RoutePill label="SESSION" value={String(target.sessionId)} tone="neutral" /> : null}
        </div>
      </DashboardPanel>

      <DashboardPanel title="FOCUSED TARGET">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <div style={{ color: '#edeeef', fontSize: '22px', fontWeight: 700, lineHeight: 1.2 }}>
            {title}
          </div>
          <div style={{ color: '#a9aaba', fontSize: '14px', lineHeight: 1.7 }}>
            {summary}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            {taskMatch && onOpenTask ? (
              <button type="button" onClick={() => onOpenTask(taskMatch.id)} style={workbenchActionButtonStyle}>
                Open Task
              </button>
            ) : null}
            {procedureMatch && onOpenProcedure ? (
              <button type="button" onClick={() => onOpenProcedure(procedureMatch.procedure_id)} style={{ ...workbenchActionButtonStyle, background: 'rgba(130,87,229,0.16)', border: '1px solid rgba(130,87,229,0.28)', color: '#f1e8ff' }}>
                Open Procedure
              </button>
            ) : null}
            {target?.sessionId && onOpenSession ? (
              <button type="button" onClick={() => onOpenSession(target.sessionId)} style={{ ...workbenchActionButtonStyle, background: 'rgba(85,149,255,0.16)', border: '1px solid rgba(85,149,255,0.28)', color: '#e6f0ff' }}>
                Open Session
              </button>
            ) : null}
          </div>
        </div>
      </DashboardPanel>

      <DashboardPanel title="WORKBENCH ACTIONS">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            {[
              ['explain', 'Explain'],
              ['verify', 'Verify'],
              ['audit', 'Audit'],
              ['fix', 'Fix'],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => applyWorkbenchAction(id)}
                disabled={!hasTarget}
                style={{
                  ...workbenchActionButtonStyle,
                  opacity: hasTarget ? 1 : 0.45,
                  cursor: hasTarget ? 'pointer' : 'default',
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
            <div style={{ color: '#8d8ea1', fontSize: '11px', fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
              Response Mode
            </div>
            <div style={{ display: 'inline-flex', borderRadius: '999px', padding: '3px', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
              {[
                ['thinking', 'Thinking'],
                ['instant', 'Instant'],
              ].map(([modeId, label]) => {
                const active = responseMode === modeId;
                return (
                  <button
                    key={modeId}
                    type="button"
                    onClick={() => setResponseMode(modeId)}
                    style={{
                      background: active
                        ? (modeId === 'instant' ? 'rgba(214,173,113,0.22)' : 'rgba(130,87,229,0.22)')
                        : 'transparent',
                      color: active
                        ? (modeId === 'instant' ? '#f3ddbf' : '#dccfff')
                        : '#8f94a7',
                      border: 'none',
                      borderRadius: '999px',
                      padding: '6px 10px',
                      fontSize: '11px',
                      fontWeight: 800,
                      cursor: 'pointer',
                      letterSpacing: '0.04em',
                      textTransform: 'uppercase',
                    }}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
          <div style={{ color: '#8d8ea1', fontSize: '12px', lineHeight: 1.6 }}>
            Use Workbench to generate a scoped prompt for the selected node, then send it into chat with the current target context attached implicitly by session, task, and procedure lineage. Instant mode skips tool use and asks for a direct answer from current context.
          </div>
          <textarea
            value={draftPrompt}
            onChange={(event) => setDraftPrompt(event.target.value)}
            placeholder={hasTarget ? 'Ask Workbench to explain, verify, audit, or fix this target...' : 'Open a task, procedure, or history event in Workbench to begin.'}
            style={{
              minHeight: '120px',
              borderRadius: '14px',
              border: '1px solid rgba(255,255,255,0.08)',
              background: '#101116',
              color: '#ececf2',
              padding: '14px',
              fontSize: '13px',
              lineHeight: 1.6,
              resize: 'vertical',
            }}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
            <div style={{ color: '#7f8091', fontSize: '11px' }}>
              {target?.sessionId
                ? 'This will send through the linked session so follow-up context stays attached.'
                : 'If no session is linked, Workbench sends through the currently selected chat lane.'}
            </div>
            <button
              type="button"
              onClick={() => void handleSendPrompt()}
              disabled={!String(draftPrompt || '').trim() || !onSendWorkbenchPrompt || sendingPrompt}
              style={{
                background: String(draftPrompt || '').trim() && onSendWorkbenchPrompt && !sendingPrompt
                  ? 'linear-gradient(135deg, #d6ad71, #9e6d38)'
                  : 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(214,173,113,0.24)',
                color: String(draftPrompt || '').trim() && onSendWorkbenchPrompt && !sendingPrompt ? '#18120b' : '#6f6a63',
                borderRadius: '999px',
                padding: '10px 14px',
                fontSize: '11px',
                fontWeight: 800,
                cursor: String(draftPrompt || '').trim() && onSendWorkbenchPrompt && !sendingPrompt ? 'pointer' : 'default',
              }}
            >
              {sendingPrompt ? 'Sending…' : 'Send to Chat'}
            </button>
          </div>
        </div>
      </DashboardPanel>

      <DashboardPanel title="TARGET METADATA">
        {metadata ? (
          <pre style={{ margin: 0, padding: '12px', borderRadius: '12px', background: '#0f1014', border: '1px solid rgba(255,255,255,0.06)', color: '#b8bbca', fontSize: '11px', lineHeight: 1.6, overflowX: 'auto', fontFamily: "'JetBrains Mono', monospace" }}>
            {stringifyJson(metadata)}
          </pre>
        ) : (
          <div style={{ color: '#8d8ea1', fontSize: '13px', lineHeight: 1.7 }}>
            No explicit target is selected yet. “Open in Workbench” from History or Procedures to seed this surface with a real node.
          </div>
        )}
      </DashboardPanel>

      <DashboardPanel title="NEXT CAPABILITIES">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', color: '#a9aaba', fontSize: '13px', lineHeight: 1.7 }}>
          <div>1. Warm a process up to a selected node so real inputs, outputs, and handoff context are inspectable.</div>
          <div>2. Replay and regenerate outputs from an intermediate node.</div>
          <div>3. Branch from edited context, tool choices, model choices, or verification policies and compare downstream outcomes.</div>
          <div>4. Drill into verification, audit, and other child processes as first-class subflows.</div>
          <div>5. Reflect all the way down into Strata’s own tools, Procedures, Knowledge artifacts, runtime policy, and UI.</div>
        </div>
      </DashboardPanel>

      {sessionMatch.length ? (
        <DashboardPanel title="RECENT SESSION MESSAGES">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {sessionMatch.map((message, index) => (
              <div key={`${message.id || index}`} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '12px', padding: '12px' }}>
                <div style={{ color: '#8d8ea1', fontSize: '11px', marginBottom: '6px' }}>
                  {String(message.role || 'message').toUpperCase()} · {formatAbsoluteWithRelative(message.created_at)}
                </div>
                <div style={{ color: '#e7e8ef', fontSize: '13px', lineHeight: 1.7 }}>
                  {stripInlineMarkdown(message.content || '') || 'No content'}
                </div>
              </div>
            ))}
          </div>
        </DashboardPanel>
      ) : null}
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
  proceduresProps,
  historyProps,
  workbenchProps,
}) {
  if (activeNav === 'dashboard') {
    return <DashboardView {...dashboardProps} />;
  }
  if (activeNav === 'history') {
    return <HistoryView {...historyProps} />;
  }
  if (activeNav === 'knowledge') {
    return <KnowledgeView {...knowledgeProps} />;
  }
  if (activeNav === 'tasks') {
    return <TasksView {...tasksProps} />;
  }
  if (activeNav === 'procedures') {
    return <ProceduresView {...proceduresProps} />;
  }
  if (activeNav === 'workbench') {
    return <WorkbenchView {...workbenchProps} />;
  }
  return null;
}
