import React, { memo, useMemo, useState } from 'react';
import axios from 'axios';
import { motion, AnimatePresence } from 'framer-motion';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { GitBranch, FlaskConical, ArchiveX, ChevronDown, ChevronRight, Activity, CheckCircle2, XCircle, Clock, Signal } from 'lucide-react';
import SegmentedProgressRail from './SegmentedProgressRail';

const MotionDiv = motion.div;

const contextButtonStyle = {
  background: 'rgba(255,255,255,0.04)',
  border: '1px solid rgba(255,255,255,0.08)',
  borderRadius: '999px',
  color: '#d7d9e6',
  fontSize: '10px',
  fontWeight: 800,
  letterSpacing: '0.06em',
  padding: '6px 10px',
  cursor: 'pointer',
  textTransform: 'uppercase',
};

const STATUS_MAP = {
  complete:            { bg: 'rgba(0,242,148,0.1)',   color: '#00f294', label: 'Completed',   progress: '100%' },
  working:             { bg: 'rgba(0,217,255,0.1)',   color: '#00d9ff', label: 'Working',     progress: '65%'  },
  blocked:             { bg: 'rgba(255,184,77,0.1)',  color: '#ffb84d', label: 'Blocked',     progress: '30%'  },
  abandoned:           { bg: 'rgba(255,153,0,0.1)',   color: '#ff9900', label: 'Abandoned',   progress: '100%' },
  cancelled:           { bg: 'rgba(148,153,173,0.1)', color: '#9499ad', label: 'Cancelled',   progress: '100%' },
  pushed:              { bg: 'rgba(130,87,229,0.1)',  color: '#8257e5', label: 'Decomposed',  progress: '10%'  },
};
const DEFAULT_STATUS = { bg: 'rgba(148,153,173,0.1)', color: '#9499ad', label: 'Pending', progress: '0%' };

const OUTCOME_MAP = {
  succeeded: { color: '#00f294', Icon: CheckCircle2 },
  failed:    { color: '#ff4d4d', Icon: XCircle },
  cancelled: { color: '#9499ad', Icon: Clock },
};

const TYPE_MAP = {
  research: { color: '#00e5cc', label: 'RESEARCH', Icon: FlaskConical },
};

const TERMINAL_STATUSES = new Set(['complete', 'abandoned', 'cancelled']);

function parseTimestamp(dateString) {
  if (!dateString) return null;
  const raw = String(dateString).trim();
  if (!raw) return null;
  const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/.test(raw);
  const normalized = hasTimezone ? raw : `${raw}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatAbsolute(dateString) {
  if (!dateString) return '—';
  const date = parseTimestamp(dateString);
  if (!date) return '—';
  return date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function formatRelative(dateString) {
  if (!dateString) return 'unknown';
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
}

function formatAbsoluteWithRelative(dateString) {
  if (!dateString) return '—';
  return `${formatAbsolute(dateString)} (${formatRelative(dateString)})`;
}

function formatElapsed(startedAt, endedAt = null) {
  if (!startedAt) return 'unknown';
  const startDate = parseTimestamp(startedAt);
  const endDate = endedAt ? parseTimestamp(endedAt) : null;
  if (!startDate) return 'unknown';
  const start = startDate.getTime();
  const end = endDate ? endDate.getTime() : Date.now();
  const totalSeconds = Math.max(0, Math.floor((end - start) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 1) return `${seconds}s`;
  if (minutes < 60) return `${minutes}m ${seconds}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function stripInlineMarkdown(content) {
  return String(content || '')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/\*(.*?)\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
    .replace(/\n+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function taskStatusProgressFraction(status) {
  switch (String(status || '').trim().toLowerCase()) {
    case 'complete':
    case 'abandoned':
    case 'cancelled':
      return 1;
    case 'working':
      return 0.68;
    case 'blocked':
      return 0.34;
    case 'pending':
      return 0.14;
    case 'pushed':
      return 0.24;
    default:
      return 0.08;
  }
}

function describeTaskProgress(task) {
  if (!task || typeof task !== 'object') {
    return { percent: 0, markers: [] };
  }
  const childSet = Array.isArray(task.children) ? task.children : [];
  if (!childSet.length) {
    return {
      percent: Math.round(taskStatusProgressFraction(task.status) * 100),
      markers: [],
    };
  }
  const markers = childSet.map((child) => {
    const childProgress = describeTaskProgress(child);
    const fallbackPercent = Math.round(taskStatusProgressFraction(child?.status) * 100);
    return {
      status: child?.status,
      percent: Math.max(fallbackPercent, childProgress.percent || 0),
    };
  });
  const total = markers.reduce((sum, marker) => sum + Math.max(0, Math.min(100, Number(marker.percent || 0))), 0);
  const average = markers.length ? Math.round(total / markers.length) : Math.round(taskStatusProgressFraction(task.status) * 100);
  return {
    percent: Math.max(Math.round(taskStatusProgressFraction(task.status) * 100), average),
    markers,
  };
}

function buildTaskContext(task) {
  const constraints = task && typeof task.constraints === 'object' && task.constraints ? task.constraints : {};
  const pendingQuestion = task?.pending_question;
  const procedureId = String(constraints.procedure_id || '').trim();
  const sourceTaskId = String(constraints.source_task_id || task?.parent_id || '').trim();
  const originLane = String(constraints.origin_lane || '').trim();
  const sourceTitle = String(task?.source_title || constraints.source_title || '').trim();
  const itemId = String(constraints.procedure_item_id || '').trim();
  const draftProcedure = String(constraints.draft_procedure_id || '').trim();
  const sourceHints = constraints.source_hints && typeof constraints.source_hints === 'object' ? constraints.source_hints : {};
  const preferredPaths = Array.isArray(sourceHints.preferred_paths) ? sourceHints.preferred_paths.filter(Boolean) : [];
  const route = [];

  if (procedureId) route.push({ label: 'Procedure', value: procedureId, tone: 'procedure' });
  if (itemId) route.push({ label: 'Checklist', value: itemId, tone: 'neutral' });
  if (draftProcedure) route.push({ label: 'Draft', value: draftProcedure, tone: 'neutral' });
  if (originLane) route.push({ label: 'Origin Lane', value: originLane, tone: 'neutral' });
  if (sourceTaskId) route.push({ label: 'Parent', value: sourceTaskId, tone: 'neutral' });
  if (sourceTitle) route.push({ label: 'Source', value: sourceTitle, tone: 'neutral' });
  if (preferredPaths.length) route.push({ label: 'Inspect', value: preferredPaths.slice(0, 2).join(' · '), tone: 'neutral' });
  if (pendingQuestion) route.push({ label: 'Needs You', value: 'operator input', tone: 'warning' });

  return { procedureId, sourceTaskId, route };
}

const ContextPill = ({ label, value, tone = 'neutral' }) => {
  const tones = {
    neutral: { bg: 'rgba(255,255,255,0.04)', border: 'rgba(255,255,255,0.08)', color: '#c8cbda' },
    procedure: { bg: 'rgba(130,87,229,0.14)', border: 'rgba(130,87,229,0.28)', color: '#ece3ff' },
    warning: { bg: 'rgba(255,184,77,0.12)', border: 'rgba(255,184,77,0.26)', color: '#ffe1ad' },
  };
  const style = tones[tone] || tones.neutral;
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: '6px',
      padding: '4px 8px',
      borderRadius: '999px',
      fontSize: '10px',
      lineHeight: 1,
      background: style.bg,
      border: `1px solid ${style.border}`,
      color: style.color,
      maxWidth: '100%',
    }}>
      <span style={{ opacity: 0.72, fontWeight: 800, letterSpacing: '0.05em', textTransform: 'uppercase' }}>{label}</span>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</span>
    </span>
  );
};

const sortAttemptsChronologically = (attempts = []) => (
  [...attempts].sort((left, right) => {
    const leftAt = parseTimestamp(left?.started_at)?.getTime() || 0;
    const rightAt = parseTimestamp(right?.started_at)?.getTime() || 0;
    return leftAt - rightAt;
  })
);

const findActivePathIds = (nodes, targetId) => {
  const normalizedTarget = String(targetId || '').trim();
  if (!normalizedTarget) return new Set();
  const visit = (items) => {
    for (const item of items || []) {
      if (!item) continue;
      if (String(item.id || '') === normalizedTarget) return [String(item.id)];
      const childPath = visit(item.children || []);
      if (childPath.length) return [String(item.id), ...childPath];
    }
    return [];
  };
  return new Set(visit(nodes));
};

const InterventionWidget = ({ taskId, taskSessionId, taskLane, pendingQuestion, onResolve }) => {
  const [input, setInput] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const questionText =
    typeof pendingQuestion === 'string'
      ? pendingQuestion
      : String(pendingQuestion?.question || pendingQuestion?.brief_question || '').trim();
  const answerSessionId = String(pendingQuestion?.session_id || taskSessionId || '').trim();
  const answerQuestionId = String(pendingQuestion?.question_id || '').trim();

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!input.trim()) return;
    setIsSubmitting(true);
    try {
      if (answerQuestionId) {
        await axios.post(`http://localhost:8000/chat`, {
          role: 'user',
          content: input,
          session_id: answerSessionId,
          preferred_tier: taskLane || 'agent',
          answer_question_id: answerQuestionId,
        });
      } else {
        await axios.post(`http://localhost:8000/tasks/${taskId}/intervene`, {
          override: input
        });
      }
      setInput('');
      if (onResolve) onResolve();
      window.location.reload();
    } catch (err) {
      console.error('Intervention failed:', err);
      alert('Failed to submit intervention. Check console.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="intervention-widget" style={{
      background: 'rgba(255,184,77,0.05)',
      border: '1px solid rgba(255,184,77,0.2)',
      borderRadius: '8px',
      padding: '12px',
      marginTop: '8px',
      display: 'flex',
      flexDirection: 'column',
      gap: '8px'
    }}>
      <div style={{ fontSize: '11px', color: '#ffb84d', fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        Human Intervention Required
      </div>
      <div style={{ fontSize: '12px', color: '#ffe2b8', lineHeight: 1.45 }}>
        {questionText || 'Provide the missing context or decision this blocked task needs.'}
      </div>
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: '8px' }}>
        <input 
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Answer this question as specifically as you can..."
          style={{
            flex: 1, background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,119,0,0.2)',
            borderRadius: '6px', padding: '6px 10px', color: '#fff', fontSize: '12px',
            outline: 'none'
          }}
        />
        <button 
          type="submit"
          disabled={isSubmitting}
          style={{
            background: '#ffb84d', color: '#000', border: 'none', borderRadius: '6px',
            padding: '4px 12px', fontSize: '11px', fontWeight: 800, cursor: 'pointer',
            opacity: isSubmitting ? 0.5 : 1
          }}
        >
          {isSubmitting ? '...' : 'SUBMIT'}
        </button>
      </form>
    </div>
  );
};

const StepHistoryPanel = ({ steps, detailLevel = 'compact', defaultExpanded = false }) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const entries = Array.isArray(steps) ? steps.filter(Boolean) : [];
  if (!entries.length) return null;
  const previewCount = detailLevel === 'full' ? Math.min(entries.length, 6) : Math.min(entries.length, 3);
  const preview = entries.slice(-previewCount).reverse();
  const visible = expanded || detailLevel === 'full' ? entries.slice().reverse() : preview;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
        <div style={{ fontSize: '10px', color: '#8b8d9e', fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
          Step History · {entries.length}
        </div>
        {entries.length > previewCount && (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            style={{
              background: 'rgba(255,255,255,0.04)',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: '999px',
              color: '#c7c8d6',
              fontSize: '10px',
              padding: '4px 8px',
              cursor: 'pointer',
            }}
          >
            {expanded ? 'Show Recent' : 'Show All'}
          </button>
        )}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {visible.map((step, index) => (
          <StepHistoryRow key={`${step.at || 'step'}-${index}`} step={step} defaultExpanded={detailLevel === 'full' && index === 0} />
        ))}
      </div>
    </div>
  );
};

const StepHistoryRow = ({ step, defaultExpanded = false }) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const label = String(step?.label || step?.step || 'Step').trim();
  const detail = String(step?.detail || '').trim();
  const at = String(step?.at || '').trim();
  const hasMore = Boolean(detail);
  return (
    <div style={{ borderRadius: '8px', border: '1px solid rgba(255,255,255,0.05)', background: 'rgba(255,255,255,0.02)' }}>
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        style={{
          width: '100%',
          background: 'none',
          border: 'none',
          padding: '8px 10px',
          color: '#d9dbe7',
          cursor: hasMore ? 'pointer' : 'default',
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: '10px',
          textAlign: 'left',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', minWidth: 0 }}>
          <div style={{ fontSize: '11px', fontWeight: 700, color: '#e7e8ef' }}>{label}</div>
          {detail && (
            <div style={{ fontSize: '10px', color: '#8dcfff', fontFamily: "'JetBrains Mono', monospace", whiteSpace: expanded ? 'pre-wrap' : 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {detail}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
          {at && <span style={{ fontSize: '10px', color: '#777' }}>{formatRelative(at)}</span>}
          {hasMore ? (expanded ? <ChevronDown size={12} color="#777" /> : <ChevronRight size={12} color="#777" />) : null}
        </div>
      </button>
      {expanded && detail && (
        <div style={{ padding: '0 10px 10px', fontSize: '10px', color: '#aab0c4', fontFamily: "'JetBrains Mono', monospace", whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {detail}
        </div>
      )}
    </div>
  );
};

const MetadataChip = ({ label, value, accent = '#8dcfff' }) => (
  <div style={{
    display: 'flex',
    flexDirection: 'column',
    gap: '3px',
    padding: '8px 10px',
    borderRadius: '8px',
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid rgba(255,255,255,0.05)',
    minWidth: '120px',
  }}>
    <div style={{ fontSize: '9px', color: '#6f758a', fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
      {label}
    </div>
    <div style={{ fontSize: '11px', color: accent, fontFamily: "'JetBrains Mono', monospace", wordBreak: 'break-word' }}>
      {value}
    </div>
  </div>
);

const StructuredArtifactPanel = ({ artifactDisplay }) => {
  if (!artifactDisplay || typeof artifactDisplay !== 'object') return null;
  const usage = artifactDisplay.usage && typeof artifactDisplay.usage === 'object' ? artifactDisplay.usage : null;
  const usageDetails = usage?.completion_tokens_details && typeof usage.completion_tokens_details === 'object'
    ? usage.completion_tokens_details
    : null;
  const chips = [];
  if (artifactDisplay.provider) chips.push({ label: 'Provider', value: String(artifactDisplay.provider) });
  if (artifactDisplay.model) chips.push({ label: 'Model', value: String(artifactDisplay.model) });
  if (artifactDisplay.job_kind) chips.push({ label: 'Job', value: String(artifactDisplay.job_kind) });
  if (artifactDisplay.duration_s != null) chips.push({ label: 'Duration', value: `${Number(artifactDisplay.duration_s).toFixed(1)}s` });
  if (usage?.prompt_tokens != null) chips.push({ label: 'Prompt Tokens', value: String(usage.prompt_tokens) });
  if (usage?.completion_tokens != null) chips.push({ label: 'Completion Tokens', value: String(usage.completion_tokens) });
  if (usage?.total_tokens != null) chips.push({ label: 'Total Tokens', value: String(usage.total_tokens) });
  if (usageDetails?.reasoning_tokens != null) chips.push({ label: 'Reasoning Tokens', value: String(usageDetails.reasoning_tokens) });

  const leftover = { ...artifactDisplay };
  delete leftover.step_history;
  delete leftover.provider;
  delete leftover.model;
  delete leftover.job_kind;
  delete leftover.duration_s;
  delete leftover.usage;
  const hasLeftover = Object.keys(leftover).length > 0;

  if (!chips.length && !hasLeftover) return null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {chips.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
          {chips.map((chip) => (
            <MetadataChip key={`${chip.label}:${chip.value}`} label={chip.label} value={chip.value} />
          ))}
        </div>
      )}
      {hasLeftover && (
        <div style={{
          fontSize: '10px',
          color: '#8b8d9e',
          fontFamily: "'JetBrains Mono', monospace",
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          background: 'rgba(255,255,255,0.02)',
          border: '1px solid rgba(255,255,255,0.05)',
          borderRadius: '8px',
          padding: '10px 12px',
        }}>
          {JSON.stringify(leftover, null, 2)}
        </div>
      )}
    </div>
  );
};

const TaskCardComponent = ({ task, onArchive, isNested = false, nowMs = Date.now(), laneDetail = null, detailLevel = 'compact', onOpenProcedure, onOpenTask, onOpenWorkbench, activePathIds = new Set() }) => {
  const isOnActivePath = activePathIds?.has?.(String(task.id)) || false;
  const defaultExpanded = useMemo(() => isOnActivePath || !TERMINAL_STATUSES.has(task.status), [task.status, isOnActivePath]);
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  const [descriptionExpanded, setDescriptionExpanded] = useState(false);
  const style = STATUS_MAP[task.status] ?? DEFAULT_STATUS;
  const typeInfo = task.type ? TYPE_MAP[task.type] : null;
  const accentColor = typeInfo ? typeInfo.color : style.color;
  const displayTitle = stripInlineMarkdown(task.title || 'Untitled task') || 'Untitled task';
  const descriptionText = String(task.description || '');
  const longDescription = descriptionText.length > 420 || descriptionText.split('\n').length > 8;
  const progressMeta = useMemo(() => describeTaskProgress(task), [task]);

  const children = Array.isArray(task.children) ? task.children : [];
  const attempts = useMemo(() => sortAttemptsChronologically(Array.isArray(task.attempts) ? task.attempts : []), [task.attempts]);
  const hasChildren = children.length > 0 || attempts.length > 0;
  const taskContext = buildTaskContext(task);
  const compactRoute = taskContext.route.slice(0, isExpanded ? taskContext.route.length : 3);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <MotionDiv
        layout
        initial={{ opacity: 0, y: 10, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        className="task-card"
        onClick={() => setIsExpanded(!isExpanded)}
        style={{
          background: isNested ? '#1a1a20' : '#141418',
          border: '1px solid rgba(255,255,255,0.07)',
          padding: '16px',
          borderRadius: '12px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
          cursor: 'pointer',
          position: 'relative',
          overflow: 'hidden',
          marginLeft: isNested ? '12px' : '0',
          borderLeft: isNested ? `2px solid ${accentColor}44` : '1px solid rgba(255,255,255,0.07)'
        }}
        whileHover={{ borderColor: accentColor, boxShadow: `0 0 0 1px ${accentColor}33` }}
      >
        {!isNested && <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: '1px', background: `linear-gradient(90deg, transparent, ${accentColor}44, transparent)` }} />}

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
            {hasChildren && (
              isExpanded ? <ChevronDown size={14} color="#888" /> : <ChevronRight size={14} color="#888" />
            )}
            {typeInfo && (
              <span style={{
                padding: '2px 6px', borderRadius: '40px', fontSize: '8px',
                fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.08em',
                background: `${typeInfo.color}18`, color: typeInfo.color,
                display: 'flex', alignItems: 'center', gap: '3px'
              }}>
                <typeInfo.Icon size={8} /> {typeInfo.label}
              </span>
            )}
            <span style={{
              padding: '2px 8px', borderRadius: '40px', fontSize: '9px',
              fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.06em',
              background: style.bg, color: style.color
            }}>
              {style.label}
            </span>
          </div>
          
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
             {task.depth > 0 && (
                <div style={{ color: '#555', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '10px', fontFamily: "'JetBrains Mono', monospace" }}>
                  <GitBranch size={11} /> D{task.depth}
                </div>
             )}
             {!isNested && onArchive && (
                <button 
                  onClick={(e) => { e.stopPropagation(); onArchive(); }}
                  style={{ background: 'none', border: 'none', color: '#555', cursor: 'pointer', padding: '2px' }}
                >
                  <ArchiveX size={13} />
                </button>
             )}
          </div>
        </div>

        <div>
          <h3 style={{ fontWeight: 600, fontSize: '14px', color: '#edeeef', lineHeight: 1.3 }}>{displayTitle}</h3>
          {compactRoute.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '8px' }}>
              {compactRoute.map((item) => (
                <ContextPill key={`${item.label}:${item.value}`} label={item.label} value={item.value} tone={item.tone} />
              ))}
            </div>
          )}
          {(isExpanded || !isNested) && task.description && (
            <div>
              <div
                className="markdown-body"
                style={{
                  fontSize: '12px',
                  color: '#6b6b7d',
                  marginTop: '6px',
                  lineHeight: '1.5',
                  maxHeight: longDescription && !descriptionExpanded ? '112px' : 'none',
                  overflow: longDescription && !descriptionExpanded ? 'hidden' : 'visible',
                  position: 'relative',
                }}
              >
                {longDescription && !descriptionExpanded && (
                  <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: '28px', background: 'linear-gradient(180deg, rgba(20,20,24,0), rgba(20,20,24,0.98))', pointerEvents: 'none' }} />
                )}
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {task.description}
                </ReactMarkdown>
              </div>
              {longDescription && (
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    setDescriptionExpanded((value) => !value);
                  }}
                  style={{
                    marginTop: '6px',
                    background: 'none',
                    border: 'none',
                    color: accentColor,
                    fontSize: '10px',
                    fontWeight: 700,
                    letterSpacing: '0.04em',
                    cursor: 'pointer',
                    padding: 0,
                  }}
                >
                  {descriptionExpanded ? 'Show less' : 'Show full description'}
                </button>
              )}
            </div>
          )}
          <div style={{ display: 'flex', gap: '12px', marginTop: '8px', flexWrap: 'wrap', fontSize: '10px', color: '#626275' }}>
            <span title={formatRelative(task.created_at)}>Created {formatAbsoluteWithRelative(task.created_at)}</span>
            <span title={formatRelative(task.updated_at)}>Updated {formatAbsoluteWithRelative(task.updated_at)}</span>
          </div>
        </div>

        {task.status === 'blocked' && (
          <div onClick={(e) => e.stopPropagation()}>
            <InterventionWidget
              taskId={task.id}
              taskSessionId={task.session_id}
              taskLane={task.lane}
              pendingQuestion={task.pending_question}
            />
          </div>
        )}

        <SegmentedProgressRail percent={progressMeta.percent} accentColor={accentColor} segments={progressMeta.markers} compact />
      </MotionDiv>

      <AnimatePresence>
        {isExpanded && (
          <MotionDiv
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column', gap: '8px', paddingLeft: '12px' }}
          >
            {/* Attempts */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
              <div style={{ fontSize: '9px', fontWeight: 800, color: '#444', letterSpacing: '0.1em', marginLeft: '12px' }}>TASK CONTEXT</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {taskContext.procedureId && onOpenProcedure ? (
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onOpenProcedure(taskContext.procedureId);
                    }}
                    style={contextButtonStyle}
                  >
                    Open Procedure
                  </button>
                ) : null}
                {onOpenTask ? (
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onOpenTask(task.id);
                    }}
                    style={contextButtonStyle}
                  >
                    Focus Task
                  </button>
                ) : null}
                {onOpenWorkbench ? (
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onOpenWorkbench({
                        kind: 'task',
                        taskId: task.id,
                        taskTitle: displayTitle,
                        lane: task.lane,
                        procedureId: taskContext.procedureId || '',
                        parentTaskId: task.parent_id || '',
                        sessionId: task.session_id || '',
                        detailLevel,
                      });
                    }}
                    style={contextButtonStyle}
                  >
                    Open in Workbench
                  </button>
                ) : null}
              </div>
            </div>
            {taskContext.route.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginLeft: '12px' }}>
                {taskContext.route.map((item) => (
                  <ContextPill key={`${task.id}-${item.label}-${item.value}`} label={item.label} value={item.value} tone={item.tone} />
                ))}
              </div>
            )}

            {attempts.length > 0 && (
               <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                <div style={{ fontSize: '9px', fontWeight: 800, color: '#444', letterSpacing: '0.1em', marginLeft: '12px' }}>ATTEMPTS</div>
                {attempts.map((attempt, idx) => (
                  <AttemptRow
                    key={attempt.id || attempt.attempt_id || idx}
                    attempt={attempt}
                    taskId={task.id}
                    index={idx + 1}
                    totalAttempts={attempts.length}
                    taskUpdatedAt={task.updated_at}
                    defaultExpanded={idx === attempts.length - 1 || (!attempt.outcome && idx >= attempts.length - 2)}
                    nowMs={nowMs}
                    hasNewerAttempt={idx < attempts.length - 1}
                    laneDetail={laneDetail}
                    detailLevel={detailLevel}
                  />
                ))}
               </div>
            )}

            {children.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <div style={{ fontSize: '9px', fontWeight: 800, color: '#444', letterSpacing: '0.1em', marginLeft: '12px' }}>SUBTASKS</div>
                {children.map((child) => (
                  <TaskCard
                    key={child.id}
                    task={child}
                    onArchive={onArchive}
                    isNested={true}
                    nowMs={nowMs}
                    laneDetail={laneDetail}
                    detailLevel={detailLevel}
                    onOpenProcedure={onOpenProcedure}
                    onOpenTask={onOpenTask}
                    onOpenWorkbench={onOpenWorkbench}
                    activePathIds={activePathIds}
                  />
                ))}
              </div>
            )}
          </MotionDiv>
        )}
      </AnimatePresence>
    </div>
  );
};

const AttemptRow = ({ attempt, taskId, index, totalAttempts, taskUpdatedAt, defaultExpanded = false, nowMs, hasNewerAttempt = false, laneDetail = null, detailLevel = 'compact' }) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const outcome = OUTCOME_MAP[attempt.outcome] || { color: '#555', Icon: Activity };
  const hasOpenAttempt = !attempt.ended_at && !attempt.outcome;
  const lastActivityAt = hasNewerAttempt ? attempt.started_at : (taskUpdatedAt || attempt.started_at);
  const parsedLastActivityAt = parseTimestamp(lastActivityAt);
  const recentlyActive = parsedLastActivityAt ? (nowMs - parsedLastActivityAt.getTime()) < 90000 : false;
  const parsedStartedAt = parseTimestamp(attempt.started_at);
  const supersededOpenAttempt = hasOpenAttempt && hasNewerAttempt;
  const staleOpenAttempt = supersededOpenAttempt || (
    hasOpenAttempt && !recentlyActive && parsedStartedAt
      ? (nowMs - parsedStartedAt.getTime()) > 300000
      : false
  );
  const isActive = hasOpenAttempt && !staleOpenAttempt && !hasNewerAttempt;
  const liveAttemptMatch = isActive
    && laneDetail
    && String(laneDetail.current_task_id || '') === String(taskId || '')
    && String(laneDetail.active_attempt_id || '') === String(attempt.id || '');
  const displayAttemptNumber = Math.max(1, (Number(totalAttempts) || 0) - index + 1);
  const artifacts = attempt.artifacts && typeof attempt.artifacts === 'object' ? attempt.artifacts : null;
  const artifactDisplay = artifacts ? { ...artifacts } : null;
  const persistedSteps = Array.isArray(artifacts?.step_history) ? artifacts.step_history : [];
  if (artifactDisplay && Array.isArray(artifactDisplay.step_history)) {
    delete artifactDisplay.step_history;
  }
  const liveSteps = liveAttemptMatch && Array.isArray(laneDetail?.recent_steps) ? laneDetail.recent_steps : [];
  const stepHistory = liveSteps.length ? liveSteps : persistedSteps;
  const summaryBits = [];
  if (artifacts?.job_kind) summaryBits.push(`job ${artifacts.job_kind}`);
  if (artifacts?.duration_s) summaryBits.push(`${Number(artifacts.duration_s).toFixed(1)}s`);
  if (attempt.resolution) summaryBits.push(`resolution ${attempt.resolution.replace(/_/g, ' ')}`);
  if (artifacts?.provider || artifacts?.model) summaryBits.push(`${artifacts.provider || 'model'} / ${artifacts.model || 'unknown'}`);
  if (attempt.plan_review?.plan_health) summaryBits.push(`plan ${attempt.plan_review.plan_health}`);
  if (attempt.plan_review?.recommendation) summaryBits.push(`review ${String(attempt.plan_review.recommendation).replace(/_/g, ' ')}`);
  if (liveAttemptMatch && laneDetail?.step_label) summaryBits.push(`live ${String(laneDetail.step_label).toLowerCase()}`);
  const isFailedAttempt = attempt.outcome === 'failed' || staleOpenAttempt;
  const collapseableFailedAttempt = isFailedAttempt && index > 1;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginLeft: '12px' }}>
      <button
        onClick={() => setExpanded(!expanded)}
        style={{ 
          background: isActive ? 'rgba(0,217,255,0.08)' : staleOpenAttempt ? 'rgba(255,184,77,0.08)' : 'rgba(255,255,255,0.02)', 
          border: isActive ? '1px solid rgba(0,217,255,0.2)' : staleOpenAttempt ? '1px solid rgba(255,184,77,0.18)' : '1px solid rgba(255,255,255,0.05)', 
          borderRadius: '10px', 
          padding: '12px 14px',
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          borderLeft: `3px solid ${isActive ? '#00d9ff' : staleOpenAttempt ? '#ffb84d' : `${outcome.color}66`}`,
          textAlign: 'left',
          width: '100%',
          cursor: 'pointer'
        }}
      >
        <div style={{ fontSize: '10px', fontWeight: 800, color: '#666', fontFamily: "'JetBrains Mono', monospace", minWidth: '24px' }}>
          A{displayAttemptNumber}
        </div>
        {isActive ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', minWidth: '28px' }}>
            <MotionDiv
              animate={{ opacity: [0.4, 1, 0.4], scale: [0.96, 1.08, 0.96] }}
              transition={{ duration: 1.4, repeat: Infinity }}
              style={{ width: '8px', height: '8px', borderRadius: '999px', background: recentlyActive ? '#00f294' : '#ffb84d', boxShadow: `0 0 10px ${recentlyActive ? '#00f29455' : '#ffb84d55'}` }}
            />
            <Signal size={12} color={recentlyActive ? '#00f294' : '#ffb84d'} />
          </div>
        ) : staleOpenAttempt ? (
          <Clock size={12} color="#ffb84d" />
        ) : (
          <outcome.Icon size={12} color={outcome.color} />
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
            <div style={{ fontSize: '11px', color: isActive ? '#d8f8ff' : staleOpenAttempt ? '#ffd89a' : '#888', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              {isActive ? 'Attempt Active' : supersededOpenAttempt ? 'Attempt Superseded' : staleOpenAttempt ? 'Attempt Stale' : `Attempt ${attempt.outcome || 'Pending'}`}
            </div>
            <div style={{ fontSize: '9px', color: '#555' }}>
              {formatAbsolute(attempt.started_at)}
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', marginTop: '3px', flexWrap: 'wrap' }}>
            <div style={{ fontSize: '10px', color: isActive ? '#86dfff' : staleOpenAttempt ? '#ffd89a' : '#666' }}>
              {isActive ? `Running for ${formatElapsed(attempt.started_at)}` : staleOpenAttempt ? `Open for ${formatElapsed(attempt.started_at)}` : `Ran for ${formatElapsed(attempt.started_at, attempt.ended_at)}`}
            </div>
            <div style={{ fontSize: '10px', color: isActive ? (recentlyActive ? '#00f294' : '#777') : staleOpenAttempt ? '#ffb84d' : '#777' }}>
              {isActive
                ? (liveAttemptMatch && laneDetail?.step_label
                  ? `${laneDetail.step_label}${laneDetail.progress_label ? ` · ${laneDetail.progress_label}` : ''}`
                  : (recentlyActive ? 'actively updating' : 'no recent heartbeat'))
                : supersededOpenAttempt ? 'newer attempt exists' : staleOpenAttempt ? 'stale open attempt' : `ended ${formatRelative(attempt.ended_at)}`}
            </div>
          </div>
          {liveAttemptMatch && (laneDetail?.step_detail || laneDetail?.step_updated_at) && (
            <div style={{ fontSize: '10px', color: '#8ddfff', marginTop: '4px', fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {[laneDetail?.step_detail, laneDetail?.step_updated_at ? `updated ${formatRelative(laneDetail.step_updated_at)}` : ''].filter(Boolean).join(' · ')}
            </div>
          )}
          {summaryBits.length > 0 && (
            <div style={{ fontSize: '10px', color: '#666', marginTop: '4px', fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {summaryBits.join(' · ')}
            </div>
          )}
          {collapseableFailedAttempt && !expanded && (
            <div style={{ fontSize: '10px', color: '#8a8d9c', marginTop: '4px' }}>
              Older failed attempt; expand for details
            </div>
          )}
        </div>
        {expanded ? <ChevronDown size={13} color="#777" /> : <ChevronRight size={13} color="#777" />}
      </button>
      <AnimatePresence>
        {expanded && (
          <MotionDiv
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            style={{ overflow: 'hidden' }}
          >
            <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '8px', padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', fontSize: '10px', color: '#777' }}>
                <span title={formatRelative(attempt.started_at)}>Started {formatAbsoluteWithRelative(attempt.started_at)}</span>
                <span title={formatRelative(lastActivityAt)}>Last task update {formatAbsoluteWithRelative(lastActivityAt)}</span>
                {attempt.ended_at && <span title={formatRelative(attempt.ended_at)}>Ended {formatAbsoluteWithRelative(attempt.ended_at)}</span>}
              </div>
              {attempt.reason && (
                <div style={{ fontSize: '11px', color: '#a8a8b5', lineHeight: 1.45 }}>
                  {attempt.reason}
                </div>
              )}
              {attempt.plan_review && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', fontSize: '10px', color: '#8b8d9e' }}>
                  <span>Plan health: {attempt.plan_review.plan_health || '—'}</span>
                  <span>Recommendation: {String(attempt.plan_review.recommendation || '—').replace(/_/g, ' ')}</span>
                  <span>Confidence: {attempt.plan_review.confidence ?? '—'}</span>
                </div>
              )}
              <StepHistoryPanel
                steps={stepHistory}
                detailLevel={detailLevel}
                defaultExpanded={detailLevel === 'full' && Boolean(stepHistory.length)}
              />
              <StructuredArtifactPanel artifactDisplay={artifactDisplay} />
              {!attempt.reason && (!artifactDisplay || Object.keys(artifactDisplay).length === 0) && !stepHistory.length && (
                <div style={{ fontSize: '10px', color: '#666' }}>
                  No detailed trace captured yet.
                </div>
              )}
            </div>
          </MotionDiv>
        )}
      </AnimatePresence>
    </div>
  );
};

const TaskCard = memo(TaskCardComponent, (prev, next) => (
  prev.task === next.task &&
  prev.isNested === next.isNested &&
  prev.nowMs === next.nowMs &&
  prev.laneDetail === next.laneDetail &&
  prev.detailLevel === next.detailLevel &&
  prev.activePathIds === next.activePathIds &&
  prev.onOpenProcedure === next.onOpenProcedure &&
  prev.onOpenTask === next.onOpenTask &&
  prev.onOpenWorkbench === next.onOpenWorkbench
));

export default TaskCard;
