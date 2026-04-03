import React, { useState, useEffect } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Activity, BookOpen, GitBranch, Play, Wrench,
  RotateCcw, History, Route, Zap, Search, MessageCircle, AlertCircle, X, ChevronRight, CheckCircle2
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import TaskCard from '../components/TaskCard';
import { useWorkbenchData } from '../hooks/useWorkbenchData';

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

const isDecompositionPhaseNode = (task) => {
  const constraints = task && typeof task.constraints === 'object' && task.constraints ? task.constraints : {};
  return String(constraints.inline_process_kind || '').trim().toLowerCase() === 'decomposition_phase';
};

const summarizeDecompositionPhase = (task) => {
  const constraints = task && typeof task.constraints === 'object' && task.constraints ? task.constraints : {};
  const summary = constraints.decomposition_summary && typeof constraints.decomposition_summary === 'object'
    ? constraints.decomposition_summary
    : {};
  const phaseId = String(constraints.decomposition_phase_id || '').trim();
  if (phaseId === 'frame_task') {
    return String(summary.problem_statement || task.description || '').trim();
  }
  if (phaseId === 'emit_leaf_tasks') {
    const kept = Number(summary.actionable_subtask_count || 0);
    const rejected = Array.isArray(summary.rejected_subtasks) ? summary.rejected_subtasks.length : 0;
    return `${kept} actionable leaves retained${rejected ? ` · ${rejected} rejected` : ''}`;
  }
  if (phaseId === 'preserve_workflow') {
    const mode = String(summary.preservation_mode || '').trim().replace(/_/g, ' ');
    const spawned = Array.isArray(summary.spawned_recovery_subtasks) ? summary.spawned_recovery_subtasks.length : 0;
    return [mode ? `Mode: ${mode}` : '', spawned ? `${spawned} recovery steps` : ''].filter(Boolean).join(' · ');
  }
  if (phaseId.startsWith('issue_') || phaseId.startsWith('guardrail')) {
    const kind = String(summary.kind || summary.guardrail || '').trim().replace(/_/g, ' ');
    return kind ? `Issue: ${kind}` : 'Decomposition issue';
  }
  return '';
};

const workbenchSimulationSessionId = (target) => {
  if (target?.procedureId) return `workbench:procedure:${target.procedureId}`;
  if (target?.taskId) return `workbench:task:${target.taskId}`;
  return 'workbench:general';
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
  sources,
  query,
  selectedPage,
  selectedSlug,
  selectedSource,
  selectedSourcePath,
  onQueryChange,
  onSelectSlug,
  onSelectSource,
  onCreatePage,
  onEditPage,
  onQueueSource,
}) => {
  const [knowledgeMode, setKnowledgeMode] = useState('wiki');
  const relatedPages = selectedPage?.related_pages || [];
  const classifyWikiPage = (page) => {
    const maintenance = page?.maintenance || {};
    const evidenceStatus = String(maintenance?.evidence_status || '').trim().toLowerCase();
    const provenanceKinds = new Set(
      Array.isArray(page?.provenance)
        ? page.provenance.map((entry) => String(entry?.kind || '').trim().toLowerCase()).filter(Boolean)
        : []
    );
    const sourceCount = Number(page?.source_count || 0);
    if (evidenceStatus === 'maintenance_report') {
      return { label: 'Report', tone: 'warning' };
    }
    if (provenanceKinds.size && [...provenanceKinds].every((kind) => kind === 'durable_doc')) {
      return { label: 'Durable source', tone: 'info' };
    }
    if (provenanceKinds.has('final_research') || provenanceKinds.has('wip_research') || provenanceKinds.has('telemetry') || provenanceKinds.has('misc')) {
      return { label: 'Ingested source', tone: 'warning' };
    }
    if (page?.created_by === 'knowledge_compactor' && sourceCount <= 1) {
      return { label: 'Seeded', tone: 'info' };
    }
    if (sourceCount > 1) {
      return { label: 'Integrated', tone: 'success' };
    }
    return { label: 'Wiki', tone: 'neutral' };
  };
  const formatSourceClass = (value) => {
    const raw = String(value || '').trim();
    if (!raw) return 'source';
    return raw.replace(/_/g, ' ');
  };
  const formatSourceKind = (value) => {
    const raw = String(value || '').trim();
    if (!raw) return 'reference';
    return raw.replace(/_/g, ' ');
  };
  const sourceTone = (source) => {
    const sourceClass = String(source?.source_class || '').trim().toLowerCase();
    const integrationStatus = String(source?.integration_status || '').trim().toLowerCase();
    if (integrationStatus === 'ingest_candidate') return 'warning';
    if (integrationStatus === 'system_generated') return 'info';
    if (sourceClass === 'durable_source') return 'success';
    return 'neutral';
  };
  const sourcePreview = stripInlineMarkdown(selectedSource?.content || '').slice(0, 260);
  const selectedSourceIsMarkdown = /\.(md|markdown|txt)$/i.test(String(selectedSource?.path || ''));
  const selectedSourceIsJson = /\.json$/i.test(String(selectedSource?.path || ''));

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
              : (sources.length ? `${sources.length} source${sources.length === 1 ? '' : 's'} visible` : 'No raw sources visible yet')}
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
                  {stripInlineMarkdown(page.summary || page.body || '').slice(0, 180) || 'No summary available yet.'}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  <RoutePill label="TYPE" value={classifyWikiPage(page).label} tone={classifyWikiPage(page).tone} />
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
                  Durable sources are canonical references such as specs and manuals. Ephemeral sources are reports, notes, and transient findings that should be integrated into the wiki when useful and aged off when they are not.
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
              {sources.map((source) => (
                <button
                  key={source.path}
                  type="button"
                  onClick={() => onSelectSource?.(source.path)}
                  style={{
                    width: '100%',
                    textAlign: 'left',
                    background: 'rgba(255,255,255,0.02)',
                    border: source.path === selectedSourcePath
                      ? '1px solid rgba(130,87,229,0.28)'
                      : '1px solid rgba(255,255,255,0.06)',
                    borderRadius: '12px',
                    padding: '12px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '6px',
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ color: '#edeeef', fontSize: '12px', fontWeight: 700 }}>{source.name || source.path}</div>
                  <div style={{ color: '#8d8ea1', fontSize: '11px', lineHeight: 1.5 }}>
                    {source.path === selectedSourcePath && sourcePreview
                      ? `${sourcePreview}${sourcePreview.length >= 260 ? '…' : ''}`
                      : 'Open source to inspect raw content, provenance role, and integration status.'}
                  </div>
                  <div style={{ color: '#8d8ea1', fontSize: '11px', fontFamily: "'JetBrains Mono', monospace" }}>{source.path}</div>
                  <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                    <RoutePill label="CLASS" value={formatSourceClass(source.source_class)} tone={sourceTone(source)} />
                    <RoutePill label="KIND" value={formatSourceKind(source.source_kind)} tone="neutral" />
                    <RoutePill label="STATUS" value={formatSourceKind(source.integration_status)} tone={source.integration_status === 'reference_source' ? 'info' : 'warning'} />
                    <RoutePill label="UPDATED" value={source.updated_at ? formatAbsoluteTime(source.updated_at) : '—'} tone="neutral" />
                    <RoutePill label="SIZE" value={typeof source.bytes === 'number' ? `${source.bytes}b` : '—'} tone="neutral" />
                  </div>
                </button>
              ))}
              {!sources.length && (
                <div style={{ fontSize: '12px', color: '#8d8ea1', lineHeight: 1.6 }}>
                  No raw knowledge sources are visible yet.
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div style={{ minWidth: 0, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '18px' }}>
        {knowledgeMode === 'wiki' && selectedPage ? (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                <RoutePill label="TYPE" value={classifyWikiPage(selectedPage).label} tone={classifyWikiPage(selectedPage).tone} />
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
              Wiki pages are the integrated layer. Durable sources and ephemeral reports can both feed it, but they should not remain indistinguishable forever.
            </div>
          </div>
        ) : selectedSource ? (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                <RoutePill label="CLASS" value={formatSourceClass(selectedSource.source_class)} tone={sourceTone(selectedSource)} />
                <RoutePill label="KIND" value={formatSourceKind(selectedSource.source_kind)} tone="neutral" />
                <RoutePill label="STATUS" value={formatSourceKind(selectedSource.integration_status)} tone={selectedSource.integration_status === 'reference_source' ? 'info' : 'warning'} />
                <RoutePill label="UPDATED" value={selectedSource.updated_at ? formatAbsoluteTime(selectedSource.updated_at) : '—'} tone="neutral" />
                <RoutePill label="SIZE" value={typeof selectedSource.bytes === 'number' ? `${selectedSource.bytes}b` : '—'} tone="neutral" />
              </div>
              <div>
                <h2 style={{ margin: 0, color: '#edeeef', fontSize: '28px', lineHeight: 1.1 }}>{selectedSource.name || selectedSource.path}</h2>
                <div style={{ marginTop: '10px', color: '#a9aaba', fontSize: '13px', lineHeight: 1.6, fontFamily: "'JetBrains Mono', monospace" }}>
                  {selectedSource.path}
                </div>
              </div>
            </div>

            <DashboardPanel title="SOURCE ROLE">
              <div style={{ color: '#a9aaba', fontSize: '13px', lineHeight: 1.7 }}>
                {selectedSource.integration_status === 'reference_source'
                  ? 'This source is treated as reference material. It should remain visible in Sources, and only derived, verified knowledge should surface in Wiki.'
                  : selectedSource.integration_status === 'ingest_candidate'
                  ? 'This source is queued or eligible for integration. It should be distilled into a verified wiki page before it becomes operator-facing canonical knowledge.'
                  : 'This source is system-generated support material and should stay out of the canonical wiki unless intentionally transformed.'}
              </div>
            </DashboardPanel>

            <div style={{ background: '#141418', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '16px', padding: '22px', overflow: 'hidden' }}>
              {selectedSourceIsJson ? (
                <pre style={{ margin: 0, color: '#d9dcec', fontSize: '12px', lineHeight: 1.7, fontFamily: "'JetBrains Mono', monospace", whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {selectedSource.content || ''}
                </pre>
              ) : (
                <div className="markdown-body" style={{ fontSize: '14px', lineHeight: '1.75', color: '#edeeef' }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {selectedSourceIsMarkdown ? (selectedSource.content || '') : `\`\`\`\n${selectedSource.content || ''}\n\`\`\``}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          </>
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

const buildWorkbenchPrompt = (action, target, taskMatch, procedureMatch, attemptMatch) => {
  const item = attemptMatch || taskMatch || procedureMatch;
  const label = attemptMatch ? `Attempt #${String(attemptMatch.attempt_id).slice(-6)}` : (target?.taskTitle || target?.title || taskMatch?.title || procedureMatch?.title || 'this target');
  const taskId = attemptMatch?.task_id || target?.taskId || taskMatch?.id || '';
  const procedureId = target?.procedureId || procedureMatch?.procedure_id || '';
  const sessionId = target?.sessionId || '';
  const contextBits = [
    taskId ? `task_id=${taskId}` : '',
    attemptMatch?.attempt_id ? `attempt_id=${attemptMatch.attempt_id}` : '',
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

const taskStateTone = (s) => {
  switch (String(s || '').toLowerCase()) {
    case 'complete': return 'success';
    case 'working': return 'success';
    case 'blocked': return 'warning';
    case 'abandoned': return 'danger';
    default: return 'neutral';
  }
};

const normalizeWorkbenchAct = (message) => String(message?.message_metadata?.communicative_act || '').trim().toLowerCase();

const shouldHideWorkbenchMessage = (message) => {
  const metadata = message?.message_metadata || {};
  const sourceActor = String(metadata.source_actor || '').trim().toLowerCase();
  const act = normalizeWorkbenchAct(message);
  return sourceActor === 'task_runner' && act !== 'response' && act !== 'question';
};

const ContextSnapshotSnapshot = ({ artifact }) => {
  const payload = artifact.payload || {};
  const [expanded, setExpanded] = React.useState(false);
  const userMessage = String(payload.user_message || '').trim();
  const systemPromptPreview = String(payload.system_prompt_preview || '').trim();
  const uniqueContext = payload.unique_context && typeof payload.unique_context === 'object' ? payload.unique_context : {};
  const toolNames = Array.isArray(payload.tool_names) ? payload.tool_names : [];
  const staticRefs = Array.isArray(payload.static_section_refs) ? payload.static_section_refs : [];
  const preferredPaths = Array.isArray(uniqueContext.preferred_start_paths) ? uniqueContext.preferred_start_paths : [];
  const handoffContext = uniqueContext.handoff_context && typeof uniqueContext.handoff_context === 'object' ? uniqueContext.handoff_context : {};
  const graphContext = uniqueContext.task_graph_context && typeof uniqueContext.task_graph_context === 'object' ? uniqueContext.task_graph_context : {};
  const recentPath = Array.isArray(graphContext.recent_path) ? graphContext.recent_path : [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div style={{ fontSize: '14px', fontWeight: 700, color: '#edeeef' }}>
        Attempt Context Snapshot
      </div>
      <div style={{ fontSize: '11px', color: '#6f7183', fontFamily: "'JetBrains Mono', monospace" }}>
        Captured at: {payload.timestamp || artifact.created_at}
      </div>

      <div style={{ display: 'flex', gap: '8px' }}>
        {payload.prompt_template_ref && <RoutePill label="PROMPT" value={payload.prompt_template_ref} />}
        {payload.prompt_lineage_id && <RoutePill label="LINEAGE" value={payload.prompt_lineage_id} tone="success" />}
        <RoutePill label="TOOLS" value={toolNames.length || 0} tone="success" />
        {staticRefs.length > 0 && <RoutePill label="STATIC" value={staticRefs.length} />}
      </div>

      {userMessage && (
        <div style={{ background: 'rgba(0,0,0,0.2)', borderRadius: '8px', padding: '12px', border: '1px solid rgba(255,255,255,0.05)' }}>
          <div style={{ fontSize: '11px', color: '#555', fontWeight: 800, marginBottom: '8px', textTransform: 'uppercase' }}>User Message</div>
          <div style={{ fontSize: '12px', color: '#edeeef', whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
            {userMessage}
          </div>
        </div>
      )}

      <div style={{ background: 'rgba(0,0,0,0.2)', borderRadius: '8px', padding: '12px', border: '1px solid rgba(255,255,255,0.05)' }}>
        <div style={{ fontSize: '11px', color: '#555', fontWeight: 800, marginBottom: '8px', textTransform: 'uppercase' }}>System Prompt Excerpt</div>
        <div style={{ fontSize: '12px', color: '#a9aaba', whiteSpace: 'pre-wrap', maxHeight: expanded ? 'none' : '140px', overflow: 'hidden', lineHeight: 1.55 }}>
          {systemPromptPreview || '(no system prompt preview captured)'}
        </div>
        <button
          onClick={() => setExpanded(!expanded)}
          style={{ background: 'none', border: 'none', color: '#60a5fa', cursor: 'pointer', fontSize: '11px', padding: 0, marginTop: '8px' }}
        >
          {expanded ? 'Show Less' : 'Show More Context'}
        </button>
      </div>

      {preferredPaths.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div style={{ fontSize: '11px', color: '#555', fontWeight: 800, textTransform: 'uppercase' }}>Preferred Start Paths</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            {preferredPaths.map((path) => (
              <RoutePill key={path} label="PATH" value={path} />
            ))}
          </div>
        </div>
      )}

      {(handoffContext.tool_call?.name || handoffContext.next_step_hint) && (
        <div style={{ background: 'rgba(130,87,229,0.03)', border: '1px solid rgba(130,87,229,0.1)', borderRadius: '12px', padding: '14px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {handoffContext.tool_call?.name && <RoutePill label="INHERITED TOOL" value={handoffContext.tool_call.name} tone="info" />}
            {handoffContext.from_task_title && <RoutePill label="FROM TASK" value={handoffContext.from_task_title} />}
          </div>
          {handoffContext.next_step_hint && (
            <div style={{ fontSize: '12px', color: '#edeeef', lineHeight: 1.6 }}>
              {handoffContext.next_step_hint}
            </div>
          )}
          {handoffContext.tool_result_preview && (
            <pre style={{ margin: 0, fontSize: '11px', background: '#0f1014', padding: '10px', borderRadius: '8px', overflowX: 'auto', color: '#d5d8e6', whiteSpace: 'pre-wrap' }}>
              {handoffContext.tool_result_preview}
            </pre>
          )}
        </div>
      )}

      {recentPath.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div style={{ fontSize: '11px', color: '#555', fontWeight: 800, textTransform: 'uppercase' }}>Recent Task Path</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {recentPath.map((node, index) => (
              <div key={`${node.task_id || node.title}-${index}`} style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: '#c7c8d6' }}>
                <RoutePill label={`D${node.depth ?? index}`} value={node.state || 'pending'} tone="neutral" />
                <span>{node.title || 'Untitled task'}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

const ModelTurnSnapshotCard = ({ artifact }) => {
  const payload = artifact.payload || {};
  const toolCalls = Array.isArray(payload.tool_calls) ? payload.tool_calls : [];
  const usage = payload.usage && typeof payload.usage === 'object' ? payload.usage : {};
  const reasoningTokens = usage?.completion_tokens_details?.reasoning_tokens;
  return (
    <div style={{ background: 'rgba(56,189,248,0.03)', border: '1px solid rgba(56,189,248,0.12)', borderRadius: '12px', padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <RoutePill label="MODEL TURN" value={payload.response_status || 'unknown'} tone={payload.response_status === 'success' ? 'success' : 'warning'} />
          {payload.provider && <RoutePill label="PROVIDER" value={payload.provider} />}
          {payload.model && <RoutePill label="MODEL" value={payload.model} />}
          {usage.prompt_tokens != null && <RoutePill label="PROMPT TOKENS" value={usage.prompt_tokens} />}
          {usage.completion_tokens != null && <RoutePill label="COMPLETION TOKENS" value={usage.completion_tokens} />}
          {reasoningTokens != null && <RoutePill label="REASONING" value={reasoningTokens} tone="warning" />}
        </div>
        <span style={{ fontSize: '10px', color: '#444' }}>{formatAbsoluteTime(payload.timestamp || artifact.recorded_at || artifact.at)}</span>
      </div>
      {payload.content_preview && (
        <div style={{ fontSize: '12px', color: '#e7e8ef', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
          {payload.content_preview}
        </div>
      )}
      {toolCalls.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div style={{ fontSize: '11px', color: '#555', fontWeight: 800, textTransform: 'uppercase' }}>Tool Calls</div>
          {toolCalls.map((call, index) => (
            <pre key={`${call?.function?.name || 'tool'}-${index}`} style={{ margin: 0, fontSize: '11px', background: '#0f1014', padding: '10px', borderRadius: '8px', overflowX: 'auto', color: '#d5d8e6', whiteSpace: 'pre-wrap' }}>
              {`${call?.function?.name || 'unknown'}(${call?.function?.arguments || ''})`}
            </pre>
          ))}
        </div>
      )}
      {payload.error && Object.keys(payload.error).length > 0 && (
        <pre style={{ margin: 0, fontSize: '11px', background: '#190d10', padding: '10px', borderRadius: '8px', overflowX: 'auto', color: '#ffb4b4', whiteSpace: 'pre-wrap' }}>
          {stringifyJson(payload.error)}
        </pre>
      )}
    </div>
  );
};

const TerminalToolCallCard = ({ artifact }) => {
  const payload = artifact.payload || {};
  const toolCall = payload.tool_call || {};
  return (
    <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: '12px', padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <RoutePill label="TERMINAL TOOL" value={toolCall.name || 'unknown'} tone="info" />
          {payload.source_module && <RoutePill label="MODULE" value={payload.source_module} />}
        </div>
        <span style={{ fontSize: '10px', color: '#444' }}>{formatAbsoluteTime(payload.timestamp || artifact.recorded_at || artifact.at)}</span>
      </div>
      {toolCall.arguments && (
        <pre style={{ margin: 0, fontSize: '11px', background: '#0f1014', padding: '10px', borderRadius: '8px', overflowX: 'auto', color: '#d5d8e6', whiteSpace: 'pre-wrap' }}>
          {stringifyJson(toolCall.arguments)}
        </pre>
      )}
      {payload.tool_result_preview && (
        <pre style={{ margin: 0, fontSize: '11px', background: '#0f1014', padding: '10px', borderRadius: '8px', overflowX: 'auto', color: '#d5d8e6', whiteSpace: 'pre-wrap' }}>
          {payload.tool_result_preview}
        </pre>
      )}
      {payload.next_step_hint && (
        <div style={{ fontSize: '12px', color: '#edeeef', lineHeight: 1.6 }}>
          {payload.next_step_hint}
        </div>
      )}
    </div>
  );
};

const ObservabilityInspector = ({ artifacts = [] }) => {
  if (!artifacts || artifacts.length === 0) return null;

  const renderArtifact = (artifact, idx) => {
    const kind = artifact.artifact_kind;
    const payload = artifact.payload || {};
    const timestamp = artifact.recorded_at || artifact.at;

    switch (kind) {
      case 'plan_review':
        return (
          <div key={idx} style={{ background: 'rgba(130,87,229,0.03)', border: '1px solid rgba(130,87,229,0.1)', borderRadius: '12px', padding: '16px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <div style={{ display: 'flex', gap: '8px' }}>
                <RoutePill label="PLAN REVIEW" value={payload.review_summary || 'Analysis'} tone="info" />
                <RoutePill label="ACTION" value={payload.recommended_action} tone="warning" />
              </div>
              <span style={{ fontSize: '10px', color: '#444' }}>{formatAbsoluteTime(timestamp)}</span>
            </div>
            <div className="markdown-body" style={{ fontSize: '13px', color: '#e7e8ef', lineHeight: 1.6 }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{payload.review_body || ''}</ReactMarkdown>
            </div>
          </div>
        );
      case 'autopsy':
        return (
          <div key={idx} style={{ background: 'rgba(248,113,113,0.03)', border: '1px solid rgba(248,113,113,0.1)', borderRadius: '12px', padding: '16px' }}>
             <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                <RoutePill label="AUTOPSY" value={payload.failure_kind || 'Task Failure'} tone="danger" />
                <span style={{ fontSize: '10px', color: '#444' }}>{formatAbsoluteTime(timestamp)}</span>
             </div>
             <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {payload.tool_call && (
                  <div style={{ fontSize: '12px', color: '#a9aaba', fontFamily: "'JetBrains Mono', monospace", background: 'rgba(0,0,0,0.2)', padding: '8px', borderRadius: '6px' }}>
                    EXE: {payload.tool_call.name}({payload.tool_call.arguments})
                  </div>
                )}
                <div style={{ fontSize: '13px', color: '#edeeef', borderLeft: '2px solid #f87171', paddingLeft: '12px' }}>
                   {payload.next_step_hint || 'No recovery guidance provided.'}
                </div>
                {payload.avoid_repeating_first_tool && (
                   <div style={{ fontSize: '11px', color: '#f87171', fontStyle: 'italic' }}>
                      Guidance: Avoid repeating {payload.avoid_repeating_first_tool.name} if previous result stands.
                   </div>
                )}
             </div>
          </div>
        );
      case 'tool_trace':
        return (
          <div key={idx} style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '12px', padding: '12px' }}>
             <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                <RoutePill label="TOOL TRACE" value={payload.tool_name} />
                <span style={{ fontSize: '10px', color: '#444' }}>{formatAbsoluteTime(timestamp)} ({payload.duration_s}s)</span>
             </div>
             <details>
                <summary style={{ fontSize: '11px', color: '#666', cursor: 'pointer' }}>View Arguments and Result</summary>
                <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                   <pre style={{ fontSize: '11px', background: '#000', padding: '8px', borderRadius: '4px', overflowX: 'auto', color: '#8d8ea1' }}>
                      ARGS: {stringifyJson(payload.arguments)}
                   </pre>
                   <pre style={{ fontSize: '11px', background: '#000', padding: '8px', borderRadius: '4px', overflowX: 'auto', color: '#ececf2' }}>
                      RES: {typeof payload.result === 'string' ? (payload.result.length > 500 ? payload.result.slice(0, 500) + '...' : payload.result) : stringifyJson(payload.result)}
                   </pre>
                </div>
             </details>
          </div>
        );
      case 'verifier_review':
        return (
          <div key={idx} style={{ background: 'rgba(0,242,148,0.03)', border: '1px solid rgba(0,242,148,0.1)', borderRadius: '12px', padding: '16px' }}>
             <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                <div style={{ display: 'flex', gap: '8px' }}>
                   <RoutePill label="VERIFICATION" value={payload.verdict} tone={payload.verdict === 'good' ? 'success' : (payload.verdict === 'flawed' ? 'danger' : 'warning')} />
                   <RoutePill label="CONFIDENCE" value={`${Math.round(payload.confidence * 100)}%`} />
                   {payload.mechanism_failure_kind && <RoutePill label="MECHANISM" value={payload.mechanism_failure_kind} tone="danger" />}
                </div>
                <span style={{ fontSize: '10px', color: '#444' }}>{formatAbsoluteTime(timestamp)}</span>
             </div>
             <div style={{ fontSize: '13px', color: '#edeeef', lineHeight: 1.5 }}>
                {payload.reasons ? payload.reasons.join(' ') : 'No reasoning text provided.'}
             </div>
          </div>
        );
      case 'context_snapshot':
        return <ContextSnapshotSnapshot artifact={artifact} />;
      case 'model_turn_snapshot':
        return <ModelTurnSnapshotCard key={idx} artifact={artifact} />;
      case 'terminal_tool_call':
        return <TerminalToolCallCard key={idx} artifact={artifact} />;
      default:
        return (
          <div key={idx} style={{ background: 'rgba(255,255,255,0.01)', border: '1px solid rgba(255,255,255,0.04)', borderRadius: '12px', padding: '12px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
               <span style={{ fontSize: '10px', fontWeight: 800, color: '#444', letterSpacing: '0.05em' }}>{String(kind || 'LOG').toUpperCase()}</span>
               <span style={{ fontSize: '10px', color: '#444' }}>{formatAbsoluteTime(timestamp)}</span>
            </div>
            <pre style={{ margin: 0, fontSize: '11px', color: '#777', overflowX: 'auto' }}>
               {stringifyJson(payload || artifact)}
            </pre>
          </div>
        );
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
       {artifacts.map((a, i) => renderArtifact(a, i))}
    </div>
  );
};

const AttemptTimeline = ({ stepHistory = [], outcome, reason }) => {
  if (!stepHistory || stepHistory.length === 0) return null;

  const getStepTone = (s) => {
    switch (String(s).toLowerCase()) {
      case 'research': return 'info';
      case 'decompose': return 'danger'; // Using danger for purple/purple-ish tones in this palette
      case 'implementation': return 'success';
      case 'deterministic_check': return 'warning';
      default: return 'neutral';
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0', paddingLeft: '8px' }}>
      {stepHistory.map((step, idx) => {
        const nextStep = stepHistory[idx + 1];
        const durationDisplay = nextStep ? (() => {
          const start = parseTimestamp(step.at);
          const end = parseTimestamp(nextStep.at);
          if (start && end) {
            const diff = Math.round((end - start) / 1000);
            return diff > 0 ? `${diff}s` : null;
          }
          return null;
        })() : null;

        return (
          <div key={idx} style={{ display: 'flex', flexDirection: 'column', gap: '0' }}>
            <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start' }}>
              <div style={{ position: 'relative', height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                {(!outcome && idx === stepHistory.length - 1) ? (
                  <motion.div
                    animate={{ scale: [1, 1.4, 1], opacity: [0.6, 1, 0.6] }}
                    transition={{ repeat: Infinity, duration: 2 }}
                    style={{
                      width: '10px', height: '10px', borderRadius: '999px',
                      background: '#8257e5',
                      border: '2px solid #141418',
                      zIndex: 2, marginTop: '12px',
                      boxShadow: '0 0 8px rgba(130,87,229,0.4)'
                    }}
                  />
                ) : (
                  <div style={{
                    width: '10px', height: '10px', borderRadius: '999px',
                    background: idx === 0 ? '#8257e5' : 'rgba(255,255,255,0.1)',
                    border: '2px solid #141418',
                    zIndex: 1, marginTop: '12px'
                  }} />
                )}
                {idx < stepHistory.length - 1 && (
                  <div style={{
                    position: 'absolute', top: '22px', bottom: '-12px',
                    width: '2px', background: 'rgba(255,255,255,0.05)'
                  }} />
                )}
              </div>
              <div style={{ padding: '8px 0 16px 0', flex: 1 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                   <RoutePill label={step.step || 'STEP'} value={step.label} tone={getStepTone(step.step)} />
                   <span style={{ fontSize: '10px', color: '#444' }}>{formatAbsoluteTime(step.at)}</span>
                </div>
                <div style={{ fontSize: '12px', color: '#8d8ea1', paddingLeft: '4px' }}>{step.detail}</div>
                {durationDisplay && (
                  <div style={{
                    fontSize: '9px', fontWeight: 800, color: '#333',
                    letterSpacing: '0.05em', marginTop: '8px', marginBottom: '-16px',
                    marginLeft: '-46px', textAlign: 'center', width: '32px',
                    background: '#141418', position: 'relative', zIndex: 1
                  }}>
                    {durationDisplay}
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })}
      {outcome && (
        <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start' }}>
          <div style={{ width: '10px', height: '10px', borderRadius: '2px', background: outcome === 'succeeded' ? '#00f294' : '#f87171', border: '2px solid #141418', marginTop: '12px', zIndex: 1 }} />
          <div style={{ padding: '8px 0', flex: 1 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
               <RoutePill label="OUTCOME" value={outcome} tone={outcome === 'succeeded' ? 'success' : 'danger'} />
            </div>
            {reason && <div style={{ fontSize: '12px', color: outcome === 'succeeded' ? '#a9aaba' : '#f87171', marginTop: '4px', paddingLeft: '4px' }}>{reason}</div>}
          </div>
        </div>
      )}
    </div>
  );
};

const ReplayControl = ({ task, onConfirm, onCancel }) => {
  const [model, setModel] = React.useState('');
  const [tier, setTier] = React.useState('');
  const [isBusy, setIsBusy] = React.useState(false);

  const handleRun = async () => {
    setIsBusy(true);
    try {
      await onConfirm({ model, tier });
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <DashboardPanel title="REPLAY CONFIGURATION">
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        <div style={{ fontSize: '13px', color: '#a9aaba' }}>
          Configure overrides for replaying <strong>{task.title}</strong>. This will spawn a new attempt with the specified variances.
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <label style={{ fontSize: '10px', color: '#555', fontWeight: 800 }}>MODEL OVERRIDE</label>
            <input
              type="text"
              placeholder="e.g. gpt-4o"
              value={model}
              onChange={e => setModel(e.target.value)}
              style={{ background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '8px', padding: '8px 12px', color: '#edeeef', fontSize: '12px' }}
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <label style={{ fontSize: '10px', color: '#555', fontWeight: 800 }}>TIER OVERRIDE</label>
            <select
              value={tier}
              onChange={e => setTier(e.target.value)}
              style={{ background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '8px', padding: '8px 12px', color: '#edeeef', fontSize: '12px' }}
            >
              <option value="">(Default Lifecycle)</option>
              <option value="strong">Strong (Trainer)</option>
              <option value="weak">Weak (Agent)</option>
            </select>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '12px', marginTop: '4px' }}>
          <button
            onClick={handleRun}
            disabled={isBusy}
            style={{
               flex: 1, height: '36px', background: 'linear-gradient(to right, #2563eb, #1d4ed8)',
               border: 'none', borderRadius: '8px', color: 'white', fontWeight: 700, fontSize: '13px',
               cursor: 'pointer', transition: 'all 0.2s', opacity: isBusy ? 0.6 : 1
            }}
          >
            {isBusy ? 'Starting...' : 'Run Replay Attempt'}
          </button>
          <button
            onClick={onCancel}
            disabled={isBusy}
            style={{
               padding: '0 16px', height: '36px', background: 'rgba(255,255,255,0.05)',
               border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', color: '#a9aaba',
               fontWeight: 700, fontSize: '13px', cursor: 'pointer'
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </DashboardPanel>
  );
};

const TaskTargetCard = ({
  task,
  onOpenTask,
  onOpenProcedure,
  onPauseTask,
  onResumeTask,
  onStopTask,
  onReplayTask,
  activeTool,
  onSendWorkbenchPrompt,
}) => {
  const [isReplaying, setIsReplaying] = React.useState(false);
  if (!task) return null;
  const stateTone = (s) => taskStateTone(s);
  const processChildren = Array.isArray(task.children) ? task.children.filter((child) => isDecompositionPhaseNode(child)) : [];
  const workChildren = Array.isArray(task.children) ? task.children.filter((child) => !isDecompositionPhaseNode(child)) : [];
  const attemptArtifactsById = React.useMemo(() => {
    const grouped = {};
    (Array.isArray(task.observability) ? task.observability : []).forEach((artifact) => {
      const attemptId = String(artifact?.attempt_id || '').trim();
      if (!attemptId) return;
      grouped[attemptId] = grouped[attemptId] || [];
      grouped[attemptId].push(artifact);
    });
    return grouped;
  }, [task.observability]);

  const handleTaskClick = () => {
    if (activeTool && onSendWorkbenchPrompt) {
       onSendWorkbenchPrompt(activeTool, { task });
    }
  };

  return (
    <div
      onClick={handleTaskClick}
      style={{
        display: 'flex', flexDirection: 'column', gap: '16px', padding: '4px',
        cursor: activeTool ? 'pointer' : 'default',
        borderRadius: '12px',
        transition: 'background 0.2s',
        background: activeTool ? 'rgba(130,87,229,0.03)' : 'transparent',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', flex: 1 }}>
          <div style={{ fontSize: '20px', fontWeight: 700, color: '#edeeef', lineHeight: 1.2 }}>{task.title}</div>
          <div style={{ fontSize: '13px', color: '#a9aaba', lineHeight: 1.6 }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{task.description || ''}</ReactMarkdown>
          </div>
        </div>
        <RoutePill label="STATUS" value={task.state} tone={stateTone(task.state)} />
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'center' }}>
        <RoutePill label="TASK ID" value={String(task.id || task.task_id)} />
        <div style={{ display: 'flex', gap: '4px', marginLeft: 'auto' }}>
          <button
            type="button"
            onClick={() => onSendWorkbenchPrompt && onSendWorkbenchPrompt({ action: 'explain', task })}
            style={{ ...workbenchActionButtonStyle, color: '#8257e5' }}
            title="Explain task"
          >
            <Activity size={14} />
          </button>
          <button
            type="button"
            onClick={() => onSendWorkbenchPrompt && onSendWorkbenchPrompt({ action: 'verify', task })}
            style={{ ...workbenchActionButtonStyle, color: '#00f294' }}
            title="Verify task"
          >
            <Route size={14} />
          </button>
          {task.state !== 'complete' && task.state !== 'cancelled' && (
            <>
              {task.paused ? (
                <button
                  type="button"
                  onClick={() => onResumeTask && onResumeTask(task.id || task.task_id)}
                  style={{ ...workbenchActionButtonStyle, color: '#34d399' }}
                  title="Resume task"
                >
                  <Play size={14} />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => onPauseTask && onPauseTask(task.id || task.task_id)}
                  style={{ ...workbenchActionButtonStyle, color: '#fbbf24' }}
                  title="Pause task"
                >
                  <Activity size={14} />
                </button>
              )}
              <button
                type="button"
                onClick={() => onStopTask && onStopTask(task.id || task.task_id)}
                style={{ ...workbenchActionButtonStyle, color: '#f87171' }}
                title="Stop task"
              >
                <Activity size={14} style={{ transform: 'rotate(90deg)' }} />
              </button>
            </>
          )}
          <button
            type="button"
            onClick={() => setIsReplaying(true)}
            style={{ ...workbenchActionButtonStyle, color: '#60a5fa' }}
            title="Replay task"
          >
            <History size={14} />
          </button>
          <button
            type="button"
            onClick={() => {
              const newTitle = window.prompt('New Title:', task.title);
              if (newTitle) onMutateTask && onMutateTask(task.id || task.task_id, { title: newTitle });
            }}
            style={{ ...workbenchActionButtonStyle, color: '#fcd34d' }}
            title="Edit Title"
          >
            <Wrench size={14} />
          </button>
          <button
            type="button"
            onClick={() => {
              if (window.confirm('Create a branch of this task?')) {
                onBranchTask && onBranchTask(task.id || task.task_id, {});
              }
            }}
            style={{ ...workbenchActionButtonStyle, color: '#a78bfa' }}
            title="Branch Task"
          >
            <Play size={14} style={{ transform: 'rotate(-45deg)' }} />
          </button>
        </div>
      </div>

      {isReplaying && (
        <ReplayControl
          task={task}
          onCancel={() => setIsReplaying(false)}
          onConfirm={async (overrides) => {
             if (onReplayTask) {
               await onReplayTask(task.id || task.task_id, overrides);
               setIsReplaying(false);
             }
          }}
        />
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
        <RoutePill label="LANE" value={task.lane || 'trainer'} />
        <RoutePill label="TYPE" value={task.type || 'research'} />
        {task.parent_task_id && (
          <button
            type="button"
            onClick={() => onOpenTask && onOpenTask(task.parent_task_id)}
            style={{
              background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: '999px', padding: '6px 12px', fontSize: '10px',
              color: '#8d8ea1', cursor: 'pointer'
            }}
          >
            PARENT: #{task.parent_task_id}
          </button>
        )}
      </div>

      <div style={{ borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
        <div style={{ fontSize: '11px', color: '#555', fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Constraints & Lifecycle</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
          {task.procedure_id && (
            <button
              type="button"
              onClick={() => onOpenProcedure && onOpenProcedure(task.procedure_id)}
              style={{ padding: 0, border: 'none', background: 'transparent', cursor: 'pointer' }}
            >
              <RoutePill label="PROCEDURE" value={task.procedure_id} tone="success" />
            </button>
          )}
          {task.execution_mode && <RoutePill label="EXECUTION" value={task.execution_mode} />}
          {task.depth > 0 && <RoutePill label="DEPTH" value={String(task.depth)} />}
          {(task.active_child_ids?.length > 0 || workChildren.length > 0) && <RoutePill label="CHILDREN" value={String(task.active_child_ids?.length || workChildren.length)} tone="success" />}
          {processChildren.length > 0 && <RoutePill label="PROCESS" value={`${processChildren.length} phases`} tone="warning" />}
        </div>

        {/* Task 1.6: Policy/Verification Card for JUDGE tasks */}
        {task.type === 'JUDGE' && task.constraints?.verification_policy && (
          <div style={{ background: 'rgba(0,242,148,0.03)', border: '1px solid rgba(0,242,148,0.1)', borderRadius: '10px', padding: '12px', marginTop: '4px' }}>
             <div style={{ fontSize: '10px', color: '#00f294', fontWeight: 800, marginBottom: '6px' }}>VERIFICATION POLICY</div>
             <div style={{ fontSize: '12px', color: '#c7c8d6', lineHeight: 1.5 }}>
                {task.constraints.verification_policy}
             </div>
          </div>
        )}

        <div style={{ fontSize: '12px', color: '#8d8ea1' }}>
          Created {formatAbsoluteWithRelative(task.created_at)}
          {task.updated_at && task.updated_at !== task.created_at && ` · Last updated ${formatAbsoluteTime(task.updated_at)}`}
        </div>
      </div>

      {processChildren.length > 0 && (
        <DashboardPanel title="DECOMPOSITION PROCESS">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {processChildren.map((child) => {
              const phaseId = String(child?.constraints?.decomposition_phase_id || '').trim();
              const phaseSummary = summarizeDecompositionPhase(child);
              const tone = phaseId.startsWith('issue_') || phaseId.startsWith('guardrail') ? 'danger' : 'warning';
              return (
                <button
                  key={child.task_id}
                  onClick={() => onOpenTask && onOpenTask(child.task_id)}
                  style={{
                    background: 'rgba(255,255,255,0.02)',
                    border: `1px solid ${tone === 'danger' ? 'rgba(248,113,113,0.24)' : 'rgba(255,184,77,0.18)'}`,
                    textAlign: 'left',
                    cursor: 'pointer',
                    borderRadius: '12px',
                    padding: '10px 14px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '6px'
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px' }}>
                    <div style={{ fontSize: '13px', fontWeight: 700, color: '#edeeef' }}>{child.title || 'Untitled Decomposition Phase'}</div>
                    <RoutePill label="PHASE" value={phaseId || 'decomposition'} tone={tone} />
                  </div>
                  {phaseSummary ? (
                    <div style={{ fontSize: '12px', color: '#bfc2d0', lineHeight: 1.6 }}>{phaseSummary}</div>
                  ) : null}
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: '11px', color: '#555', fontFamily: "'JetBrains Mono', monospace" }}>#{child.task_id}</span>
                    <span style={{ color: '#444', fontSize: '10px' }}>•</span>
                    <span style={{ fontSize: '11px', color: '#666' }}>{formatAbsoluteWithRelative(child.updated_at || child.created_at)}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </DashboardPanel>
      )}

      {/* Task 2.5: Child Work Drilldown */}
      {workChildren.length > 0 && (
        <DashboardPanel title="CHILD WORK">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <div style={{ display: 'flex', gap: '12px', marginBottom: '4px' }}>
               <RoutePill label="MODE" value={task.execution_mode || 'SERIAL'} />
               {task.procedure_id && <RoutePill label="PROCEDURE" value={task.procedure_id} tone="success" />}
            </div>
            {workChildren.map(child => (
              <button
                key={child.task_id}
                onClick={() => onOpenTask && onOpenTask(child.task_id)}
                style={{
                   background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.05)',
                   textAlign: 'left', cursor: 'pointer', borderRadius: '12px', padding: '10px 14px',
                   display: 'flex', justifyContent: 'space-between', alignItems: 'center'
                }}
              >
                <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                  <div style={{ fontSize: '13px', fontWeight: 700, color: '#edeeef' }}>{child.title || 'Untitled Subtask'}</div>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <span style={{ fontSize: '11px', color: '#555', fontFamily: "'JetBrains Mono', monospace" }}>#{child.task_id}</span>
                    <span style={{ color: '#444', fontSize: '10px' }}>•</span>
                    <span style={{ fontSize: '11px', color: '#666' }}>{child.lane || 'trainer'}</span>
                    {child.duration_s && (
                      <>
                        <span style={{ color: '#444', fontSize: '10px' }}>•</span>
                        <span style={{ fontSize: '11px', color: '#666' }}>{child.duration_s}s</span>
                      </>
                    )}
                  </div>
                </div>
                <RoutePill label="STATE" value={child.state} tone={stateTone(child.state)} />
              </button>
            ))}
          </div>
        </DashboardPanel>
      )}

      {task.attempts?.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '12px' }}>
          <div style={{ fontSize: '11px', color: '#555', fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Attempt History ({task.attempts.length})</div>
          {task.attempts.map((attempt, idx) => (
            <AttemptTargetCard
              key={`${attempt.attempt_id}-${idx}`}
              attempt={attempt}
              attemptArtifacts={attemptArtifactsById[String(attempt.attempt_id || '')] || []}
              onSendWorkbenchPrompt={onSendWorkbenchPrompt}
            />
          ))}
        </div>
      )}

      {task.observability?.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '12px' }}>
          <div style={{ fontSize: '11px', color: '#555', fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Observation Logs ({task.observability.length})</div>
          <ObservabilityInspector artifacts={task.observability} />
        </div>
      )}
    </div>
  );
};

const AttemptTargetCard = ({ attempt, attemptArtifacts = [], onSendWorkbenchPrompt }) => {
  const [expanded, setExpanded] = useState(false);
  const outcomeTone = (o) => {
    switch (String(o || '').toLowerCase()) {
      case 'succeeded': return 'success';
      case 'failed': return 'danger';
      case 'cancelled': return 'neutral';
      default: return 'neutral';
    }
  };

  return (
    <div
      style={{
        background: 'rgba(255,255,255,0.02)',
        border: '1px solid rgba(255,255,255,0.05)',
        borderRadius: '12px',
        padding: '12px',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px'
      }}
    >
      <div
        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }}
        onClick={() => setExpanded(!expanded)}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <Activity size={14} color="#8257e5" />
          <div style={{ fontSize: '13px', fontWeight: 700, color: '#ececf2' }}>Attempt #{String(attempt.attempt_id || 'new').slice(-6)}</div>
          <RoutePill label="OUTCOME" value={attempt.outcome || 'working'} tone={outcomeTone(attempt.outcome)} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {attempt.outcome === 'failed' && (
            <button
               onClick={(e) => { e.stopPropagation(); onSendWorkbenchPrompt && onSendWorkbenchPrompt('explain', { attempt }); }}
               style={{ background: 'rgba(248,113,113,0.1)', border: 'none', color: '#f87171', borderRadius: '4px', padding: '2px 6px', fontSize: '10px', fontWeight: 800, cursor: 'pointer' }}
            >
               EXPLAIN FAILURE
            </button>
          )}
          <button
             onClick={(e) => { e.stopPropagation(); onSendWorkbenchPrompt && onSendWorkbenchPrompt('audit', { attempt }); }}
             style={{ background: 'rgba(130,87,229,0.1)', border: 'none', color: '#8257e5', borderRadius: '4px', padding: '2px 6px', fontSize: '10px', fontWeight: 800, cursor: 'pointer' }}
          >
             AUDIT TRACE
          </button>
          <div style={{ fontSize: '11px', color: '#6f7183', marginLeft: '4px' }}>{formatRelativeTime(attempt.started_at)}</div>
        </div>
      </div>

      {expanded && (
        <div style={{ borderTop: '1px solid rgba(255,255,255,0.04)', paddingTop: '10px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            <RoutePill label="MODEL" value={attempt.artifacts?.model || 'unknown'} />
            <RoutePill label="PROVIDER" value={attempt.artifacts?.provider || 'unknown'} />
            <RoutePill label="DURATION" value={`${attempt.artifacts?.duration_s || '—'}s`} />
          </div>

          {attempt.artifacts?.step_history?.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.06em', textTransform: 'uppercase' }}>Execution Timeline</div>
              <AttemptTimeline
                stepHistory={attempt.artifacts.step_history}
                outcome={attempt.outcome}
                reason={attempt.reason}
              />
            </div>
          )}

          {attempt.evidence?.autopsy && (
            <ObservabilityInspector artifacts={[{ artifact_kind: 'autopsy', payload: attempt.evidence.autopsy, at: attempt.ended_at }]} />
          )}
          {attemptArtifacts.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.06em', textTransform: 'uppercase' }}>Attempt Observability</div>
              <ObservabilityInspector artifacts={attemptArtifacts} />
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const workPoolOptionsForLane = (lane) => {
  if (lane === 'trainer') return [{ id: 'trainer', label: 'trainer' }];
  return [
    { id: 'local_agent', label: 'local_agent' },
    { id: 'remote_agent', label: 'remote_agent' },
  ];
};

const ProcedureTargetCard = ({ procedure, workbenchLane = 'agent', selectedWorkPool, onSelectWorkPool, onSendWorkbenchPrompt }) => {
  const [expandedStepId, setExpandedStepId] = useState(null);
  const [playingStepId, setPlayingStepId] = useState(null);

  if (!procedure) return null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', padding: '4px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', flex: 1 }}>
          <div style={{ fontSize: '24px', fontWeight: 800, color: '#edeeef', letterSpacing: '-0.02em', lineHeight: 1.1 }}>{procedure.title}</div>
          <div style={{ fontSize: '15px', color: '#8d8ea1', lineHeight: 1.6, maxWidth: '700px' }}>{procedure.summary}</div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '12px' }}>
          <RoutePill label="STATE" value={procedure.lifecycle_state || 'draft'} tone={procedureLifecycleTone(procedure.lifecycle_state)} />
          <button
            onClick={() => onSendWorkbenchPrompt && onSendWorkbenchPrompt('play_procedure', { procedure })}
            style={{
              background: 'linear-gradient(135deg, #8257e5, #6341b0)', color: 'white', border: 'none', borderRadius: '10px',
              padding: '10px 20px', fontSize: '13px', fontWeight: 800, cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: '8px', boxShadow: '0 4px 12px rgba(130,87,229,0.3)'
            }}
          >
            <Play size={14} fill="currentColor" />
            PLAY PROCEDURE
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
        <RoutePill label="PROCEDURE ID" value={procedure.procedure_id} tone="success" />
        <RoutePill label="LINEAGE" value={procedure.lineage_id} />
        {procedure.variant_id && <RoutePill label="VARIANT" value={procedure.variant_id} />}
        <RoutePill label="TARGET" value={selectedWorkPool || (workbenchLane === 'trainer' ? 'trainer' : 'local_agent')} tone="info" />
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'center' }}>
        <div style={{ fontSize: '11px', color: '#7f8091', fontWeight: 800, letterSpacing: '0.08em' }}>EXECUTION PROFILE</div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {workPoolOptionsForLane(workbenchLane).map((option) => {
            const active = option.id === selectedWorkPool;
            return (
              <button
                key={option.id}
                type="button"
                onClick={() => onSelectWorkPool?.(option.id)}
                style={{
                  background: active ? 'rgba(85,149,255,0.14)' : 'rgba(255,255,255,0.04)',
                  border: active ? '1px solid rgba(85,149,255,0.28)' : '1px solid rgba(255,255,255,0.08)',
                  color: active ? '#ddebff' : '#c7c8d6',
                  borderRadius: '999px',
                  padding: '7px 12px',
                  fontSize: '11px',
                  fontWeight: 800,
                  cursor: 'pointer',
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      </div>

      {procedure.description && (
        <div style={{ fontSize: '13px', color: '#c7c8d6', lineHeight: 1.6, background: 'rgba(255,255,255,0.02)', padding: '14px', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.04)' }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{procedure.description}</ReactMarkdown>
        </div>
      )}

      {procedure.checklist?.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ fontSize: '12px', color: '#444', fontWeight: 800, letterSpacing: '0.1em', textTransform: 'uppercase' }}>Checklist Steps</div>
          {procedure.checklist.map((item, idx) => {
            const isExpanded = expandedStepId === item.id;
            const isPlaying = playingStepId === item.id;

            return (
              <div
                key={item.id || idx}
                style={{
                  background: isExpanded ? 'rgba(130,87,229,0.04)' : 'rgba(255,255,255,0.02)',
                  border: isExpanded ? '1px solid rgba(130,87,229,0.2)' : '1px solid rgba(255,255,255,0.04)',
                  borderRadius: '14px', overflow: 'hidden',
                  transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)'
                }}
              >
                <div
                  onClick={() => setExpandedStepId(isExpanded ? null : item.id)}
                  style={{ cursor: 'pointer', padding: '16px', display: 'flex', gap: '16px', alignItems: 'flex-start' }}
                >
                  <div style={{
                    flexShrink: 0, width: '28px', height: '28px', borderRadius: '8px',
                    background: isExpanded ? '#8257e5' : 'rgba(255,255,255,0.05)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: '14px', fontWeight: 800, color: isExpanded ? 'white' : '#555'
                  }}>
                    {idx + 1}
                  </div>
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    <div style={{ fontSize: '15px', fontWeight: 700, color: isExpanded ? '#edeeef' : '#8d8ea1' }}>{item.title}</div>
                    <div style={{ fontSize: '13px', color: '#666', lineHeight: 1.5 }}>
                      <span style={{ color: '#444', fontWeight: 800, marginRight: '8px', fontSize: '11px' }}>VERIFY:</span>
                      {item.verification}
                    </div>
                  </div>
                  <ChevronRight size={20} color="#333" style={{ transform: isExpanded ? 'rotate(90deg)' : 'none', transition: 'transform 0.2s', marginTop: '4px' }} />
                </div>

                <AnimatePresence>
                  {isExpanded && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      style={{ overflow: 'hidden' }}
                    >
                      <div style={{ padding: '0 16px 16px 60px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
                        <div style={{ display: 'flex', gap: '10px' }}>
                          <button
                            onClick={(e) => { e.stopPropagation(); onSendWorkbenchPrompt && onSendWorkbenchPrompt('preview_step', { procedure, step: item, workPool: selectedWorkPool }); }}
                            style={{
                              background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)',
                              borderRadius: '8px', padding: '8px 16px', fontSize: '11px', fontWeight: 800,
                              color: '#fff', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px'
                            }}
                          >
                            <Search size={14} />
                            PREVIEW PROMPT
                          </button>
                          <button
                            onClick={async (e) => {
                              e.stopPropagation();
                              setPlayingStepId(item.id);
                              try {
                                await onSendWorkbenchPrompt?.('play_step', { procedure, step: item, workPool: selectedWorkPool });
                              } finally {
                                setPlayingStepId((current) => (current === item.id ? null : current));
                              }
                            }}
                            disabled={isPlaying}
                            style={{
                              background: 'rgba(130,87,229,0.15)', border: '1px solid rgba(130,87,229,0.3)',
                              borderRadius: '8px', padding: '8px 16px', fontSize: '11px', fontWeight: 800,
                              color: '#dccfff', cursor: isPlaying ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: '8px',
                              opacity: isPlaying ? 0.65 : 1
                            }}
                          >
                            <Play size={14} fill="currentColor" />
                            {isPlaying ? 'RUNNING…' : 'DRY RUN STEP'}
                          </button>
                        </div>

                        {isPlaying && (
                           <div style={{
                             background: '#0a0a0c', border: '1px solid rgba(255,255,255,0.06)',
                             borderRadius: '10px', padding: '16px', display: 'flex', flexDirection: 'column', gap: '10px'
                           }}>
                              <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                                 <motion.div
                                   animate={{ scale: [1, 1.2, 1], opacity: [0.6, 1, 0.6] }}
                                   transition={{ repeat: Infinity, duration: 2 }}
                                   style={{ width: '10px', height: '10px', borderRadius: '999px', background: '#8257e5', boxShadow: '0 0 8px #8257e5' }}
                                 />
                                 <span style={{ fontSize: '11px', fontWeight: 800, color: '#8257e5', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Dry Run In Progress...</span>
                              </div>
                              <div style={{ fontSize: '12px', color: '#555', fontStyle: 'italic', lineHeight: 1.5 }}>
                                 This samples the step prompt with the model only. It does not execute the full Strata runtime or create a persistent task.
                              </div>
                           </div>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

const SessionTargetCard = ({ messages, metadata }) => {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', padding: '4px' }}>
       <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
             <div style={{ fontSize: '11px', color: '#555', fontWeight: 800 }}>SESSION CONTEXT</div>
             <div style={{ fontSize: '18px', fontWeight: 700, color: '#edeeef' }}>{metadata?.title || 'Active Session'}</div>
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
             {metadata?.source_kind && <RoutePill label="SOURCE" value={metadata.source_kind} />}
             {metadata?.opened_by && <RoutePill label="OPENED" value={metadata.opened_by} />}
          </div>
       </div>

       {messages?.length > 0 && (
         <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div style={{ fontSize: '11px', color: '#555', fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Recent Thread ({messages.length})</div>
            {messages.map((msg, idx) => {
              const isAssistant = msg.role === 'assistant';
              const isSystem = msg.role === 'system';
              const bg = isAssistant ? 'rgba(130,87,229,0.03)' : (isSystem ? 'rgba(255,184,77,0.03)' : 'transparent');
              const border = isAssistant ? 'rgba(130,87,229,0.1)' : (isSystem ? 'rgba(255,184,77,0.1)' : 'rgba(255,255,255,0.05)');

              return (
                <div key={idx} style={{ padding: '12px', borderRadius: '12px', background: bg, border: `1px solid ${border}` }}>
                   <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                      <span style={{ fontSize: '10px', fontWeight: 800, color: isAssistant ? '#dccfff' : (isSystem ? '#ffd39b' : '#8d8ea1'), textTransform: 'uppercase' }}>{msg.role}</span>
                      <span style={{ fontSize: '10px', color: '#444' }}>{formatAbsoluteTime(msg.created_at)}</span>
                   </div>
                   <div style={{ fontSize: '13px', color: '#e7e8ef', lineHeight: 1.6 }}>
                      {stripInlineMarkdown(msg.content)}
                   </div>
                </div>
              );
            })}
         </div>
       )}
    </div>
  );
};

const WorkbenchView = ({
  currentScope,
  workPoolDetails,
  target,
  activeTasks,
  finishedTasks,
  procedures,
  messages,
  onOpenTask,
  onOpenProcedure,
  onOpenSession,
  onSendWorkbenchPrompt,
  history,
  onInspectTarget,
  apiBase,
  onPauseTask,
  onResumeTask,
  onStopTask,
  onReplayTask,
  onMutateTask,
  onSendMessage,
}) => {
  const [draftPrompt, setDraftPrompt] = useState('');
  const [sendingPrompt, setSendingPrompt] = useState(false);
  const [activeTool, setActiveTool] = useState(null); // 'explain' | 'verify' | 'audit' | 'fix'
  const [stepInspection, setStepInspection] = useState(null);

  // Task 2.1: Consolidated detail fetching via hook
  const { data: detailedTask, loading: loadingDetail, error: fetchError } = useWorkbenchData(target?.taskId, apiBase);

  const allTasks = flattenTaskTree([...(activeTasks || []), ...(finishedTasks || [])]);
  const taskMatchBase = target?.taskId ? allTasks.find((task) => String(task.id) === String(target.taskId)) : null;

  // Merge in detailed data if we have it
  const taskMatch = detailedTask ? {
    ...taskMatchBase,
    ...detailedTask.task,
    attempts: detailedTask.attempts,
    children: detailedTask.children,
    parent: detailedTask.parent,
    observability: detailedTask.observability,
  } : taskMatchBase;

  const procedureMatch = target?.procedureId
    ? (Array.isArray(procedures) ? procedures.map((procedure) => normalizeProcedureRecord(procedure)).find((procedure) => procedure?.procedure_id === target.procedureId) : null)
    : null;
  const workbenchLane = normalizeLaneKey(
    target?.lane
      || taskMatch?.lane
      || currentScope
      || procedureMatch?.target_lane
      || target?.metadata?.lane
  ) || 'agent';
  const defaultWorkbenchPool = workbenchLane === 'trainer'
    ? 'trainer'
    : String(target?.workPool || target?.executionProfile || procedureMatch?.target_work_pool || procedureMatch?.target_execution_profile || metadata?.work_pool || metadata?.execution_profile || 'local_agent');
  const [selectedWorkPool, setSelectedWorkPool] = useState(defaultWorkbenchPool);
  const simulationSessionId = workbenchSimulationSessionId(target);
  const sessionMatch = Array.isArray(messages)
    ? (target?.sessionId
        ? messages.filter((message) => String(message.session_id) === String(target.sessionId) && !shouldHideWorkbenchMessage(message)).slice(-5)
        : messages.filter((message) => message?.message_metadata?.workbench_session === simulationSessionId && !shouldHideWorkbenchMessage(message)).slice(-5))
    : [];
  const metadata = target?.metadata || taskMatch || procedureMatch || null;
  const hasTarget = Boolean(target || taskMatch || procedureMatch);

  useEffect(() => {
    setStepInspection(null);
  }, [target?.taskId, target?.procedureId, target?.sessionId]);

  useEffect(() => {
    setSelectedWorkPool(workbenchLane === 'trainer' ? 'trainer' : defaultWorkbenchPool);
  }, [defaultWorkbenchPool, workbenchLane]);

  const applyWorkbenchAction = (action) => {
    setDraftPrompt(buildWorkbenchPrompt(action, target, taskMatch, procedureMatch));
  };

  const handleSendPrompt = async (overrides = {}) => {
    const promptToSend = overrides.prompt || draftPrompt;
    const normalized = String(promptToSend || '').trim();
    if (!normalized || !onSendWorkbenchPrompt) return;
    setSendingPrompt(true);
    try {
      await onSendWorkbenchPrompt({
        prompt: normalized,
        target: overrides.target || target,
        task: overrides.task || taskMatch,
        procedure: overrides.procedure || procedureMatch,
      });
      if (!overrides.prompt) setDraftPrompt('');
    } finally {
      setSendingPrompt(false);
    }
  };

  const handleContextualAction = async (action, item) => {
    if (action === 'preview_step') {
      try {
        const resp = await fetch(`${apiBase}/admin/workbench/procedures/${item.procedure.procedure_id}/steps/${item.step.id}/preview?lane=${encodeURIComponent(workbenchLane)}&work_pool=${encodeURIComponent(item.workPool || selectedWorkPool)}`);
        const data = await resp.json();
        if (data.status === 'ok') {
          setStepInspection({
            kind: 'preview',
            lane: data.lane || workbenchLane,
            workPool: data.work_pool || item.workPool || selectedWorkPool,
            procedureId: item.procedure.procedure_id,
            procedureTitle: item.procedure.title,
            stepId: item.step.id,
            stepTitle: item.step.title,
            messages: data.messages || [],
            createdAt: new Date().toISOString(),
          });
        }
      } catch (err) {
        console.error('Failed to preview step:', err);
        setStepInspection({
          kind: 'error',
          lane: workbenchLane,
          workPool: item.workPool || selectedWorkPool,
          procedureId: item.procedure.procedure_id,
          procedureTitle: item.procedure.title,
          stepId: item.step.id,
          stepTitle: item.step.title,
          createdAt: new Date().toISOString(),
          error: `Prompt preview failed. ${err?.message || 'Unknown error.'}`,
        });
      }
      return;
    }

    if (action === 'play_step') {
      try {
        const resp = await fetch(`${apiBase}/admin/workbench/procedures/${item.procedure.procedure_id}/steps/${item.step.id}/execute`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lane: workbenchLane, work_pool: item.workPool || selectedWorkPool, execution_profile: item.workPool || selectedWorkPool }),
        });
        const data = await resp.json();
        if (data.status === 'ok') {
          setStepInspection({
            kind: 'dry_run',
            lane: data.lane || workbenchLane,
            workPool: data.work_pool || item.workPool || selectedWorkPool,
            procedureId: item.procedure.procedure_id,
            procedureTitle: item.procedure.title,
            stepId: item.step.id,
            stepTitle: item.step.title,
            createdAt: new Date().toISOString(),
            messages: data.messages || [],
            response: data.response || null,
          });
        } else {
          setStepInspection({
            kind: 'error',
            lane: data.lane || workbenchLane,
            workPool: data.work_pool || item.workPool || selectedWorkPool,
            procedureId: item.procedure.procedure_id,
            procedureTitle: item.procedure.title,
            stepId: item.step.id,
            stepTitle: item.step.title,
            createdAt: new Date().toISOString(),
            messages: data.messages || [],
            error: `Dry run failed. ${data.message || 'Unknown error.'}`,
            response: data.response || null,
          });
        }
      } catch (err) {
        console.error('Failed to play step:', err);
        setStepInspection({
          kind: 'error',
          lane: workbenchLane,
          workPool: item.workPool || selectedWorkPool,
          procedureId: item.procedure.procedure_id,
          procedureTitle: item.procedure.title,
          stepId: item.step.id,
          stepTitle: item.step.title,
          createdAt: new Date().toISOString(),
          error: `Dry run failed. ${err?.message || 'Unknown error.'}`,
        });
      }
      return;
    }

    if (action === 'play_procedure') {
       try {
         const procedureId = item.procedure?.procedure_id || item.procedure_id;
         const resp = await fetch(`${apiBase}/admin/procedures/${procedureId}/queue`, {
           method: 'POST',
           headers: { 'Content-Type': 'application/json' },
           body: JSON.stringify({
             lane: workbenchLane,
             work_pool: item.workPool || selectedWorkPool,
             execution_profile: item.workPool || selectedWorkPool,
             session_id: target?.sessionId || undefined,
           })
         });
         const data = await resp.json();
         if (data.status === 'queued') {
            onOpenTask && onOpenTask(data.task_id);
         }
       } catch (err) {
         console.error('Failed to play procedure:', err);
       }
       return;
    }

    const prompt = buildWorkbenchPrompt(
      action,
      null,
      item?.task || (item?.id ? item : null),
      null, // procedure
      item?.attempt || (item?.attempt_id ? item : null)
    );
    void handleSendPrompt({
      prompt,
      task: item?.task || (item?.task_id ? item : taskMatch),
      target: item?.attempt ? { kind: 'task', taskId: item.attempt.task_id, sessionId: target?.sessionId } : target
    });
  };

  return (
    <div style={{ flex: 1, display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* ── COLUMN 1: INSPECTOR (70%) ────────────────────────────────────────── */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '18px', borderRight: '1px solid rgba(255,255,255,0.05)' }}>
        <DashboardPanel title="WORKBENCH">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <Wrench size={18} color="#8257e5" />
              <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', flex: 1 }}>
                <div style={{ color: '#edeeef', fontSize: '13px', fontWeight: 700 }}>Strata Workbench</div>
                <div style={{ color: '#8d8ea1', fontSize: '11px' }}>
                  Universal process debugger. Click inspection targets to analyze their state, or use selection tools to investigate branches.
                </div>
              </div>
              {taskMatch?.lane && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                   <div style={{ width: '6px', height: '6px', borderRadius: '999px', background: '#00f294' }} />
                   <span style={{ fontSize: '10px', fontWeight: 800, color: '#00f294', letterSpacing: '0.04em' }}>LIVE ON {String(taskMatch.execution_profile || taskMatch.work_pool || taskMatch.lane).toUpperCase()}</span>
                </div>
              )}
              {!taskMatch?.lane && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <div style={{ width: '6px', height: '6px', borderRadius: '999px', background: '#60a5fa' }} />
                  <span style={{ fontSize: '10px', fontWeight: 800, color: '#60a5fa', letterSpacing: '0.04em' }}>SCOPE {String(workbenchLane).toUpperCase()} · {String(selectedWorkPool).toUpperCase()}</span>
                </div>
              )}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
              <RoutePill label="TARGET" value={String(target?.kind || 'none')} tone="neutral" />
              <RoutePill label="PROFILE" value={selectedWorkPool} tone="info" />
              {target?.taskId && <RoutePill label="TASK" value={`#${target.taskId}`} />}
              {target?.procedureId && <RoutePill label="PROCEDURE" value={target.procedureId} tone="success" />}
              {target?.sessionId && <RoutePill label="SESSION" value={String(target.sessionId).slice(0, 8)} />}
            </div>
            {history?.length > 1 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center', borderTop: '1px solid rgba(255,255,255,0.04)', paddingTop: '10px', marginTop: '4px' }}>
                <span style={{ fontSize: '10px', color: '#444', fontWeight: 800, marginRight: '4px', letterSpacing: '0.05em' }}>HISTORY</span>
                {history.map((h, i) => {
                   const isActive = h === target;
                   const label = h.taskId ? `#${h.taskId}` : (h.procedureId ? h.procedureId : (h.sessionId ? `Session ${h.sessionId.slice(0, 4)}` : 'Node'));
                   return (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <button
                        onClick={() => onInspectTarget && onInspectTarget(h)}
                        style={{
                          background: 'transparent', border: 'none',
                          color: isActive ? '#8257e5' : '#666',
                          fontSize: '11px', fontWeight: isActive ? 800 : 400,
                          cursor: 'pointer', textDecoration: isActive ? 'none' : 'underline'
                        }}
                      >
                        {label}
                      </button>
                      {i < history.length - 1 && <span style={{ color: '#222', fontSize: '10px' }}>›</span>}
                    </div>
                   )
                }).slice(0, 8)}
              </div>
            )}
          </div>
        </DashboardPanel>

        <DashboardPanel title="TARGET INSPECTOR">
          {loadingDetail && (
            <div style={{ padding: '20px', textAlign: 'center', color: '#444', fontSize: '12px' }}>Loading detailed context...</div>
          )}
          {fetchError && (
            <div style={{ padding: '20px', textAlign: 'center', color: '#f87171', fontSize: '12px' }}>{fetchError}</div>
          )}
          {taskMatch && (
            <TaskTargetCard
              task={taskMatch}
              onOpenTask={onOpenTask}
              onOpenProcedure={onOpenProcedure}
              onPauseTask={onPauseTask}
              onResumeTask={onResumeTask}
              onStopTask={onStopTask}
              onReplayTask={onReplayTask}
              onMutateTask={onMutateTask}
              activeTool={activeTool}
              onSendWorkbenchPrompt={handleContextualAction}
            />
          )}
          {!taskMatch && procedureMatch && (
            <ProcedureTargetCard
              procedure={procedureMatch}
              workbenchLane={workbenchLane}
              selectedWorkPool={selectedWorkPool}
              onSelectWorkPool={setSelectedWorkPool}
              onSendWorkbenchPrompt={handleContextualAction}
            />
          )}
          {!taskMatch && !procedureMatch && target?.sessionId && <SessionTargetCard messages={sessionMatch} metadata={target.metadata} />}
          {!hasTarget && (
            <div style={{ padding: '60px 40px', textAlign: 'center' }}>
              <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '16px' }}>
                 <Activity size={32} color="#222" />
              </div>
              <div style={{ color: '#8d8ea1', fontSize: '14px', lineHeight: 1.6 }}>
                No explicit target is selected. Use “Open in Workbench” to begin deep inspection of a task or procedure.
              </div>
            </div>
          )}
          {hasTarget && (
            <details style={{ marginTop: '24px', borderTop: '1px solid rgba(255,255,255,0.04)', paddingTop: '16px' }}>
              <summary style={{ fontSize: '10px', color: '#444', fontWeight: 800, cursor: 'pointer', letterSpacing: '0.1em', listStyle: 'none' }}>+ SHOW RAW METADATA</summary>
              <pre style={{ marginTop: '12px', margin: 0, padding: '12px', borderRadius: '12px', background: '#0f1014', border: '1px solid rgba(255,255,255,0.06)', color: '#8d8ea1', fontSize: '11px', lineHeight: 1.6, overflowX: 'auto', fontFamily: "'JetBrains Mono', monospace" }}>
                {stringifyJson(metadata)}
              </pre>
            </details>
          )}
        </DashboardPanel>

        {stepInspection && (
          <DashboardPanel title={stepInspection.kind === 'preview' ? 'STEP PREVIEW' : stepInspection.kind === 'dry_run' ? 'DRY RUN RESULT' : 'STEP ISSUE'}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                <RoutePill label="LANE" value={String(stepInspection.lane || workbenchLane)} tone="info" />
                <RoutePill label="PROFILE" value={String(stepInspection.workPool || selectedWorkPool)} tone="info" />
                {stepInspection.procedureId && <RoutePill label="PROCEDURE" value={stepInspection.procedureId} tone="success" />}
                {stepInspection.stepId && <RoutePill label="STEP" value={stepInspection.stepId} />}
                <RoutePill label="MODE" value={stepInspection.kind === 'dry_run' ? 'dry_run' : stepInspection.kind} tone={stepInspection.kind === 'error' ? 'danger' : 'neutral'} />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <div style={{ color: '#edeeef', fontSize: '18px', fontWeight: 800 }}>{stepInspection.stepTitle || 'Workbench step'}</div>
                <div style={{ color: '#8d8ea1', fontSize: '12px' }}>
                  {stepInspection.procedureTitle || 'Workbench'} · {formatAbsoluteTime(stepInspection.createdAt)}
                </div>
              </div>
              {stepInspection.error && (
                <div style={{ padding: '14px', borderRadius: '12px', background: 'rgba(248,113,113,0.06)', border: '1px solid rgba(248,113,113,0.18)', color: '#fecaca', fontSize: '12px', lineHeight: 1.6 }}>
                  {stepInspection.error}
                </div>
              )}
              {Array.isArray(stepInspection.messages) && stepInspection.messages.length > 0 && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '12px' }}>
                  {stepInspection.messages.map((message, index) => (
                    <div key={`${message.role || 'message'}-${index}`} style={{ borderRadius: '12px', background: '#0f1014', border: '1px solid rgba(255,255,255,0.06)', overflow: 'hidden' }}>
                      <div style={{ padding: '10px 12px', borderBottom: '1px solid rgba(255,255,255,0.05)', color: '#8d8ea1', fontSize: '10px', fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                        {message.role || 'message'}
                      </div>
                      <pre style={{ margin: 0, padding: '14px', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#ececf2', fontSize: '12px', lineHeight: 1.7, fontFamily: "'JetBrains Mono', monospace" }}>
                        {String(message.content || '')}
                      </pre>
                    </div>
                  ))}
                </div>
              )}
              {stepInspection.response && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  <div style={{ borderRadius: '12px', background: '#0f1014', border: '1px solid rgba(255,255,255,0.06)', overflow: 'hidden' }}>
                    <div style={{ padding: '10px 12px', borderBottom: '1px solid rgba(255,255,255,0.05)', color: '#8d8ea1', fontSize: '10px', fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                      Model Output
                    </div>
                    <pre style={{ margin: 0, padding: '14px', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#ececf2', fontSize: '12px', lineHeight: 1.7, fontFamily: "'JetBrains Mono', monospace" }}>
                      {String(stepInspection.response?.content || '(no content)')}
                    </pre>
                  </div>
                  {Array.isArray(stepInspection.response?.tool_calls) && stepInspection.response.tool_calls.length > 0 && (
                    <div style={{ borderRadius: '12px', background: '#0f1014', border: '1px solid rgba(255,255,255,0.06)', overflow: 'hidden' }}>
                      <div style={{ padding: '10px 12px', borderBottom: '1px solid rgba(255,255,255,0.05)', color: '#8d8ea1', fontSize: '10px', fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                        Tool Calls
                      </div>
                      <div style={{ padding: '14px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                        {stepInspection.response.tool_calls.map((toolCall, index) => (
                          <pre key={`${toolCall?.id || toolCall?.function?.name || 'tool'}-${index}`} style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#ececf2', fontSize: '12px', lineHeight: 1.7, fontFamily: "'JetBrains Mono', monospace" }}>
                            {`${toolCall?.function?.name || 'tool'}(${toolCall?.function?.arguments || ''})`}
                          </pre>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </DashboardPanel>
        )}
      </div>

      {/* ── COLUMN 2: ACTION CONSOLE / CHAT (30%) ───────────────────────────────── */}
      <div style={{ width: '400px', display: 'flex', flexDirection: 'column', background: '#08080a' }}>
        <header style={{ padding: '20px 24px', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: '11px', color: '#555', fontWeight: 800, letterSpacing: '0.08em' }}>ACTION CONSOLE</div>
          <div style={{ display: 'flex', gap: '8px' }}>
             {activeTool && (
               <button
                 onClick={() => setActiveTool(null)}
                 style={{ background: 'rgba(248,113,113,0.1)', border: '1px solid rgba(248,113,113,0.2)', color: '#f87171', borderRadius: '999px', padding: '4px 10px', fontSize: '10px', fontWeight: 800, cursor: 'pointer' }}
               >
                 STOP TOOL
               </button>
             )}
          </div>
        </header>

        <div style={{ flex: 1, overflowY: 'auto', padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            {[
              ['explain', 'Explain'],
              ['verify', 'Verify'],
              ['audit', 'Audit'],
              ['fix', 'Fix'],
            ].map(([id, label]) => {
              const isActive = activeTool === id;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => {
                    setActiveTool(isActive ? null : id);
                    if (!isActive) applyWorkbenchAction(id);
                  }}
                  disabled={!hasTarget}
                  style={{
                    ...workbenchActionButtonStyle,
                    background: isActive ? 'rgba(130,87,229,0.2)' : 'rgba(255,255,255,0.04)',
                    borderColor: isActive ? 'rgba(130,87,229,0.3)' : 'rgba(255,255,255,0.08)',
                    color: isActive ? '#dccfff' : '#8f94a7',
                    opacity: hasTarget ? 1 : 0.45,
                    cursor: hasTarget ? 'pointer' : 'default',
                  }}
                >
                  {label}
                </button>
              );
            })}
          </div>

          <div style={{ borderTop: '1px solid rgba(255,255,255,0.05)', padding: '16px 0', flex: 1, display: 'flex', flexDirection: 'column' }}>
            {sessionMatch.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                 {sessionMatch.map((msg, idx) => {
                    const isAssistant = msg.role === 'assistant';
                    const isSystem = msg.role === 'system';
                    const bg = isAssistant ? 'rgba(130,87,229,0.03)' : (isSystem ? 'rgba(255,184,77,0.03)' : 'transparent');

                    return (
                      <div key={idx} style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                           <span style={{ fontSize: '10px', fontWeight: 800, color: isAssistant ? '#8257e5' : '#444', textTransform: 'uppercase' }}>{msg.role}</span>
                           <span style={{ fontSize: '9px', color: '#333' }}>{formatAbsoluteTime(msg.created_at)}</span>
                        </div>
                        <div style={{ fontSize: '12px', color: '#e7e8ef', lineHeight: 1.6, background: bg, padding: '10px', borderRadius: '10px' }}>
                           {stripInlineMarkdown(msg.content)}
                        </div>
                      </div>
                    );
                 })}
              </div>
            ) : (
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#222', fontSize: '12px', textAlign: 'center', padding: '40px' }}>
                No conversational investigation history for this target. Preview and dry-run results appear in the main Workbench pane.
              </div>
            )}
          </div>
        </div>

        <div style={{ padding: '20px', borderTop: '1px solid rgba(255,255,255,0.05)', background: '#0a0a0c' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
            <span style={{ fontSize: '10px', color: '#555', fontWeight: 800 }}>ACTION CONSOLE</span>
            <RoutePill label="MODE" value="thinking" />
          </div>
          <textarea
            value={draftPrompt}
            onChange={(event) => setDraftPrompt(event.target.value)}
            placeholder="Query findings or propose branches..."
            style={{
              width: '100%', minHeight: '80px', borderRadius: '12px', background: '#101116',
              border: '1px solid rgba(255,255,255,0.08)', color: '#ececf2', padding: '12px',
              fontSize: '12px', lineHeight: 1.6, resize: 'none', outline: 'none'
            }}
          />
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '12px' }}>
            <button
              onClick={() => handleSendPrompt()}
              disabled={!draftPrompt.trim() || sendingPrompt}
              style={{
                background: draftPrompt.trim() && !sendingPrompt ? '#8257e5' : 'rgba(255,255,255,0.03)',
                color: draftPrompt.trim() && !sendingPrompt ? 'white' : '#333',
                border: 'none', borderRadius: '999px', padding: '8px 16px', fontSize: '11px',
                fontWeight: 800, cursor: draftPrompt.trim() && !sendingPrompt ? 'pointer' : 'default'
              }}
            >
              {sendingPrompt ? 'Sending…' : 'Send'}
            </button>
          </div>
        </div>
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
  workPoolDetails,
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
  const visibleWorkPools = currentScope === 'trainer'
    ? ['trainer']
    : currentScope === 'agent'
    ? ['local_agent', 'remote_agent']
    : ['trainer', 'local_agent', 'remote_agent'];

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

      <DashboardPanel title="WORK POOLS">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
          {visibleWorkPools.map((poolKey) => {
            const detail = workPoolDetails?.[poolKey] || {};
            const profileLabel = poolKey === 'local_agent' ? 'Local Agent' : poolKey === 'remote_agent' ? 'Remote Agent' : 'Trainer';
            return (
              <div key={poolKey} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: '12px', padding: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', alignItems: 'center' }}>
                  <span style={{ color: '#e7e8ef', fontSize: '12px', fontWeight: 800 }}>{profileLabel}</span>
                  <RoutePill label="MODE" value={detail.activity_label || detail.status || 'Idle'} tone={String(detail.activity_mode || '').toUpperCase().includes('GENERATING') ? 'success' : String(detail.activity_mode || '').toUpperCase().includes('STALLED') || String(detail.activity_mode || '').toUpperCase().includes('OFFLINE') ? 'warning' : 'neutral'} />
                </div>
                <div style={{ color: '#8d8ea1', fontSize: '11px', lineHeight: 1.6 }}>
                  {detail.current_task_title || detail.activity_reason || 'No active task right now.'}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  <RoutePill label="QUEUE" value={String(detail.queue_depth ?? 0)} tone="neutral" />
                  <RoutePill label="HEALTH" value={String(detail.tier_health || 'unknown')} tone={String(detail.tier_health || '').toLowerCase() === 'ok' ? 'success' : String(detail.tier_health || '').toLowerCase() === 'error' ? 'danger' : 'neutral'} />
                  <RoutePill label="HEARTBEAT" value={detail.heartbeat_age_s == null ? '—' : `${Math.round(Number(detail.heartbeat_age_s || 0))}s`} tone="neutral" />
                </div>
              </div>
            );
          })}
        </div>
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
