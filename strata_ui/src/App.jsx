import React, { Suspense, lazy, memo, startTransition, useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Plus, Zap,
  MessageSquare, Send,
  Terminal, AlertCircle, X, Settings,
  Activity, Trash2, LayoutDashboard, History,
  Pause, Play, Square, Pencil, Paperclip,
  BookOpen, Search, ThumbsUp, ThumbsDown, Heart, Reply, Sparkles, GitBranch, Wrench
} from 'lucide-react';

const MotionDiv = motion.div;
const MotionSpan = motion.span;
const APP_VERSION = typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : '0.0.0';
const APP_CHANNEL = typeof __APP_CHANNEL__ === 'string' ? __APP_CHANNEL__ : 'dev';
const NonChatContent = lazy(() => import('./views/NonChatContent'));
const LazySettingsView = lazy(() => import('./views/SettingsView'));
const MarkdownMessageBody = lazy(() => import('./components/MarkdownMessageBody'));
const TaskPaneContent = lazy(() => import('./views/TaskPaneContent'));
const preloadNonChatContent = () => import('./views/NonChatContent');
const preloadSettingsView = () => import('./views/SettingsView');

const PROVIDER_FRIENDLY_NAMES = {
  google: 'Google',
  openrouter: 'OpenRouter',
  lmstudio: 'LM Studio',
  cerebras: 'Cerebras',
};

const TRANSPORT_FRIENDLY_NAMES = {
  cloud: 'Cloud',
  local: 'Local',
};

const CHAT_LANES = ['trainer', 'agent'];

const defaultSessionIdForLane = (lane) => `${lane}:default`;
const draftSessionIdForLane = (lane) => `${lane}:draft-${Date.now()}`;
const isDraftSessionId = (sessionId) => typeof sessionId === 'string' && /^(trainer|agent):draft-\d+$/.test(sessionId);
const isDesktopRuntime = () =>
  typeof window !== 'undefined' &&
  Object.prototype.hasOwnProperty.call(window, '__TAURI_INTERNALS__');
const draftHasContent = (draft) => {
  if (!draft) return false;
  const title = String(draft.title || '').trim();
  const body = String(draft.draftMessage || '').trim();
  const attachments = Array.isArray(draft.attachments) ? draft.attachments : [];
  return Boolean(body) || attachments.length > 0 || (Boolean(title) && title !== 'New Session');
};

const persistedSessionHasContent = (session) => {
  if (!session || typeof session !== 'object') return false;
  if (Number(session.message_count || 0) > 0) return true;
  if (session.first_message_at || session.last_message_at) return true;
  const preview = String(session.last_message_preview || '').trim();
  if (preview && preview.toLowerCase() !== 'no messages yet') return true;
  return false;
};

const normalizeLaneKey = (value) => {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'trainer' || normalized === 'strong') return 'trainer';
  if (normalized === 'agent' || normalized === 'weak') return 'agent';
  return '';
};

const laneForSessionId = (sessionId) => {
  if (typeof sessionId !== 'string') return 'trainer';
  if (sessionId.startsWith('agent:')) return 'agent';
  if (sessionId.startsWith('trainer:')) return 'trainer';
  if (sessionId.startsWith('weak:')) return 'agent';
  if (sessionId.startsWith('strong:')) return 'trainer';
  return 'trainer';
};

const explicitLaneForSessionId = (sessionId) => {
  if (typeof sessionId !== 'string') return null;
  if (sessionId.startsWith('agent:')) return 'agent';
  if (sessionId.startsWith('trainer:')) return 'trainer';
  if (sessionId.startsWith('weak:')) return 'agent';
  if (sessionId.startsWith('strong:')) return 'trainer';
  return null;
};

const sessionMatchesLane = (sessionId, lane) => laneForSessionId(sessionId) === lane;

const laneForTask = (task) => {
  const explicitLane = normalizeLaneKey(task?.lane);
  if (explicitLane) return explicitLane;
  return laneForSessionId(task?.session_id);
};

const normalizeLaneStatusMap = (raw) => {
  const source = raw && typeof raw === 'object' ? raw : {};
  return {
    trainer: source.trainer || source.strong || 'IDLE',
    agent: source.agent || source.weak || 'IDLE',
  };
};

const normalizeTierStatusMap = (raw) => {
  const source = raw && typeof raw === 'object' ? raw : {};
  return {
    trainer: source.trainer || source.Trainer || source.strong || source.Strong || 'unknown',
    agent: source.agent || source.Agent || source.weak || source.Weak || 'unknown',
  };
};

const defaultLaneDetail = {
  status: 'IDLE',
  tier_health: 'unknown',
  activity_mode: 'IDLE',
  activity_label: 'Idle',
  activity_reason: '',
  queue_depth: 0,
  runnable_count: 0,
  blocked_count: 0,
  needs_you_count: 0,
  paused_task_count: 0,
  current_task_id: null,
  current_task_title: '',
  current_task_state: null,
  current_task_updated_at: null,
  active_attempt_id: null,
  active_attempt_started_at: null,
  step: '',
  step_label: '',
  step_detail: '',
  step_updated_at: null,
  progress_label: '',
  recent_steps: [],
  ticker_items: [],
  heartbeat_state: 'unknown',
  heartbeat_age_s: null,
};

const normalizeLaneDetail = (raw) => {
  const source = raw && typeof raw === 'object' ? raw : {};
  return { ...defaultLaneDetail, ...source };
};

const normalizeLaneDetailMap = (raw) => {
  const source = raw && typeof raw === 'object' ? raw : {};
  return {
    trainer: normalizeLaneDetail(source.trainer || source.strong),
    agent: normalizeLaneDetail(source.agent || source.weak),
  };
};

const normalizeRoutingSummary = (raw) => {
  const source = raw && typeof raw === 'object' ? raw : null;
  if (!source) return null;
  return {
    ...source,
    trainer: source.trainer || source.strong || null,
    agent: source.agent || source.weak || null,
    chat: source.chat || source.trainer || source.strong || null,
  };
};

const deriveThrottleMode = (settings) => {
  const policy = settings && typeof settings === 'object'
    ? (settings.inference_throttle_policy || {})
    : {};
  const comfort = policy && typeof policy === 'object'
    ? (policy.operator_comfort || {})
    : {};
  const throttleMode = String(policy.throttle_mode || 'hard').trim().toLowerCase();
  const comfortProfile = String(comfort.profile || 'quiet').trim().toLowerCase();
  return throttleMode === 'greedy' || comfortProfile === 'aggressive' ? 'turbo' : 'quiet';
};

const buildThrottleSettingsPayload = (mode) => {
  if (mode === 'turbo') {
    return {
      inference_throttle_policy: {
        throttle_mode: 'greedy',
        operator_comfort: {
          profile: 'aggressive',
          ambiguity_bias: 'prefer_action',
          allow_annoying_if_explicit: true,
        },
      },
    };
  }
  return {
    inference_throttle_policy: {
      throttle_mode: 'hard',
      operator_comfort: {
        profile: 'quiet',
        ambiguity_bias: 'prefer_quiet',
        allow_annoying_if_explicit: false,
      },
    },
  };
};

const fallbackSessionTitle = (sessionId) => {
  const visibleSessionId = displaySessionId(sessionId);
  if (!visibleSessionId || visibleSessionId === 'default') return 'New Session';
  const ts = parseInt(visibleSessionId.replace('session-', ''), 10);
  if (isNaN(ts)) return visibleSessionId;
  return new Date(ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};

const displaySessionId = (sessionId) => {
  if (typeof sessionId !== 'string') return 'default';
  if (sessionId.startsWith('agent:')) return sessionId.slice('agent:'.length);
  if (sessionId.startsWith('trainer:')) return sessionId.slice('trainer:'.length);
  return sessionId;
};

const parseTimestamp = (dateString) => {
  if (!dateString) return null;
  const raw = String(dateString).trim();
  if (!raw) return null;
  const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/.test(raw);
  const normalized = hasTimezone ? raw : `${raw}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
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

const formatAbsoluteTime = (dateString) => {
  if (!dateString) return '—';
  const date = parseTimestamp(dateString);
  if (!date) return '—';
  return date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};

const formatAbsoluteWithRelative = (dateString) => {
  if (!dateString) return '—';
  return `${formatAbsoluteTime(dateString)} (${formatRelativeTime(dateString)})`;
};

const formatHeartbeatAge = (seconds) => {
  const numeric = Number(seconds);
  if (!Number.isFinite(numeric) || numeric < 0) return '—';
  if (numeric < 60) return `${Math.round(numeric)}s`;
  if (numeric < 3600) return `${Math.round(numeric / 60)}m`;
  return `${Math.round(numeric / 3600)}h`;
};

const buildTaskTree = (sourceTasks = []) => {
  const map = {};
  sourceTasks.forEach((task) => {
    map[task.id] = { ...task, children: [] };
  });

  const roots = [];
  sourceTasks.forEach((task) => {
    if (task.parent_id && map[task.parent_id] && task.parent_id !== task.id) {
      map[task.parent_id].children.push(map[task.id]);
    } else if (!task.parent_id || task.parent_id === task.id) {
      roots.push(map[task.id]);
    }
  });

  roots.sort((a, b) => String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || '')));
  return roots;
};

const formatLaneHeartbeat = (detail) => {
  if (!detail) return 'no signal';
  if (detail.heartbeat_age_s == null) {
    if (detail.activity_mode === 'GENERATING') return 'starting';
    return 'no heartbeat';
  }
  return `${detail.heartbeat_state} · ${formatHeartbeatAge(detail.heartbeat_age_s)} ago`;
};

const formatMessageForDisplay = (content) => {
  const raw = String(content || '');
  const normalized = raw.replace(/([.!?])\s+\*?#\s+/g, '$1\n\n# ').replace(/\n\*?#\s+/g, '\n# ');
  const headingIndex = normalized.search(/\n#\s+/);
  if (headingIndex <= 0) {
    return { lead: '', body: normalized };
  }
  return {
    lead: normalized.slice(0, headingIndex).trim(),
    body: normalized.slice(headingIndex + 1).trim(),
  };
};

const createDraftRecord = (sessionId, draftMessage = '', overrides = {}) => ({
  session_id: sessionId,
  title: 'New Session',
  draft: true,
  draftMessage,
  unread_count: 0,
  created_at: new Date().toISOString(),
  last_message_at: null,
  last_message_preview: draftMessage.trim() || 'Draft',
  session_metadata: {
    participant_names: { user: 'You', trainer: 'Trainer', agent: 'Agent', system: 'System' },
  },
  ...overrides,
});

const formatBytes = (bytes) => {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return '';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
};

const fileToBase64 = (file) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => {
    const result = String(reader.result || '');
    const [, base64 = ''] = result.split(',', 2);
    resolve(base64);
  };
  reader.onerror = () => reject(reader.error || new Error('Failed to read attachment.'));
  reader.readAsDataURL(file);
});

const createPendingAttachmentPlaceholder = (file) => ({
  id: `attachment-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  title: file.name || 'Attachment',
  name: file.name || 'Attachment',
  media_type: file.type || 'application/octet-stream',
  size_bytes: Number(file.size || 0),
  description: 'Preparing attachment',
  status: 'preparing',
  progress: 0.12,
});

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

const REACTION_OPTIONS = [
  { key: 'thumbs_up', label: 'Helpful', icon: ThumbsUp },
  { key: 'thumbs_down', label: 'Needs Work', icon: ThumbsDown },
  { key: 'heart', label: 'Loved It', icon: Heart },
  { key: 'emphasis', label: 'Important', icon: Zap },
  { key: 'confused', label: 'Confusing', icon: AlertCircle },
];

const LANE_ACCENTS = {
  trainer: {
    bubbleBg: 'linear-gradient(135deg, rgba(205,96,52,0.94), rgba(152,51,39,0.92))',
    bubbleBorder: 'rgba(255,163,112,0.38)',
    bubbleShadow: '0 12px 28px rgba(129,57,35,0.28)',
    chip: '#ffd7c3',
  },
  agent: {
    bubbleBg: 'linear-gradient(135deg, rgba(93,131,137,0.92), rgba(63,112,103,0.92))',
    bubbleBorder: 'rgba(140,196,188,0.3)',
    bubbleShadow: '0 12px 28px rgba(36,76,73,0.24)',
    chip: '#d2f1ea',
  },
  user: {
    bubbleBg: '#15161b',
    bubbleBorder: 'rgba(255,255,255,0.08)',
    bubbleShadow: 'none',
    chip: '#c9ccd8',
  },
};

const LANE_THEME = {
  trainer: {
    label: 'Trainer',
    chipText: '#ffd7c3',
    chipBg: 'rgba(205,96,52,0.12)',
    chipBorder: 'rgba(255,163,112,0.24)',
    glow: 'rgba(205,96,52,0.16)',
    title: '#ffd7c3',
    preview: 'rgba(255,215,195,0.72)',
    activeTitle: '#fff1e8',
  },
  agent: {
    label: 'Agent',
    chipText: '#d2f1ea',
    chipBg: 'rgba(93,131,137,0.14)',
    chipBorder: 'rgba(140,196,188,0.24)',
    glow: 'rgba(93,131,137,0.16)',
    title: '#d2f1ea',
    preview: 'rgba(210,241,234,0.72)',
    activeTitle: '#ebfaf6',
  },
};

const titleCaseWords = (value) => String(value || '')
  .split(/[_\s]+/)
  .filter(Boolean)
  .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
  .join(' ');

const friendlyProviderLabel = (provider) => {
  const normalized = String(provider || '').trim().toLowerCase();
  return PROVIDER_FRIENDLY_NAMES[normalized] || titleCaseWords(normalized || provider || '');
};

const friendlyTransportLabel = (transport) => {
  const normalized = String(transport || '').trim().toLowerCase();
  return TRANSPORT_FRIENDLY_NAMES[normalized] || titleCaseWords(normalized || transport || '');
};

const friendlyModelLabel = (model) => {
  const raw = String(model || '').trim();
  if (!raw) return '';
  if (raw.length <= 32) return raw;
  const compact = raw
    .replace(/^mlx-+/i, '')
    .replace(/^openai\//i, '')
    .replace(/^anthropic\//i, '')
    .replace(/^google\//i, '')
    .replace(/-reasoning(?:-[a-z0-9]+)*$/i, '')
    .replace(/-thinking(?:-[a-z0-9]+)*$/i, '')
    .replace(/-(instruction|instruct|distilled)$/i, '');
  return compact.length <= 32 ? compact : `${compact.slice(0, 29)}...`;
};

const normalizeCommunicativeAct = (message) => String(message?.message_metadata?.communicative_act || '').trim().toLowerCase();

const isDirectCommunication = (message) => {
  if (message?.role === 'user') return true;
  if (message?.role === 'assistant') {
    const sourceKind = String(message?.message_metadata?.source_kind || '').trim().toLowerCase();
    if (!sourceKind || sourceKind === 'chat_reply' || sourceKind === 'chat_error' || sourceKind === 'tool_progress') {
      return true;
    }
  }
  const act = normalizeCommunicativeAct(message);
  return act === 'response' || act === 'question';
};

const eventAlignmentForMessage = (message) => {
  const metadata = message?.message_metadata || {};
  const sourceKind = String(metadata.source_kind || '').trim().toLowerCase();
  const sourceActor = String(metadata.source_actor || '').trim().toLowerCase();
  if (sourceKind === 'feedback_event' || sourceActor === 'user_opened' || sourceActor === 'message_feedback') {
    return 'right';
  }
  if (sourceKind.includes('tool') || sourceKind.includes('task_') || sourceActor === 'task_runner' || sourceActor === 'chat_runtime') {
    return 'left';
  }
  return 'center';
};

const getMessageLane = (message, fallbackLane) => {
  if (message?.role === 'user') return 'user';
  const explicitLane = normalizeLaneKey(message?.message_metadata?.lane);
  if (explicitLane) return explicitLane;
  const sessionLane = explicitLaneForSessionId(message?.session_id);
  if (sessionLane) return sessionLane;
  return fallbackLane === 'agent' ? 'agent' : 'trainer';
};

const participantLabel = (value, participantNames = {}) => {
  const normalized = String(value || '').trim().toLowerCase();
  if (!normalized) return '';
  if (normalized === 'user') return participantNames.user || 'you';
  if (normalized === 'system') return participantNames.system || 'system';
  if (normalized === 'trainer') return participantNames.trainer || 'Trainer';
  if (normalized === 'agent') return participantNames.agent || 'Agent';
  if (normalized === 'chat_runtime') return participantNames.system || 'Strata';
  return value;
};

const audienceRecipients = (audience) => {
  if (Array.isArray(audience)) {
    return audience.map((item) => String(item || '').trim()).filter(Boolean);
  }
  const raw = String(audience || '').trim();
  if (!raw) return [];
  if (raw.includes(',')) {
    return raw.split(',').map((item) => item.trim()).filter(Boolean);
  }
  return [raw];
};

const allRecipientsSatisfied = (expectedRecipients, receipts, receiptField) => {
  if (!expectedRecipients.length) return false;
  const receiptSet = new Set(
    receipts
      .map((item) => String(item?.[receiptField] || '').trim().toLowerCase())
      .filter(Boolean)
  );
  return expectedRecipients.every((recipient) => receiptSet.has(String(recipient || '').trim().toLowerCase()));
};

const formatMessageLifecycle = (message, participantNames = {}) => {
  const metadata = message?.message_metadata || {};
  const readReceipts = Array.isArray(metadata.read_receipts) ? metadata.read_receipts : [];
  const seenReceipts = Array.isArray(metadata.seen_receipts) ? metadata.seen_receipts : [];
  const deliveryRecords = Array.isArray(metadata.delivery_records) ? metadata.delivery_records : [];
  const expectedRecipients = audienceRecipients(metadata.audience);
  if (message?.role === 'user') {
    const latestRead = readReceipts[readReceipts.length - 1];
    if (latestRead?.reader) {
      return allRecipientsSatisfied(expectedRecipients, readReceipts, 'reader')
        ? 'Read'
        : `Read by ${participantLabel(latestRead.reader, participantNames)}`;
    }
    const latestSeen = seenReceipts[seenReceipts.length - 1];
    if (latestSeen?.actor) return `Seen by ${participantLabel(latestSeen.actor, participantNames)}`;
    if (message?.pending) return 'Sending';
    if (message?.failed) return 'Send failed';
    return 'Queued';
  }
  const latestRead = readReceipts[readReceipts.length - 1];
  if (latestRead?.reader) {
    return allRecipientsSatisfied(expectedRecipients, readReceipts, 'reader')
      ? 'Read'
      : `Read by ${participantLabel(latestRead.reader, participantNames)}`;
  }
  const latestDelivery = deliveryRecords[deliveryRecords.length - 1];
  if (latestDelivery?.recipient) {
    return allRecipientsSatisfied(expectedRecipients, deliveryRecords, 'recipient')
      ? 'Delivered'
      : `Delivered to ${participantLabel(latestDelivery.recipient, participantNames)}`;
  }
  if (message?.pending) return 'Sending';
  return 'Sent';
};

const describeEventMessage = (message) => {
  const metadata = message?.message_metadata || {};
  const sourceKind = String(metadata.source_kind || message?.role || 'event').trim();
  const tags = Array.isArray(metadata.tags) ? metadata.tags.filter(Boolean) : [];
  const label = titleCaseWords(sourceKind);
  return {
    label,
    tags: tags.slice(0, 3),
  };
};

const messageSenderKey = (message, lane) => {
  if (message?.role === 'user') return 'user';
  if (isDirectCommunication(message)) return `agent:${getMessageLane(message, lane)}`;
  return `system:${eventAlignmentForMessage(message)}`;
};

const messageSenderTitle = (message, lane, participantNames = {}) => {
  if (message?.role === 'user') return participantNames?.user || 'You';
  if (isDirectCommunication(message)) {
    const messageLane = getMessageLane(message, lane);
    if (messageLane === 'agent') return participantNames?.agent || 'Agent';
    return participantNames?.trainer || 'Trainer';
  }
  return participantNames?.system || 'System';
};

const shouldGroupMessages = (currentMessage, previousMessage, lane) => {
  if (!currentMessage || !previousMessage) return false;
  if (messageSenderKey(currentMessage, lane) !== messageSenderKey(previousMessage, lane)) return false;
  const currentTime = parseTimestamp(currentMessage.created_at);
  const previousTime = parseTimestamp(previousMessage.created_at);
  if (!currentTime || !previousTime) return false;
  return Math.abs(currentTime.getTime() - previousTime.getTime()) <= 6 * 60 * 60 * 1000;
};

const ReactionMenu = ({
  message,
  busyKey,
  onReact,
  onTypedResponse,
  onClose,
}) => {
  const counts = message?.reactions?.counts || {};
  const viewerReactions = message?.reactions?.viewer_reactions || [];
  return (
    <div
      style={{
        position: 'absolute',
        top: 'calc(100% + 8px)',
        right: 0,
        width: '220px',
        background: '#13141a',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: '14px',
        boxShadow: '0 18px 34px rgba(0,0,0,0.32)',
        padding: '10px',
        zIndex: 20,
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
      }}
    >
      <div style={{ fontSize: '10px', color: '#7e8293', fontWeight: 800, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
        React
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '8px' }}>
        {REACTION_OPTIONS.map(({ key, label, icon: Icon }) => {
          const active = viewerReactions.includes(key);
          const busy = busyKey === `${message.id}:${key}`;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onReact(message.id, key)}
              disabled={busy}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                background: active ? 'rgba(130,87,229,0.16)' : 'rgba(255,255,255,0.03)',
                border: active ? '1px solid rgba(130,87,229,0.34)' : '1px solid rgba(255,255,255,0.07)',
                borderRadius: '10px',
                padding: '8px 10px',
                color: active ? '#e2d7ff' : '#c0c3d3',
                fontSize: '11px',
                fontWeight: 700,
                cursor: busy ? 'default' : 'pointer',
                opacity: busy ? 0.7 : 1,
              }}
            >
              <Icon size={12} />
              <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {counts[key] > 0 ? `${label} · ${counts[key]}` : label}
              </span>
            </button>
          );
        })}
      </div>
      <button
        type="button"
        onClick={() => onTypedResponse(message)}
        style={{
          background: 'rgba(255,255,255,0.04)',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: '10px',
          padding: '9px 10px',
          color: '#d4d7e4',
          fontSize: '11px',
          fontWeight: 700,
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        Add typed response…
      </button>
      <button
        type="button"
        onClick={onClose}
        style={{
          background: 'none',
          border: 'none',
          color: '#7f8394',
          fontSize: '10px',
          fontWeight: 800,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          cursor: 'pointer',
          alignSelf: 'flex-end',
        }}
      >
        Close
      </button>
    </div>
  );
};

const MessageActionPill = ({
  message,
  onReact,
  onReply,
}) => (
  <MotionDiv
    initial={{ opacity: 0, y: 4, scale: 0.98 }}
    animate={{ opacity: 1, y: 0, scale: 1 }}
    exit={{ opacity: 0, y: 4, scale: 0.98 }}
    transition={{ duration: 0.16, ease: 'easeOut' }}
    style={{
      display: 'inline-flex',
      alignItems: 'center',
      background: 'rgba(10,10,12,0.96)',
      border: '1px solid rgba(255,255,255,0.09)',
      borderRadius: '999px',
      boxShadow: '0 10px 22px rgba(0,0,0,0.28)',
      overflow: 'hidden',
    }}
  >
    <button
      type="button"
      onClick={() => onReact(message.id)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        background: 'transparent',
        border: 'none',
        color: '#afb4c6',
        cursor: 'pointer',
        padding: '8px 10px',
      }}
    >
      <Sparkles size={12} />
    </button>
    <div style={{ width: '1px', alignSelf: 'stretch', background: 'rgba(255,255,255,0.09)' }} />
    <button
      type="button"
      onClick={() => onReply(message)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        background: 'transparent',
        border: 'none',
        color: '#afb4c6',
        cursor: 'pointer',
        padding: '8px 10px',
      }}
    >
      <Reply size={12} />
    </button>
  </MotionDiv>
);

const MessageMetaRow = ({ message, lane, participantNames }) => {
  const metadata = message?.message_metadata || {};
  const direct = isDirectCommunication(message);
  const laneAccent = LANE_ACCENTS[getMessageLane(message, lane)] || LANE_ACCENTS.trainer;
  const timestamp = formatAbsoluteTime(message.created_at);
  const lifecycle = formatMessageLifecycle(message, participantNames);
  const isUser = message?.role === 'user';
  const eventAlignedRight = !direct && eventAlignmentForMessage(message) === 'right';
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '10px',
        flexWrap: 'wrap',
        marginTop: '8px',
        fontSize: '10px',
        color: '#707487',
        paddingLeft: direct && message.role !== 'user' ? '4px' : 0,
      }}
    >
      {isUser || !direct ? (
        <>
          <span style={{ color: direct ? laneAccent.chip : '#98a0b4', fontWeight: 700 }}>{lifecycle}</span>
          <span>{timestamp}</span>
        </>
      ) : (
        <>
          <span>{timestamp}</span>
          <span style={{ color: direct ? laneAccent.chip : '#98a0b4', fontWeight: 700 }}>{lifecycle}</span>
        </>
      )}
    </div>
  );
};

const AttachmentPill = ({ attachment, onRemove = null, compact = false }) => {
  const label = String(attachment?.title || attachment?.name || 'Attachment');
  const description = String(attachment?.description || formatBytes(attachment?.size_bytes) || '').trim();
  const status = String(attachment?.status || '').trim().toLowerCase();
  const progress = Math.max(0, Math.min(1, Number(attachment?.progress || 0)));
  const accent = status === 'error'
    ? '#ff8f8f'
    : status === 'ready'
    ? '#a8ffe4'
    : '#b9c1d9';
  return (
    <div
      style={{
        position: 'relative',
        display: 'inline-flex',
        alignItems: 'center',
        gap: '8px',
        maxWidth: compact ? '220px' : '320px',
        background: 'rgba(255,255,255,0.04)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: '999px',
        padding: compact ? '6px 10px' : '7px 12px',
        overflow: 'hidden',
      }}
    >
      {status && status !== 'ready' && (
        <div
          style={{
            position: 'absolute',
            left: 0,
            bottom: 0,
            height: '2px',
            width: `${progress * 100}%`,
            background: status === 'error' ? '#ff6b6b' : 'linear-gradient(90deg, #8b5cf6, #60a5fa)',
          }}
        />
      )}
      <Paperclip size={12} color="#9ca1b4" />
      <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: '#e7e8ef', fontSize: '11px', fontWeight: 700 }}>
        {label}
      </span>
      {description && (
        <span style={{ color: accent, fontSize: '10px', whiteSpace: 'nowrap', flexShrink: 0 }}>
          {description}
        </span>
      )}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          style={{ background: 'none', border: 'none', color: '#8b90a5', cursor: 'pointer', display: 'flex', padding: 0, marginLeft: '2px' }}
        >
          <X size={12} />
        </button>
      )}
    </div>
  );
};

const MessageCard = ({
  message,
  lane,
  reactionBusyKey,
  openReactionMenuId,
  onOpenReactionMenu,
  onCloseReactionMenu,
  onReact,
  onReply,
  onTypedResponse,
  showSenderTitle,
  participantNames,
}) => {
  const [hovered, setHovered] = useState(false);
  const display = formatMessageForDisplay(message.content);
  const direct = isDirectCommunication(message);
  const laneKey = getMessageLane(message, lane);
  const laneAccent = LANE_ACCENTS[laneKey] || LANE_ACCENTS.trainer;
  const eventDescriptor = describeEventMessage(message);
  const showReactionButton = message.role !== 'user' && !message.pending && !message.failed;
  const reactionMenuOpen = openReactionMenuId === message.id;
  const eventAlignment = eventAlignmentForMessage(message);
  const attachments = Array.isArray(message?.message_metadata?.attachments) ? message.message_metadata.attachments : [];
  const alignSelf = direct
    ? (message.role === 'user' ? 'flex-end' : 'flex-start')
    : (eventAlignment === 'right' ? 'flex-end' : eventAlignment === 'center' ? 'center' : 'flex-start');
  const eventWidth = eventAlignment === 'center' ? '72%' : '68%';

  return (
    <MotionDiv
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        alignSelf,
        maxWidth: direct ? '78%' : eventWidth,
        width: direct ? 'auto' : eventWidth,
        position: 'relative',
      }}
    >
      {showSenderTitle && (
        <div
          style={{
            fontSize: '10px',
            fontWeight: 800,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: direct ? laneAccent.chip : '#8fa2c4',
            marginBottom: '8px',
            paddingLeft: message.role === 'user' ? 0 : '4px',
            textAlign: direct
              ? (message.role === 'user' ? 'right' : 'left')
              : (eventAlignment === 'right' ? 'right' : eventAlignment === 'center' ? 'center' : 'left'),
          }}
        >
          {messageSenderTitle(message, lane, participantNames)}
        </div>
      )}
      <div
        style={{
          background: direct
            ? laneAccent.bubbleBg
            : 'transparent',
          padding: direct ? '14px 18px' : '2px 0 0',
          borderRadius: direct
            ? message.role === 'user'
              ? '16px 16px 4px 16px'
              : '16px 16px 16px 4px'
            : '0',
          border: message.is_intervention
            ? '1px solid rgba(255,77,77,0.3)'
            : direct
            ? `1px solid ${laneAccent.bubbleBorder}`
            : 'none',
          color: direct ? '#f7f8fc' : '#e7e8ef',
          boxShadow: direct ? laneAccent.bubbleShadow : 'none',
          position: 'relative',
        }}
      >
        {!direct && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px', flexWrap: 'wrap' }}>
            {eventDescriptor.tags.map((tag) => (
              <span key={tag} style={{ fontSize: '9px', color: '#9aa0b5', borderRadius: '999px', padding: '2px 6px', background: 'rgba(255,255,255,0.03)' }}>
                {tag}
              </span>
            ))}
          </div>
        )}
        {message.is_intervention && (
          <div style={{ color: '#ff4d4d', fontSize: '10px', fontWeight: 800, marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '4px', letterSpacing: '0.08em' }}>
            <AlertCircle size={12} /> ACTION REQUIRED
          </div>
        )}
        <div className="markdown-body" style={{ fontSize: '14px', lineHeight: '1.65' }}>
          {display.lead && (
            <div style={{ fontSize: '13px', color: direct ? 'rgba(255,255,255,0.92)' : '#e8e9f2', fontWeight: 600, marginBottom: '10px' }}>
              {display.lead}
            </div>
          )}
          <Suspense fallback={<div style={{ whiteSpace: 'pre-wrap' }}>{display.body}</div>}>
            <MarkdownMessageBody content={display.body} />
          </Suspense>
        </div>
        {attachments.length > 0 && (
          <div style={{ marginTop: '12px', display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            {attachments.map((attachment, index) => (
              <AttachmentPill
                key={attachment.id || attachment.storage_key || `${message.id}-attachment-${index}`}
                attachment={attachment}
                compact
              />
            ))}
          </div>
        )}
        {(message.pending || message.failed) && (
          <div style={{ marginTop: '8px', fontSize: '10px', fontWeight: 700, letterSpacing: '0.06em', color: message.failed ? 'rgba(255,230,230,0.92)' : 'rgba(255,255,255,0.78)' }}>
            {message.failed ? 'SEND FAILED' : 'SENDING'}
          </div>
        )}
        <AnimatePresence>
        {showReactionButton && hovered && direct && (
          <div
            style={{
              position: 'absolute',
              right: '18px',
              bottom: '-18px',
              zIndex: 10,
            }}
          >
            <MessageActionPill
              message={message}
              onReact={(messageId) => onOpenReactionMenu(reactionMenuOpen ? null : messageId)}
              onReply={onReply}
            />
            {reactionMenuOpen && (
              <ReactionMenu
                message={message}
                busyKey={reactionBusyKey}
                onReact={onReact}
                onTypedResponse={onTypedResponse}
                onClose={() => onCloseReactionMenu()}
              />
            )}
          </div>
        )}
        </AnimatePresence>
      </div>
      <MessageMetaRow message={message} lane={lane} participantNames={participantNames} />
      <AnimatePresence>
      {showReactionButton && hovered && !direct && (
        <div
          style={{
            position: 'absolute',
            display: 'flex',
            justifyContent: 'flex-end',
            right: 0,
            top: '100%',
            marginTop: '-20px',
            zIndex: 10,
          }}
        >
          <MessageActionPill
            message={message}
            onReact={(messageId) => onOpenReactionMenu(reactionMenuOpen ? null : messageId)}
            onReply={onReply}
          />
          {reactionMenuOpen && (
            <ReactionMenu
              message={message}
              busyKey={reactionBusyKey}
              onReact={onReact}
              onTypedResponse={onTypedResponse}
              onClose={() => onCloseReactionMenu()}
            />
          )}
        </div>
      )}
      </AnimatePresence>
    </MotionDiv>
  );
};

const MemoMessageCard = memo(MessageCard);


// ─── Session History Pane ──────────────────────────────────────────────────────
const HistoryPane = ({ sessionList, sessionId, setSessionId, deleteSession, renameSession, onNewSession, showLaneBadge = false }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', padding: '8px 0' }}>
    <button
      type="button"
      onClick={onNewSession}
      style={{
        margin: '0 8px 6px',
        padding: '12px 14px',
        borderRadius: '12px',
        border: '1px solid rgba(130,87,229,0.22)',
        background: 'rgba(130,87,229,0.08)',
        color: '#b693ff',
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
        fontSize: '14px',
        fontWeight: 700,
        cursor: 'pointer',
        textAlign: 'left',
      }}
    >
      <Plus size={16} />
      <span>New Session</span>
    </button>
    {sessionList.length === 0 && (
      <div style={{ textAlign: 'center', color: '#555', fontSize: '13px', padding: '32px 16px' }}>
        No sessions yet
      </div>
    )}
    {sessionList.map(session => (
      <SessionRow
        key={session.session_id}
        session={session}
        active={sessionId === session.session_id}
        showLaneBadge={showLaneBadge}
        onClick={() => setSessionId(session.session_id)}
        onDelete={() => deleteSession(session.session_id)}
        onRename={() => renameSession(session)}
      />
    ))}
  </div>
);

const SessionRow = ({ session, active, onClick, onDelete, onRename, showLaneBadge = false }) => {
  const [hovered, setHovered] = useState(false);
  const baseLabel = session.title || fallbackSessionTitle(session.session_id);
  const unreadCount = Number(session.unread_count || 0);
  const sourceKind = String(session.session_metadata?.opened_by || session.session_metadata?.source_kind || '').trim();
  const autonomous = sourceKind && sourceKind !== 'user_opened' && sourceKind !== 'user';
  const isDraft = Boolean(session?.draft) || isDraftSessionId(session?.session_id);
  const sessionTime = isDraft ? session.created_at : session.last_message_at;
  const lane = laneForSessionId(session?.session_id);
  const laneTheme = LANE_THEME[lane] || LANE_THEME.trainer;
  const laneBadge = { label: laneTheme.label, color: laneTheme.chipText, bg: laneTheme.chipBg, border: laneTheme.chipBorder };
  const iconBg = active ? laneBadge.bg : laneTheme.chipBg;
  const iconBorder = `1px solid ${laneBadge.border}`;
  const titleColor = active ? laneTheme.activeTitle : laneTheme.title;
  const previewColor = active ? 'rgba(233,240,255,0.82)' : laneTheme.preview;

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        padding: '10px 12px', borderRadius: '8px',
        background: active ? 'rgba(255,255,255,0.03)' : hovered ? 'rgba(255,255,255,0.02)' : 'transparent',
        cursor: 'pointer', display: 'flex', alignItems: 'center',
        justifyContent: 'space-between',
        color: active ? laneTheme.title : '#888',
        fontSize: '13px', transition: 'all 0.15s'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', overflow: 'hidden' }}>
        <span
          style={{
            width: '22px',
            height: '22px',
            borderRadius: '999px',
            background: iconBg,
            border: iconBorder,
            color: laneBadge.color,
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: unreadCount > 0 ? '9px' : '0',
            fontWeight: 800,
            flexShrink: 0,
            position: 'relative',
            boxShadow: active ? `0 0 14px ${laneTheme.glow}` : 'none',
          }}
        >
          {unreadCount > 0 ? (
            unreadCount > 99 ? '99+' : unreadCount
          ) : (
            <MessageSquare size={12} style={{ color: laneBadge.color, opacity: 0.95 }} />
          )}
        </span>
        <div style={{ overflow: 'hidden' }}>
          <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: '6px' }}>
            {showLaneBadge ? (
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                <span style={{ color: laneBadge.color, fontWeight: 700 }}>{laneBadge.label}</span>
                <span style={{ color: titleColor }}>:</span>
                <span style={{ color: titleColor }}> {baseLabel}</span>
              </span>
            ) : (
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: titleColor }}>{baseLabel}</span>
            )}
            {autonomous && (
              <span style={{ fontSize: '9px', fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase', color: laneTheme.chipText, background: laneTheme.chipBg, border: `1px solid ${laneTheme.chipBorder}`, borderRadius: '999px', padding: '2px 5px', flexShrink: 0 }}>
                Auto
              </span>
            )}
          </div>
          <div style={{ fontSize: '10px', color: previewColor, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {session.last_message_preview || 'No messages yet'}
          </div>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
        <span title={formatAbsoluteTime(sessionTime)} style={{ fontSize: '10px', color: '#666' }}>
          {formatRelativeTime(sessionTime)}
        </span>
        {hovered && (
          <button
            onClick={e => { e.stopPropagation(); onRename(); }}
            title="Rename session"
            style={{ background: 'none', border: 'none', color: '#8d90a3', cursor: 'pointer', padding: '2px', display: 'flex', opacity: 0.8, flexShrink: 0 }}
          >
            <Pencil size={12} />
          </button>
        )}
        {hovered && (
          <button
            onClick={e => { e.stopPropagation(); onDelete(); }}
            style={{ background: 'none', border: 'none', color: '#ff4d4d', cursor: 'pointer', padding: '2px', display: 'flex', opacity: 0.7, flexShrink: 0 }}
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>
    </div>
  );
};

// ─── App ──────────────────────────────────────────────────────────────────────
function App() {
  const [messages, setMessages]       = useState([]);
  const [tasks, setTasks]             = useState([]);
  const [inputText, setInputText]     = useState('');
  const [laneDrafts, setLaneDrafts]   = useState({ trainer: [], agent: [] });
  const [isSending, setIsSending]     = useState(false);
  const [sendError, setSendError]     = useState('');
  const [responseMode, setResponseMode] = useState('thinking');
  const [reactionBusyKey, setReactionBusyKey] = useState('');
  const [openReactionMenuId, setOpenReactionMenuId] = useState('');
  const [replyTarget, setReplyTarget] = useState(null);
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [chatLane, setChatLane]       = useState('trainer');
  const [currentScope, setCurrentScope] = useState('trainer');
  const [scopeSessionIds, setScopeSessionIds] = useState({
    home: null,
    trainer: defaultSessionIdForLane('trainer'),
    agent: defaultSessionIdForLane('agent'),
  });
  const [sessionList, setSessionList] = useState([]);
  const [activeNav, setActiveNav]     = useState('chat');   // 'chat' | 'tasks' | 'history' | 'knowledge' | 'procedures' | 'workbench' | 'dashboard' | 'settings'
  const [apiStatus, setApiStatus]     = useState('connecting'); // 'ok' | 'error' | 'connecting'
  const [workerApiStatus, setWorkerApiStatus] = useState('connecting'); // 'ok' | 'error' | 'connecting'
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const attachmentInputRef = useRef(null);
  const sessionListRef = useRef([]);
  const isSendingRef = useRef(false);
  const fetchGenRef = useRef(0);       // generation counter for stale-poll rejection
  const fetchPromiseRef = useRef(null);
  const pendingFetchRef = useRef(false);
  const pendingDataRefreshRef = useRef(false);
  const workerStatusPromiseRef = useRef(null);
  const pendingWorkerStatusRef = useRef(false);
  const refreshTimerRef = useRef(null);
  const [activityNowMs, setActivityNowMs] = useState(() => Date.now());
  const [workerStatus, setWorkerStatus] = useState('RUNNING'); // RUNNING, PAUSED, STOPPED
  const [laneStatuses, setLaneStatuses] = useState({ trainer: 'IDLE', agent: 'IDLE' });
  const [laneDetails, setLaneDetails] = useState(normalizeLaneDetailMap(null));
  const [globalPaused, setGlobalPaused] = useState(false);
  const [pausedLanes, setPausedLanes] = useState([]);
  const [rebooting, setRebooting] = useState(false);
  const [reconnectingBackend, setReconnectingBackend] = useState(false);
  const [desiredGlobalEnabled, setDesiredGlobalEnabled] = useState(true);
  const [desiredLaneEnabled, setDesiredLaneEnabled] = useState({ trainer: true, agent: true });
  const [telemetry, setTelemetry] = useState(null);
  const [providerTelemetry, setProviderTelemetry] = useState({});
  const [dashboard, setDashboard] = useState(null);
  const [loadedContext, setLoadedContext] = useState({ files: [], budget_tokens: 0 });
  const [routingSummary, setRoutingSummary] = useState(null);
  const [specsSnapshot, setSpecsSnapshot] = useState(null);
  const [specProposalSnapshot, setSpecProposalSnapshot] = useState([]);
  const [knowledgePagesSnapshot, setKnowledgePagesSnapshot] = useState([]);
  const [knowledgeSources, setKnowledgeSources] = useState([]);
  const [knowledgeQuery, setKnowledgeQuery] = useState('');
  const [knowledgePages, setKnowledgePages] = useState([]);
  const [selectedKnowledgeSlug, setSelectedKnowledgeSlug] = useState('');
  const [selectedKnowledgePage, setSelectedKnowledgePage] = useState(null);
  const [selectedKnowledgeSourcePath, setSelectedKnowledgeSourcePath] = useState('');
  const [selectedKnowledgeSource, setSelectedKnowledgeSource] = useState(null);
  const [procedures, setProcedures] = useState([]);
  const [selectedProcedureId, setSelectedProcedureId] = useState('');
  const [workbenchTarget, setWorkbenchTarget] = useState(null);
  const [workbenchHistory, setWorkbenchHistory] = useState([]);
  const [selectedProcedure, setSelectedProcedure] = useState(null);
  const [retentionSnapshot, setRetentionSnapshot] = useState(null);
  const [variantRatingsSnapshot, setVariantRatingsSnapshot] = useState(null);
  const [predictionTrustSnapshot, setPredictionTrustSnapshot] = useState(null);
  const [proposalConfigSnapshot, setProposalConfigSnapshot] = useState(null);
  const [evalJobsSnapshot, setEvalJobsSnapshot] = useState([]);
  const [operatorNotice, setOperatorNotice] = useState('');
  const [throttleMode, setThrottleMode] = useState('quiet');
  const [throttleModeSaving, setThrottleModeSaving] = useState(false);
  const [showFinishedTasks, setShowFinishedTasks] = useState(false);
  const [editingSessionTitle, setEditingSessionTitle] = useState(false);
  const [sessionTitleDraft, setSessionTitleDraft] = useState('');
  const [titleHovered, setTitleHovered] = useState(false);
  const API = 'http://localhost:8000';

  const [archivedTasks, setArchivedTasks] = useState(() => {
    try { return JSON.parse(localStorage.getItem('archivedTasks') || '[]'); } catch { return []; }
  });

  const handleArchiveTask = useCallback((taskId) => {
    setArchivedTasks(prev => {
      const next = [...prev, taskId];
      localStorage.setItem('archivedTasks', JSON.stringify(next));
      return next;
    });
  }, []);

  const [tiers, setTiers] = useState({ trainer: 'unknown', agent: 'unknown' });
  const [showCloudModal, setShowCloudModal] = useState(false);
  const sessionId = scopeSessionIds[currentScope] || (currentScope === 'home' ? null : defaultSessionIdForLane(currentScope));
  const effectiveLane = currentScope === 'home'
    ? (sessionId ? laneForSessionId(sessionId) : chatLane)
    : currentScope;
  const isDraftSession = isDraftSessionId(sessionId);
  const currentDraft = Object.values(laneDrafts).flat().find((draft) => draft?.session_id === sessionId) || null;
  const activeChatRoute = routingSummary?.[effectiveLane] || routingSummary?.chat || null;
  const currentSession = currentDraft || sessionList.find((session) => session.session_id === sessionId) || null;
  const currentSessionMetadata = currentSession?.session_metadata || {};
  const visibleSessionId = displaySessionId(sessionId);
  const sessionLabel = currentSession?.title || currentDraft?.title || fallbackSessionTitle(sessionId);
  const sessionMetaLabel = isDraftSession
    ? `${effectiveLane.toUpperCase()} draft session`
    : visibleSessionId === 'default'
    ? `${effectiveLane.toUpperCase()} default session`
    : currentScope === 'home'
    ? `GLOBAL · ${effectiveLane.toUpperCase()} · ${visibleSessionId}`
    : `${effectiveLane.toUpperCase()} · ${visibleSessionId}`;
  const suggestedSessionTitle = currentSessionMetadata?.recommended_title && currentSessionMetadata?.recommended_title !== sessionLabel
    ? currentSessionMetadata.recommended_title
    : '';
  const participantNames = {
    user: currentSessionMetadata?.participant_names?.user || 'You',
    trainer: currentSessionMetadata?.participant_names?.trainer === 'Trainer-agent'
      ? 'Trainer'
      : (currentSessionMetadata?.participant_names?.trainer || 'Trainer'),
    agent: currentSessionMetadata?.participant_names?.agent || 'Agent',
    system: currentSessionMetadata?.participant_names?.system || 'System',
  };
  const replyTargetSender = replyTarget ? messageSenderTitle(replyTarget, effectiveLane, participantNames) : '';
  const showSessionPane = activeNav === 'chat';
  const showTaskPane = activeNav === 'chat' || activeNav === 'dashboard';
  const showSpecBanner = activeNav === 'chat';

  const upsertLaneDraft = useCallback((lane, sessionIdToUpsert, updater) => {
    setLaneDrafts((prev) => {
      const drafts = prev[lane] || [];
      const existingIndex = drafts.findIndex((draft) => draft?.session_id === sessionIdToUpsert);
      const current = existingIndex >= 0 ? drafts[existingIndex] : null;
      const nextDraft = updater(current);
      if (!nextDraft) return prev;
      if (existingIndex >= 0) {
        const nextDrafts = drafts.slice();
        nextDrafts[existingIndex] = nextDraft;
        return { ...prev, [lane]: nextDrafts };
      }
      return { ...prev, [lane]: [nextDraft, ...drafts] };
    });
  }, []);

  const materializeDraftIfNeeded = useCallback((lane, draftSessionId, draftMessage = '', overrides = {}) => {
    let created = false;
    upsertLaneDraft(lane, draftSessionId, (existing) => {
      const nextMessage = draftMessage != null ? draftMessage : (existing?.draftMessage || '');
      const nextDraft = existing
        ? {
            ...existing,
            ...overrides,
            draftMessage: nextMessage,
            attachments: overrides.attachments ?? existing?.attachments ?? [],
            last_message_preview: nextMessage.trim() || existing?.last_message_preview || 'Draft',
          }
        : createDraftRecord(draftSessionId, nextMessage, {
            ...overrides,
            attachments: overrides.attachments ?? [],
          });
      if (!existing) created = true;
      return nextDraft;
    });
    return created;
  }, [upsertLaneDraft]);

  const handleInspectWorkbenchTarget = useCallback((target) => {
    if (!target) return;
    setWorkbenchHistory(prev => {
      // Don't add duplicate top entries
      if (prev[0]?.taskId === target.taskId && 
          prev[0]?.procedureId === target.procedureId && 
          prev[0]?.sessionId === target.sessionId) return prev;
      return [target, ...prev].slice(0, 10);
    });
    setWorkbenchTarget(target);
    if (target?.procedureId) {
      setSelectedProcedureId(target.procedureId);
    }
    setActiveNav('workbench');
  }, []);

  useEffect(() => {
    sessionListRef.current = sessionList;
  }, [sessionList]);

  useEffect(() => {
    if (workerApiStatus !== 'ok') return;
    setDesiredGlobalEnabled(workerStatus !== 'PAUSED' && workerStatus !== 'STOPPED');
    setDesiredLaneEnabled({
      trainer: !pausedLanes.includes('trainer'),
      agent: !pausedLanes.includes('agent'),
    });
  }, [pausedLanes, workerApiStatus, workerStatus]);

  useEffect(() => {
    setOpenReactionMenuId('');
  }, [messages, sessionId]);

  useEffect(() => {
    if (isDraftSession) {
      setInputText(currentDraft?.draftMessage || '');
      setPendingAttachments(Array.isArray(currentDraft?.attachments) ? currentDraft.attachments : []);
      setMessages([]);
      setSendError('');
      return;
    }
    setInputText('');
    setPendingAttachments([]);
  }, [currentDraft?.draftMessage, isDraftSession, sessionId]);

  useEffect(() => {
    if (replyTarget) {
      inputRef.current?.focus();
    }
  }, [replyTarget]);

  const fetchWorkerStatus = useCallback(async () => {
    if (workerStatusPromiseRef.current) {
      pendingWorkerStatusRef.current = true;
      return workerStatusPromiseRef.current;
    }

    const request = (async () => {
      try {
        const res = await axios.get(`${API}/admin/worker/status`);
        setWorkerApiStatus('ok');
        setWorkerStatus(res.data.status.worker);
        setGlobalPaused(Boolean(res.data.status.global_paused));
        setPausedLanes(Array.isArray(res.data.status.paused_lanes) ? res.data.status.paused_lanes.map(normalizeLaneKey).filter(Boolean) : []);
        const nextLaneStatuses = normalizeLaneStatusMap(res.data.status.lanes);
        const nextLaneDetails = normalizeLaneDetailMap(res.data.status.lane_details);
        const nextTiers = normalizeTierStatusMap(res.data.status.tiers);
        setLaneStatuses(nextLaneStatuses);
        setLaneDetails(nextLaneDetails);
        setTiers(nextTiers);
        if (nextTiers.trainer === 'error' && !localStorage.getItem('skipCloudWarning')) {
          setShowCloudModal(true);
        }
      } catch (e) {
        setWorkerApiStatus('error');
        console.error('Failed to fetch worker status', e);
      } finally {
        workerStatusPromiseRef.current = null;
        if (pendingWorkerStatusRef.current) {
          pendingWorkerStatusRef.current = false;
          window.setTimeout(() => {
            void fetchWorkerStatus();
          }, 0);
        }
      }
    })();

    workerStatusPromiseRef.current = request;
    return request;
  }, [API]);

  const loadRuntimeSettings = useCallback(async () => {
    try {
      const res = await axios.get(`${API}/admin/settings`);
      const nextSettings = res?.data?.settings || {};
      setThrottleMode(deriveThrottleMode(nextSettings));
    } catch (e) {
      console.error('Failed to load runtime settings', e);
    }
  }, [API]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void fetchWorkerStatus();
      void loadRuntimeSettings();
    }, 0);
    return () => {
      clearTimeout(timer);
    };
  }, [fetchWorkerStatus, loadRuntimeSettings]);

  const handleSetThrottleMode = useCallback(async (nextMode) => {
    const normalizedMode = nextMode === 'turbo' ? 'turbo' : 'quiet';
    setThrottleMode(normalizedMode);
    setThrottleModeSaving(true);
    try {
      const res = await axios.post(`${API}/admin/settings`, buildThrottleSettingsPayload(normalizedMode));
      const nextSettings = res?.data?.settings || {};
      setThrottleMode(deriveThrottleMode(nextSettings));
      setOperatorNotice(`Throttle mode set to ${normalizedMode}.`);
    } catch (e) {
      console.error('Failed to update throttle mode', e);
      setOperatorNotice(`Failed to switch to ${normalizedMode} mode.`);
      void loadRuntimeSettings();
    } finally {
      setThrottleModeSaving(false);
    }
  }, [API, loadRuntimeSettings]);

  const handleReboot = async () => {
    setRebooting(true);
    try {
      await axios.post(`${API}/admin/reboot`);
      setTimeout(() => {
        setRebooting(false);
        void Promise.all([fetchWorkerStatus(), fetchData(true)]);
      }, 3000);
    } catch (e) {
      console.error('Reboot failed', e);
      setRebooting(false);
    }
  };

  const handlePause = async (lane = null) => {
    try {
      await axios.post(`${API}/admin/worker/pause`, null, lane ? { params: { lane } } : undefined);
      await fetchWorkerStatus();
    } catch (e) { console.error(e); }
  };

  const handleResume = async (lane = null) => {
    try {
      await axios.post(`${API}/admin/worker/resume`, null, lane ? { params: { lane } } : undefined);
      await fetchWorkerStatus();
    } catch (e) { console.error(e); }
  };

  const handlePauseTask = async (taskId) => {
    if (!taskId) return;
    try {
      await axios.post(`${API}/admin/tasks/${taskId}/pause`);
      await Promise.all([fetchWorkerStatus(), fetchData(true), loadRuntimeSettings()]);
    } catch (e) { console.error(e); }
  };

  const handleResumeTask = async (taskId) => {
    if (!taskId) return;
    try {
      await axios.post(`${API}/admin/tasks/${taskId}/resume`);
      await Promise.all([fetchWorkerStatus(), fetchData(true)]);
    } catch (e) { console.error(e); }
  };

  const handleStopTask = async (taskId) => {
    if (!taskId) return;
    try {
      await axios.post(`${API}/admin/tasks/${taskId}/stop`);
      await Promise.all([fetchWorkerStatus(), fetchData(true)]);
    } catch (e) { console.error(e); }
  };

  const handleReplayTask = async (taskId, overrides = {}) => {
    if (!taskId) return;
    try {
      await axios.post(`${API}/admin/tasks/${taskId}/replay`, overrides);
      await Promise.all([fetchWorkerStatus(), fetchData(true)]);
    } catch (e) { console.error(e); }
  };
  const handleBranchTask = async (taskId, payload = {}) => {
    if (!taskId) return;
    try {
      const resp = await axios.post(`${API}/admin/tasks/${taskId}/branch`, payload);
      await Promise.all([fetchWorkerStatus(), fetchData(true)]);
      return resp.data;
    } catch (e) { console.error(e); }
  };

  const handleMutateTask = async (taskId, payload = {}) => {
    if (!taskId) return;
    try {
      const resp = await axios.post(`${API}/admin/tasks/${taskId}/mutate`, payload);
      await Promise.all([fetchWorkerStatus(), fetchData(true)]);
      return resp.data;
    } catch (e) { console.error(e); }
  };

  const handleMutateProcedure = async (procedureId, payload = {}) => {
    if (!procedureId) return;
    try {
      const resp = await axios.post(`${API}/admin/registry/procedures/${procedureId}/mutate`, payload);
      await Promise.all([fetchData(true)]);
      return resp.data;
    } catch (e) { console.error(e); }
  };

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    const hasOpenAttempts = tasks.some((task) =>
      Array.isArray(task.attempts) && task.attempts.some((attempt) => !attempt.ended_at && !attempt.outcome)
    );
    if (!hasOpenAttempts) return undefined;
    const interval = window.setInterval(() => {
      setActivityNowMs(Date.now());
    }, 15000);
    return () => window.clearInterval(interval);
  }, [tasks]);

  const fetchData = useCallback(async (force = false) => {
    if (fetchPromiseRef.current) {
      if (force) pendingFetchRef.current = true;
      return fetchPromiseRef.current;
    }

    // Skip polling while a message is in-flight (unless forced)
    if (!force && isSendingRef.current) return;

    const request = (async () => {
      // Increment generation — any older in-flight fetch will see a mismatch and bail
      const gen = ++fetchGenRef.current;
      try {
        const sessionParams = activeNav === 'chat' && currentScope !== 'home' ? { lane: effectiveLane } : undefined;
        const taskParams = { attempt_limit: 8, include_evidence: false };
        const needsDashboardData = activeNav === 'dashboard';
        const needsChatBannerData = activeNav === 'chat';
        const [tasksRes, msgsRes, sessionsRes, telemetryRes, providerTelemetryRes, dashboardRes, loadedContextRes, routingRes, specsRes, specProposalsRes, knowledgePagesRes, knowledgeSourcesRes, retentionRes, variantRatingsRes, predictionTrustRes, proposalConfigRes, evalJobsRes] = await Promise.all([
          axios.get(`${API}/tasks`, { params: taskParams }),
          !sessionId || isDraftSession ? Promise.resolve({ data: [] }) : axios.get(`${API}/messages?session_id=${sessionId}`),
          axios.get(`${API}/sessions`, { params: sessionParams }),
          needsDashboardData ? axios.get(`${API}/admin/telemetry?limit=8`) : Promise.resolve({ data: { telemetry: null } }),
          needsDashboardData ? axios.get(`${API}/admin/providers/telemetry`) : Promise.resolve({ data: { providers: {} } }),
          needsDashboardData ? axios.get(`${API}/admin/dashboard?limit=6`) : Promise.resolve({ data: { dashboard: null } }),
          needsDashboardData ? axios.get(`${API}/admin/context/loaded`) : Promise.resolve({ data: { loaded: { files: [], budget_tokens: 0 } } }),
          axios.get(`${API}/admin/routing`),
          needsDashboardData ? axios.get(`${API}/admin/specs`) : Promise.resolve({ data: { specs: null } }),
          (needsDashboardData || needsChatBannerData) ? axios.get(`${API}/admin/spec_proposals?limit=6`) : Promise.resolve({ data: { proposals: [] } }),
          needsDashboardData ? axios.get(`${API}/admin/knowledge/pages?limit=6&audience=operator`) : Promise.resolve({ data: { pages: [] } }),
          activeNav === 'knowledge' ? axios.get(`${API}/admin/knowledge/sources?limit=100`) : Promise.resolve({ data: { sources: [] } }),
          needsDashboardData ? axios.get(`${API}/admin/storage/retention`) : Promise.resolve({ data: null }),
          needsDashboardData ? axios.get(`${API}/admin/variants/ratings`) : Promise.resolve({ data: null }),
          needsDashboardData ? axios.get(`${API}/admin/predictions/trust`) : Promise.resolve({ data: null }),
          needsDashboardData ? axios.get(`${API}/admin/evals/proposal_config`) : Promise.resolve({ data: null }),
          needsDashboardData ? axios.get(`${API}/admin/evals/jobs`) : Promise.resolve({ data: null })
        ]);

        // If a newer fetch was launched while we were awaiting, discard this result
        if (gen !== fetchGenRef.current) return;

        const sessions = (Array.isArray(sessionsRes.data) ? sessionsRes.data.slice() : [])
          .filter((session) => {
            if (activeNav !== 'chat') return true;
            if (currentScope === 'home') return Boolean(explicitLaneForSessionId(session.session_id));
            return sessionMatchesLane(session.session_id, effectiveLane);
          });
        const shouldIncludeCurrentSession =
          Boolean(sessionId) &&
          !sessions.some((s) => s.session_id === sessionId) &&
          (currentScope === 'home'
            ? Boolean(explicitLaneForSessionId(sessionId))
            : sessionMatchesLane(sessionId, effectiveLane));
        if (shouldIncludeCurrentSession) {
          const existingSession = sessionListRef.current.find((session) => session.session_id === sessionId);
          sessions.push({
            session_id: sessionId,
            title: existingSession?.title || fallbackSessionTitle(sessionId),
            message_count: msgsRes.data.length,
            first_message_at: msgsRes.data[0]?.created_at || null,
            last_message_at: msgsRes.data[msgsRes.data.length - 1]?.created_at || null,
            last_message_preview: msgsRes.data[msgsRes.data.length - 1]?.content || '',
            last_message_role: msgsRes.data[msgsRes.data.length - 1]?.role || null,
          });
        }
        sessions.sort((a, b) => String(b.last_message_at || '').localeCompare(String(a.last_message_at || '')));
        startTransition(() => {
          setTasks(tasksRes.data);
          setMessages(msgsRes.data);
          setSessionList(sessions);
          setTelemetry(telemetryRes.data.telemetry);
          setProviderTelemetry(providerTelemetryRes.data.providers || {});
          setDashboard(dashboardRes.data.dashboard || null);
          setLoadedContext(loadedContextRes.data.loaded || { files: [], budget_tokens: 0 });
          setRoutingSummary(normalizeRoutingSummary(routingRes.data.routing));
          setSpecsSnapshot(specsRes.data.specs || null);
          setSpecProposalSnapshot(specProposalsRes.data.proposals || []);
          setKnowledgePagesSnapshot(knowledgePagesRes.data.pages || []);
          setKnowledgeSources(knowledgeSourcesRes.data.sources || []);
          setRetentionSnapshot(retentionRes.data || null);
          setVariantRatingsSnapshot(variantRatingsRes?.data?.ratings || null);
          setPredictionTrustSnapshot(predictionTrustRes?.data?.trust || null);
          setProposalConfigSnapshot(proposalConfigRes?.data?.config || null);
          setEvalJobsSnapshot(evalJobsRes?.data?.jobs || []);
          setApiStatus('ok');
          if (activeNav === 'chat' && currentScope === 'home' && !sessionId && sessions.length) {
            setScopeSessionIds((prev) => ({ ...prev, home: sessions[0].session_id }));
          }
        });
      } catch (err) {
        if (gen !== fetchGenRef.current) return;
        console.error('Fetch failed', err);
        setApiStatus('error');
      } finally {
        fetchPromiseRef.current = null;
        if (pendingFetchRef.current) {
          pendingFetchRef.current = false;
          window.setTimeout(() => {
            void fetchData(true);
          }, 0);
        }
      }
    })();

    fetchPromiseRef.current = request;
    return request;
  }, [activeNav, currentScope, effectiveLane, isDraftSession, sessionId]);

  const handleReconnect = useCallback(async () => {
    setApiStatus('connecting');
    setWorkerApiStatus('connecting');
    setDesiredGlobalEnabled(true);
    setReconnectingBackend(true);
    try {
      if (isDesktopRuntime()) {
        try {
          const { invoke } = await import('@tauri-apps/api/core');
          await invoke('desktop_reconnect_backend');
        } catch (err) {
          console.error('Desktop reconnect failed', err);
        }
      }
      await Promise.all([fetchWorkerStatus(), fetchData(true)]);
      const healthy = await axios.get(`${API}/admin/health`, { timeout: 2500 })
        .then(() => true)
        .catch(() => false);
      if (!healthy) {
        setDesiredGlobalEnabled(false);
      }
      return healthy;
    } catch (err) {
      console.error('Reconnect refresh failed', err);
      setDesiredGlobalEnabled(false);
      return false;
    } finally {
      setReconnectingBackend(false);
    }
  }, [API, fetchData, fetchWorkerStatus, loadRuntimeSettings]);

  const scheduleRefresh = useCallback(({ force = false, refreshData = true, refreshWorker = true } = {}) => {
    if (refreshData) {
      pendingDataRefreshRef.current = true;
      pendingFetchRef.current = pendingFetchRef.current || force;
    }
    if (refreshWorker) {
      pendingWorkerStatusRef.current = true;
    }
    if (refreshTimerRef.current != null) return;
    refreshTimerRef.current = window.setTimeout(() => {
      refreshTimerRef.current = null;
      const shouldForceFetch = pendingFetchRef.current;
      const shouldRefreshData = pendingDataRefreshRef.current;
      const shouldRefreshWorker = pendingWorkerStatusRef.current;
      pendingFetchRef.current = false;
      pendingDataRefreshRef.current = false;
      pendingWorkerStatusRef.current = false;
      const jobs = [];
      if (shouldRefreshWorker) {
        jobs.push(fetchWorkerStatus());
      }
      if (shouldRefreshData || shouldForceFetch) {
        jobs.push(fetchData(shouldForceFetch));
      }
      void Promise.all(jobs);
    }, 100);
  }, [fetchData, fetchWorkerStatus]);

  useEffect(() => {
    scheduleRefresh({ force: true });

    let fallbackInterval = null;
    const es = new EventSource(`${API}/events`);

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (fallbackInterval) {
          clearInterval(fallbackInterval);
          fallbackInterval = null;
        }
        if (data.type === 'worker_status') {
          scheduleRefresh({ refreshData: false, refreshWorker: true });
        } else if (data.type === 'task_update' || data.type === 'message') {
          scheduleRefresh({ force: true, refreshData: true, refreshWorker: true });
        }
      } catch (err) {
        console.error('SSE Parse Error:', err);
      }
    };

    es.onerror = (err) => {
      console.error('SSE Error:', err);
      if (!fallbackInterval) {
        fallbackInterval = setInterval(() => {
          scheduleRefresh({ force: true });
        }, 30000);
      }
    };

    return () => {
      es.close();
      if (refreshTimerRef.current != null) {
        clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
      if (fallbackInterval) {
        clearInterval(fallbackInterval);
      }
    };
  }, [API, scheduleRefresh]);

  useEffect(() => {
    if (activeNav !== 'knowledge') return;
    let cancelled = false;

    const loadKnowledge = async () => {
      try {
        const pagesRes = await axios.get(`${API}/admin/knowledge/pages`, {
          params: {
            limit: 50,
            audience: 'operator',
            query: knowledgeQuery || undefined,
          },
        });
        if (cancelled) return;
        const allPages = pagesRes.data.pages || [];
        const nextPages = allPages.filter((page) => {
          const maintenance = page?.maintenance || {};
          const reviewStatus = String(maintenance?.review_status || '').trim().toLowerCase();
          const evidenceStatus = String(maintenance?.evidence_status || '').trim().toLowerCase();
          const createdBy = String(page?.created_by || '').trim().toLowerCase();
          const provenanceKinds = new Set(
            Array.isArray(page?.provenance)
              ? page.provenance.map((entry) => String(entry?.kind || '').trim().toLowerCase()).filter(Boolean)
              : []
          );
          const sourceCount = Number(page?.source_count || 0);
          if (page?.slug === 'current-knowledge-base' || page?.slug === 'knowledge-maintenance-report') return true;
          if (reviewStatus === 'confirmed') return true;
          if (page?.created_by === 'operator') return true;
          if (evidenceStatus === 'synthesized' || evidenceStatus === 'verified') return true;
          if (createdBy === 'knowledge_compactor' && sourceCount <= 1) return false;
          if (provenanceKinds.size && [...provenanceKinds].every((kind) => kind === 'durable_doc')) return false;
          if (sourceCount > 1) return true;
          return false;
        });
        setKnowledgePages(nextPages);

        const hasCurrent = nextPages.some((page) => page.slug === selectedKnowledgeSlug);
        const nextSlug = hasCurrent ? selectedKnowledgeSlug : (nextPages[0]?.slug || '');
        setSelectedKnowledgeSlug(nextSlug);

        if (!nextSlug) {
          setSelectedKnowledgePage(null);
          return;
        }

        const pageRes = await axios.get(`${API}/admin/knowledge/pages/${nextSlug}`, {
          params: { audience: 'operator' },
        });
        if (cancelled) return;
        setSelectedKnowledgePage(pageRes.data.page || null);
      } catch (err) {
        if (cancelled) return;
        console.error('Failed to load knowledge pages', err);
        setKnowledgePages([]);
        setSelectedKnowledgePage(null);
      }
    };

    const loadKnowledgeSources = async () => {
      try {
        const sourcesRes = await axios.get(`${API}/admin/knowledge/sources`, {
          params: { limit: 100 },
        });
        if (cancelled) return;
        const nextSources = sourcesRes.data.sources || [];
        setKnowledgeSources(nextSources);
        const hasCurrent = nextSources.some((source) => source.path === selectedKnowledgeSourcePath);
        setSelectedKnowledgeSourcePath(hasCurrent ? selectedKnowledgeSourcePath : (nextSources[0]?.path || ''));
      } catch (err) {
        if (cancelled) return;
        console.error('Failed to load knowledge sources', err);
        setKnowledgeSources([]);
        setSelectedKnowledgeSourcePath('');
      }
    };

    void Promise.all([loadKnowledge(), loadKnowledgeSources()]);
    return () => {
      cancelled = true;
    };
  }, [API, activeNav, knowledgeQuery, selectedKnowledgeSlug]);

  useEffect(() => {
    if (activeNav !== 'knowledge') return;
    if (!selectedKnowledgeSourcePath) {
      setSelectedKnowledgeSource(null);
      return;
    }
    let cancelled = false;
    const loadKnowledgeSource = async () => {
      try {
        const sourceRes = await axios.get(`${API}/admin/knowledge/source`, {
          params: { path: selectedKnowledgeSourcePath },
        });
        if (cancelled) return;
        setSelectedKnowledgeSource(sourceRes.data.source || null);
      } catch (err) {
        if (cancelled) return;
        console.error('Failed to load knowledge source', err);
        setSelectedKnowledgeSource(null);
      }
    };
    void loadKnowledgeSource();
    return () => {
      cancelled = true;
    };
  }, [API, activeNav, selectedKnowledgeSourcePath]);

  useEffect(() => {
    if (activeNav !== 'procedures') return;
    let cancelled = false;

    const loadProcedures = async () => {
      try {
        const proceduresRes = await axios.get(`${API}/admin/procedures`);
        if (cancelled) return;
        const nextProcedures = Array.isArray(proceduresRes?.data?.procedures) ? proceduresRes.data.procedures : [];
        setProcedures(nextProcedures);

        const hasCurrent = nextProcedures.some((procedure) => procedure.procedure_id === selectedProcedureId);
        const nextProcedureId = hasCurrent ? selectedProcedureId : (nextProcedures[0]?.procedure_id || '');
        setSelectedProcedureId(nextProcedureId);

        if (!nextProcedureId) {
          setSelectedProcedure(null);
          return;
        }

        const detailRes = await axios.get(`${API}/admin/procedures/${encodeURIComponent(nextProcedureId)}`);
        if (cancelled) return;
        setSelectedProcedure(detailRes?.data?.procedure || null);
      } catch (err) {
        if (cancelled) return;
        console.error('Failed to load procedures', err);
        setProcedures([]);
        setSelectedProcedure(null);
      }
    };

    void loadProcedures();
    return () => {
      cancelled = true;
    };
  }, [API, activeNav, selectedProcedureId]);

  const handleCreateKnowledgePage = useCallback(async () => {
    const title = window.prompt('Title for the new knowledge page:', '');
    if (title == null) return;
    const normalizedTitle = title.trim();
    if (!normalizedTitle) return;
    const summary = window.prompt('Short summary (optional):', '') ?? '';
    const body = window.prompt('Page body:', summary || normalizedTitle);
    if (body == null) return;
    const normalizedBody = body.trim();
    if (!normalizedBody) return;
    try {
      const res = await axios.post(`${API}/admin/knowledge/pages`, {
        title: normalizedTitle,
        summary: summary.trim() || undefined,
        body: normalizedBody,
        created_by: 'operator',
        updated_reason: 'manual_create',
        domain: 'project',
      });
      const slug = res?.data?.page?.slug || '';
      if (slug) {
        setSelectedKnowledgeSlug(slug);
        setSelectedKnowledgePage(res.data.page || null);
      }
      setKnowledgeQuery('');
    } catch (err) {
      console.error('Failed to create knowledge page', err);
      window.alert('Failed to create knowledge page.');
    }
  }, [API]);

  const handleEditKnowledgePage = useCallback(async () => {
    if (!selectedKnowledgePage) return;
    const nextTitle = window.prompt('Edit page title:', selectedKnowledgePage.title || selectedKnowledgePage.slug || '');
    if (nextTitle == null) return;
    const normalizedTitle = nextTitle.trim();
    if (!normalizedTitle) return;
    const nextSummary = window.prompt('Edit summary:', selectedKnowledgePage.summary || '') ?? '';
    const nextBody = window.prompt('Edit page body:', selectedKnowledgePage.body || selectedKnowledgePage.summary || '');
    if (nextBody == null) return;
    const normalizedBody = nextBody.trim();
    if (!normalizedBody) return;
    try {
      const res = await axios.post(`${API}/admin/knowledge/pages`, {
        slug: selectedKnowledgePage.slug,
        title: normalizedTitle,
        summary: nextSummary.trim() || undefined,
        body: normalizedBody,
        tags: selectedKnowledgePage.tags || [],
        aliases: selectedKnowledgePage.aliases || [],
        related_pages: selectedKnowledgePage.related_pages || [],
        confidence: selectedKnowledgePage.confidence,
        created_by: 'operator',
        updated_reason: 'manual_edit',
        domain: selectedKnowledgePage.domain || 'project',
      });
      const slug = res?.data?.page?.slug || selectedKnowledgePage.slug;
      setSelectedKnowledgeSlug(slug);
      setSelectedKnowledgePage(res.data.page || null);
    } catch (err) {
      console.error('Failed to edit knowledge page', err);
      window.alert('Failed to edit knowledge page.');
    }
  }, [API, selectedKnowledgePage]);

  const handleQueueKnowledgeSource = useCallback(async () => {
    const sourceTitle = window.prompt('Name this source or note:', '');
    if (sourceTitle == null) return;
    const normalizedTitle = sourceTitle.trim();
    if (!normalizedTitle) return;
    const sourceNotes = window.prompt('What should be integrated from this source?', '') ?? '';
    try {
      await axios.post(`${API}/admin/knowledge/update`, {
        title: normalizedTitle,
        reason: `Integrate staged source into the knowledge base. ${sourceNotes.trim()}`.trim(),
        evidence_hints: sourceNotes.trim() ? [sourceNotes.trim()] : [],
        target_scope: 'knowledge_source',
        domain: 'project',
        session_id: sessionId || defaultSessionIdForLane(effectiveLane),
      });
      setOperatorNotice(`Queued source integration for ${normalizedTitle}`);
    } catch (err) {
      console.error('Failed to queue knowledge source integration', err);
      window.alert('Failed to queue source integration.');
    }
  }, [API, effectiveLane, sessionId]);

  const syncDraftComposerState = useCallback((nextMessage, nextAttachments) => {
    if (!isDraftSession) return;
    const normalizedAttachments = Array.isArray(nextAttachments) ? nextAttachments : [];
    const normalizedMessage = String(nextMessage || '');
    if (normalizedMessage.trim() || normalizedAttachments.length > 0 || currentDraft) {
      materializeDraftIfNeeded(effectiveLane, sessionId, normalizedMessage, {
        attachments: normalizedAttachments,
      });
    }
  }, [currentDraft, effectiveLane, isDraftSession, materializeDraftIfNeeded, sessionId]);

  const discardPreparedAttachments = useCallback(async (attachments) => {
    const discardable = (Array.isArray(attachments) ? attachments : []).filter((item) => item?.storage_key || item?.storage_path);
    if (!discardable.length) return;
    try {
      await axios.post(`${API}/chat/attachments/discard`, {
        attachments: discardable.map(({ storage_key, storage_path, id }) => ({ storage_key, storage_path, id })),
      });
    } catch (err) {
      console.error('Failed to discard prepared attachment(s)', err);
    }
  }, [API]);

  const handleQueueProcedure = useCallback(async (procedureId) => {
    if (!procedureId) return;
    try {
      await axios.post(`${API}/admin/procedures/${encodeURIComponent(procedureId)}/queue`, {
        lane: currentScope === 'home' ? effectiveLane : currentScope,
        session_id: currentScope === 'home' ? sessionId : (sessionId || undefined),
      });
      scheduleRefresh({ force: true, refreshData: true, refreshWorker: true });
    } catch (err) {
      console.error('Failed to queue procedure', err);
      window.alert('Failed to queue Procedure.');
    }
  }, [API, currentScope, effectiveLane, scheduleRefresh, sessionId]);

  const addAttachments = useCallback(async (files) => {
    const items = Array.from(files || []).filter(Boolean);
    if (!items.length) return;
    const placeholders = items.map(createPendingAttachmentPlaceholder);
    setPendingAttachments((prev) => {
      const next = [...prev, ...placeholders];
      syncDraftComposerState(inputText, next);
      return next;
    });

    await Promise.allSettled(items.map(async (file, index) => {
      const placeholder = placeholders[index];
      try {
        setPendingAttachments((prev) => {
          const next = prev.map((item) => (
            item.id === placeholder.id
              ? { ...item, description: 'Encoding attachment', progress: 0.32 }
              : item
          ));
          syncDraftComposerState(inputText, next);
          return next;
        });
        const prepared = {
          id: placeholder.id,
          name: file.name || 'Attachment',
          media_type: file.type || 'application/octet-stream',
          size_bytes: Number(file.size || 0),
          base64_data: await fileToBase64(file),
        };
        setPendingAttachments((prev) => {
          const next = prev.map((item) => (
            item.id === placeholder.id
              ? { ...item, description: 'Processing upload', progress: 0.68 }
              : item
          ));
          syncDraftComposerState(inputText, next);
          return next;
        });
        const res = await axios.post(`${API}/chat/attachments/prepare`, {
          attachments: [prepared],
        });
        const processed = Array.isArray(res?.data?.attachments) ? res.data.attachments[0] : null;
        if (!processed) throw new Error('Attachment processor returned no payload');
        setPendingAttachments((prev) => {
          const next = prev.map((item) => (
            item.id === placeholder.id
              ? {
                  ...item,
                  ...processed,
                  id: placeholder.id,
                  status: 'ready',
                  progress: 1,
                  description: processed.summary
                    ? `Ready · ${formatBytes(processed.size_bytes)}`
                    : (processed.description || `Ready · ${formatBytes(processed.size_bytes)}`),
                }
              : item
          ));
          syncDraftComposerState(inputText, next);
          return next;
        });
      } catch (err) {
        console.error('Failed to process attachment', err);
        setPendingAttachments((prev) => {
          const next = prev.map((item) => (
            item.id === placeholder.id
              ? { ...item, status: 'error', progress: 1, description: 'Upload failed' }
              : item
          ));
          syncDraftComposerState(inputText, next);
          return next;
        });
        setSendError('Failed to process one or more attachments.');
      }
    }));
  }, [API, inputText, syncDraftComposerState]);

  const removePendingAttachment = useCallback((attachmentId) => {
    setPendingAttachments((prev) => {
      const removed = prev.filter((item) => item.id === attachmentId);
      void discardPreparedAttachments(removed);
      const next = prev.filter((item) => item.id !== attachmentId);
      syncDraftComposerState(inputText, next);
      return next;
    });
  }, [discardPreparedAttachments, inputText, syncDraftComposerState]);

  const handleSendMessage = async () => {
    if ((!inputText.trim() && pendingAttachments.length === 0) || isSending) return;
    if (pendingAttachments.some((item) => String(item?.status || '').toLowerCase() === 'preparing')) {
      setSendError('Attachment processing is still in progress.');
      return;
    }
    const text = inputText;
    const replyPrefix = replyTarget
      ? `Replying to "${String(replyTarget.content || '').replace(/\s+/g, ' ').trim().slice(0, 120)}": `
      : '';
    const outboundText = `${replyPrefix}${text}`;
    const tempId = `temp-${Date.now()}`;
    setInputText('');
    const outboundAttachments = pendingAttachments;
    setPendingAttachments([]);
    if (isDraftSession) {
      materializeDraftIfNeeded(effectiveLane, sessionId, '', { attachments: [] });
    }
    setSendError('');
    setIsSending(true);
    isSendingRef.current = true;
    const targetSessionId = isDraftSession ? `${effectiveLane}:session-${Date.now()}` : sessionId;
    // Optimistic update: show the user's message immediately
    setMessages(prev => [...prev, {
      id: tempId,
      role: 'user',
      content: outboundText,
      pending: true,
      message_metadata: {
        attachments: outboundAttachments.map(({ base64_data, ...rest }) => rest),
      },
    }]);
    try {
      await axios.post(`${API}/chat`, {
        role: 'user',
        content: outboundText,
        session_id: targetSessionId,
        preferred_tier: effectiveLane,
        response_mode: responseMode,
        attachments: outboundAttachments,
      });
      if (isDraftSession) {
        const draftTitle = String(currentDraft?.title || '').trim();
        if (draftTitle && draftTitle !== fallbackSessionTitle(currentDraft?.session_id)) {
          try {
            await axios.patch(`${API}/sessions/${targetSessionId}`, { title: draftTitle });
          } catch (renameErr) {
            console.error('Failed to apply draft session title.', renameErr);
          }
        }
        setLaneDrafts((prev) => ({
          ...prev,
          [effectiveLane]: (prev[effectiveLane] || []).filter((draft) => draft?.session_id !== sessionId),
        }));
        setScopeSessionIds((prev) => ({ ...prev, [currentScope]: targetSessionId }));
      } else {
        await fetchData(true);
      }
      setReplyTarget(null);
    } catch (err) {
      console.error('Failed to send message.', err);
      const detail = err?.response?.data?.detail;
      const message = typeof detail === 'string' ? detail : 'Message failed to send. Please retry.';
      setSendError(message);
      if (isDraftSession) {
        materializeDraftIfNeeded(effectiveLane, sessionId, outboundText);
        setInputText(outboundText);
        setPendingAttachments(outboundAttachments);
      }
      setMessages(prev => prev.map(msg => (
        msg.id === tempId ? { ...msg, pending: false, failed: true } : msg
      )));
    }
    setIsSending(false);
    isSendingRef.current = false;
  };

  const handleReactToMessage = useCallback(async (messageId, reactionKey) => {
    const busyKey = `${messageId}:${reactionKey}`;
    setReactionBusyKey(busyKey);
    try {
      const res = await axios.post(`${API}/messages/${messageId}/react`, {
        session_id: sessionId,
        reaction: reactionKey,
      });
      const nextFeedback = res?.data?.feedback || null;
      if (nextFeedback) {
        setMessages((prev) => prev.map((msg) => (
          msg.id === messageId ? { ...msg, reactions: nextFeedback } : msg
        )));
      }
      await fetchData(true);
    } catch (err) {
      console.error('Failed to react to message', err);
    } finally {
      setReactionBusyKey('');
    }
  }, [API, effectiveLane, fetchData, sessionId]);

  const startNewChat = () => {
    const draftLane = currentScope === 'home' ? chatLane : currentScope;
    const draftsForLane = laneDrafts[draftLane] || [];
    const activeDraft = draftsForLane.find((draft) => draft?.session_id === sessionId) || null;
    if (isDraftSession && !activeDraft && inputText.trim()) {
      materializeDraftIfNeeded(draftLane, sessionId, inputText);
    }
    if (activeDraft && !draftHasContent(activeDraft)) {
      setScopeSessionIds(prev => ({ ...prev, [currentScope]: activeDraft.session_id }));
      setMessages([]);
      setInputText(activeDraft.draftMessage || '');
      setPendingAttachments(Array.isArray(activeDraft.attachments) ? activeDraft.attachments : []);
      setSendError('');
      return;
    }
    const draftId = draftSessionIdForLane(draftLane);
    if (pendingAttachments.length > 0) {
      void discardPreparedAttachments(pendingAttachments);
    }
    setScopeSessionIds(prev => ({ ...prev, [currentScope]: draftId }));
    setMessages([]);
    setInputText('');
    setPendingAttachments([]);
    setSendError('');
  };

  const deleteSession = async (idToDelete) => {
    if (isDraftSessionId(idToDelete)) {
      const draftLane = laneForSessionId(idToDelete);
      setLaneDrafts((prev) => ({
        ...prev,
        [draftLane]: (prev[draftLane] || []).filter((draft) => draft?.session_id !== idToDelete),
      }));
      setSessionList(prev => prev.filter((session) => session.session_id !== idToDelete));
      if (sessionId === idToDelete) {
        if (pendingAttachments.length > 0) {
          void discardPreparedAttachments(pendingAttachments);
        }
        setScopeSessionIds(prev => ({ ...prev, [currentScope]: currentScope === 'home' ? null : defaultSessionIdForLane(draftLane) }));
        setMessages([]);
        setInputText('');
        setPendingAttachments([]);
      }
      return;
    }
    try {
      await axios.delete(`${API}/sessions/${idToDelete}`);
      setSessionList(prev => prev.filter((session) => session.session_id !== idToDelete));
      if (sessionId === idToDelete) {
        setScopeSessionIds(prev => ({ ...prev, [currentScope]: currentScope === 'home' ? null : defaultSessionIdForLane(effectiveLane) }));
        setMessages([]);
      }
    } catch (err) {
      console.error('Failed to delete session.', err);
    }
  };

  const renameSession = useCallback(async (session) => {
    const currentTitle = session?.title || fallbackSessionTitle(session?.session_id);
    const nextTitle = window.prompt('Rename session:', currentTitle);
    if (nextTitle == null) return;
    const normalizedTitle = nextTitle.trim();
    if (!normalizedTitle || normalizedTitle === currentTitle) return;
    if (isDraftSessionId(session?.session_id)) {
      const draftLane = laneForSessionId(session.session_id);
      setLaneDrafts((prev) => ({
        ...prev,
        [draftLane]: (prev[draftLane] || []).map((draft) => (
          draft?.session_id === session.session_id
            ? { ...draft, title: normalizedTitle }
            : draft
        )),
      }));
      return;
    }
    try {
      await axios.patch(`${API}/sessions/${session.session_id}`, { title: normalizedTitle });
      await fetchData(true);
    } catch (err) {
      console.error('Failed to rename session.', err);
    }
  }, [API, fetchData]);

  const commitInlineSessionTitle = useCallback(async () => {
    const normalizedTitle = String(sessionTitleDraft || '').trim();
    setEditingSessionTitle(false);
    if (!normalizedTitle || normalizedTitle === sessionLabel) {
      setSessionTitleDraft(sessionLabel);
      return;
    }
    if (isDraftSession) {
      setLaneDrafts((prev) => ({
        ...prev,
        [effectiveLane]: (prev[effectiveLane] || []).map((draft) => (
          draft?.session_id === sessionId
            ? { ...draft, title: normalizedTitle }
            : draft
        )),
      }));
      return;
    }
    try {
      await axios.patch(`${API}/sessions/${sessionId}`, { title: normalizedTitle });
      await fetchData(true);
    } catch (err) {
      console.error('Failed to rename session.', err);
      setSessionTitleDraft(sessionLabel);
    }
  }, [API, effectiveLane, fetchData, isDraftSession, sessionId, sessionLabel, sessionTitleDraft]);

  const acceptSuggestedSessionTitle = useCallback(async (sessionIdToRename, suggestedTitle) => {
    const normalizedTitle = String(suggestedTitle || '').trim();
    if (!normalizedTitle) return;
    try {
      await axios.patch(`${API}/sessions/${sessionIdToRename}`, { title: normalizedTitle });
      await fetchData(true);
    } catch (err) {
      console.error('Failed to apply suggested session title.', err);
    }
  }, [API, fetchData]);

  const handleTypedReactionResponse = useCallback(async (message) => {
    const preview = String(message?.content || '').replace(/\s+/g, ' ').trim().slice(0, 120);
    const typedResponse = window.prompt('Add a typed response for this message:', '');
    setOpenReactionMenuId('');
    if (typedResponse == null) return;
    const normalized = typedResponse.trim();
    if (!normalized) return;
    try {
      await axios.post(`${API}/chat`, {
        role: 'user',
        content: `Feedback on message "${preview}": ${normalized}`,
        session_id: sessionId,
        preferred_tier: effectiveLane,
        response_mode: responseMode,
      });
      await fetchData(true);
    } catch (err) {
      console.error('Failed to send typed response.', err);
    }
  }, [API, effectiveLane, fetchData, responseMode, sessionId]);

  const handleReplyToMessage = useCallback((message) => {
    setOpenReactionMenuId('');
    setReplyTarget(message);
  }, []);

  const handleResetDatabase = async () => {
    await axios.post(`${API}/admin/fresh-start`);
    localStorage.removeItem('archivedTasks');
    setSessionList([]);
    setLaneDrafts({ trainer: [], agent: [] });
    setScopeSessionIds({
      home: null,
      trainer: defaultSessionIdForLane('trainer'),
      agent: defaultSessionIdForLane('agent'),
    });
    setMessages([]);
    setTasks([]);
    setArchivedTasks([]);
  };

  const runOperatorAction = useCallback(async (label, fn) => {
    setOperatorNotice(`${label}…`);
    try {
      await fn();
      setOperatorNotice(`${label} complete`);
      await fetchData(true);
    } catch (err) {
      console.error(`${label} failed`, err);
      const detail = err?.response?.data?.detail;
      setOperatorNotice(typeof detail === 'string' ? `${label} failed: ${detail}` : `${label} failed`);
    }
  }, [fetchData]);

  const handleRunRetention = useCallback(() => runOperatorAction('Retention run', () => (
    axios.post(`${API}/admin/storage/retention/run`, { force: true })
  )), [API, runOperatorAction]);

  const handleCompactKnowledge = useCallback(() => runOperatorAction('Knowledge compaction', () => (
    axios.post(`${API}/admin/knowledge/compact`)
  )), [API, runOperatorAction]);

  const handleContextScan = useCallback(() => runOperatorAction('Context scan', () => (
    axios.post(`${API}/admin/context/scan`)
  )), [API, runOperatorAction]);

  const handleQueueBootstrap = useCallback(() => runOperatorAction('Bootstrap cycle', () => (
    axios.post(`${API}/admin/experiments/bootstrap_cycle`, { queue: true, auto_promote: true })
  )), [API, runOperatorAction]);

  const handleQueueSampleTick = useCallback(() => runOperatorAction('Sampled eval', () => (
    axios.post(`${API}/admin/evals/sample_tick`, {
      queue: true,
      suite_name: 'mmlu_mini_v1',
      include_context: false,
      include_strong: true,
      include_weak: true,
      sample_size: 2,
      profiles: ['raw_model', 'harness_no_capes', 'harness_tools_no_web', 'harness_web_no_tools', 'harness_tools_web'],
    })
  )), [API, runOperatorAction]);

  const handleResolveSpecProposal = useCallback(async (proposal, resolution) => {
    const reviewerNotes = window.prompt(`Reviewer notes for ${resolution}:`, '') ?? '';
    let clarificationRequest = '';
    if (resolution === 'needs_clarification') {
      clarificationRequest = window.prompt('What clarification should Strata request?', '') ?? '';
      if (!clarificationRequest.trim()) {
        setOperatorNotice('Clarification cancelled');
        return;
      }
    }
    await runOperatorAction(`Spec proposal ${resolution}`, () => (
      axios.post(`${API}/admin/spec_proposals/${proposal.proposal_id}/resolve`, {
        resolution,
        reviewer_notes: reviewerNotes,
        clarification_request: clarificationRequest,
        reviewer: 'operator_ui',
      })
    ));
  }, [API, runOperatorAction]);

  // Derived telemetry from live data
  const specPendingCount = specProposalSnapshot.filter((proposal) => proposal?.status === 'pending_review').length;
  const specClarificationCount = specProposalSnapshot.filter((proposal) => proposal?.status === 'needs_clarification').length;

  const tasksForScope = React.useMemo(() => {
    if (currentScope === 'home') return tasks;
    return tasks.filter((task) => {
      const lane = laneForTask(task);
      return !lane || lane === currentScope;
    });
  }, [currentScope, tasks]);

  const scopeAttemptMetrics = React.useMemo(() => {
    const attempts = tasksForScope
      .flatMap((task) => (Array.isArray(task.attempts) ? task.attempts : []).map((attempt) => ({ ...attempt, taskId: task.id })))
      .filter((attempt) => Boolean(attempt.started_at))
      .sort((a, b) => String(b.ended_at || b.started_at || '').localeCompare(String(a.ended_at || a.started_at || '')));

    const summarizeSuccessRate = (rows) => {
      if (!rows.length) return '—';
      const succeeded = rows.filter((attempt) => attempt.outcome === 'succeeded').length;
      return `${succeeded}/${rows.length}`;
    };

    const completedDurationsMs = attempts
      .map((attempt) => {
        if (!attempt.started_at || !attempt.ended_at) return null;
        const started = Date.parse(attempt.started_at);
        const ended = Date.parse(attempt.ended_at);
        if (!Number.isFinite(started) || !Number.isFinite(ended) || ended < started) return null;
        return ended - started;
      })
      .filter((value) => Number.isFinite(value));

    const averageDurationMs = completedDurationsMs.length
      ? completedDurationsMs.reduce((sum, value) => sum + value, 0) / completedDurationsMs.length
      : null;

    return {
      attempts,
      success10: summarizeSuccessRate(attempts.slice(0, 10)),
      success50: summarizeSuccessRate(attempts.slice(0, 50)),
      averageDurationLabel: averageDurationMs == null
        ? '—'
        : averageDurationMs < 1000
        ? `${Math.round(averageDurationMs)}ms`
        : averageDurationMs < 60000
        ? `${(averageDurationMs / 1000).toFixed(1)}s`
        : `${(averageDurationMs / 60000).toFixed(1)}m`,
    };
  }, [tasksForScope]);

  const scopeOperationalMetrics = React.useMemo(() => {
    const queued = tasksForScope.filter((task) => task.status === 'pending' && !task.paused && !task.human_intervention_required).length;
    const working = tasksForScope.filter((task) => task.status === 'working').length;
    const blocked = tasksForScope.filter((task) => task.status === 'blocked').length;
    const needsYou = tasksForScope.filter((task) => task.human_intervention_required).length;
    const pausedTasks = tasksForScope.filter((task) => task.paused).length;
    return {
      queued,
      working,
      blocked,
      needsYou,
      pausedTasks,
      loadedContextCount: loadedContext?.files?.length || 0,
      loadedContextBudget: loadedContext?.budget_tokens || 0,
    };
  }, [loadedContext?.budget_tokens, loadedContext?.files, tasksForScope]);
  const scopeHasAnyTasks = tasksForScope.length > 0;

  const fullTaskTree = React.useMemo(() => buildTaskTree(tasks), [tasks]);
  const taskTree = React.useMemo(() => {
    const filteredTasks = tasks.filter((task) => !archivedTasks.includes(task.id));
    return buildTaskTree(filteredTasks);
  }, [tasks, archivedTasks]);

  useEffect(() => {
    if (!editingSessionTitle) {
      setSessionTitleDraft(sessionLabel);
    }
  }, [editingSessionTitle, sessionLabel]);

  useEffect(() => {
    if (isDraftSession) return;
    if (activeNav !== 'chat') return;
    if (!currentSession || Number(currentSession.unread_count || 0) <= 0) return;
    let cancelled = false;
    const markRead = async () => {
      try {
        await axios.post(`${API}/sessions/${sessionId}/read`, {});
        if (!cancelled) {
          await fetchData(true);
        }
      } catch (err) {
        if (!cancelled) {
          console.error('Failed to mark session as read.', err);
        }
      }
    };
    void markRead();
    return () => {
      cancelled = true;
    };
  }, [API, activeNav, currentSession, fetchData, isDraftSession, sessionId]);

  // ── Icon Nav items ───────────────────────────────────────────────────────────
  const navItems = [
    { id: 'chat',      Icon: MessageSquare,   label: 'Chat'      },
    { id: 'tasks',     Icon: Activity,        label: 'Tasks'     },
    { id: 'history',   Icon: History,         label: 'History'   },
    { id: 'knowledge', Icon: BookOpen,        label: 'Knowledge' },
    { id: 'procedures', Icon: GitBranch,      label: 'Procedures' },
    { id: 'workbench', Icon: Wrench,          label: 'Workbench' },
    { id: 'dashboard', Icon: LayoutDashboard, label: 'Dashboard' },
    { id: 'settings',  Icon: Settings,        label: 'Settings'  },
  ];
  const topTabs = [
    { id: 'home', label: 'Global', subtitle: 'System-wide view and controls', accent: 'neutral' },
    { id: 'agent', label: 'Agent', subtitle: 'Agent-model execution instance', accent: 'agent' },
    { id: 'trainer', label: 'Trainer', subtitle: 'Bootstrap / supervision instance', accent: 'trainer' },
  ];

  const finishedTaskTree = taskTree.filter(task => ['complete', 'abandoned', 'cancelled'].includes(task.status));
  const activeTaskTree = taskTree.filter(task => !['complete', 'abandoned', 'cancelled'].includes(task.status));
  const laneCurrentTaskTitles = React.useMemo(() => {
    const byId = new Map(tasks.map((task) => [task.id, stripInlineMarkdown(task.title || '')]));
    return {
      trainer: byId.get(laneDetails?.trainer?.current_task_id) || '',
      agent: byId.get(laneDetails?.agent?.current_task_id) || '',
    };
  }, [laneDetails, tasks]);
  const scopeFilterLane = currentScope === 'home' ? null : effectiveLane;
  const scopedActiveTaskTree = scopeFilterLane
    ? activeTaskTree.filter((task) => {
        const lane = laneForTask(task);
        return !lane || lane === scopeFilterLane;
      })
    : activeTaskTree;
  const scopedFinishedTaskTree = scopeFilterLane
    ? finishedTaskTree.filter((task) => {
        const lane = laneForTask(task);
        return !lane || lane === scopeFilterLane;
      })
    : finishedTaskTree;
  const scopedQueuedTaskTree = React.useMemo(
    () => scopedActiveTaskTree.filter((task) => task.status === 'pending'),
    [scopedActiveTaskTree]
  );
  const laneVisibleTasks = activeNav === 'chat' && currentScope !== 'home'
    ? scopedActiveTaskTree
    : activeTaskTree;
  const laneQueuedTasks = React.useMemo(
    () => laneVisibleTasks.filter((task) => task.status === 'pending'),
    [laneVisibleTasks]
  );
  const laneFinishedTasks = activeNav === 'chat' && currentScope !== 'home'
    ? scopedFinishedTaskTree
    : finishedTaskTree;
  const visibleTaskTree = activeNav === 'dashboard' ? taskTree : laneVisibleTasks;
  const visibleSessionList = React.useMemo(() => {
    const list = Array.isArray(sessionList) ? sessionList.slice() : [];
    const drafts = (currentScope === 'home'
      ? [...(laneDrafts.trainer || []), ...(laneDrafts.agent || [])]
      : (laneDrafts[effectiveLane] || []))
      .slice()
      .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
    const persisted = list.filter((session) => {
      if (drafts.some((draft) => draft.session_id === session.session_id)) return false;
      if (!persistedSessionHasContent(session)) return false;
      if (currentScope === 'home') return Boolean(explicitLaneForSessionId(session.session_id));
      return sessionMatchesLane(session.session_id, effectiveLane);
    });
    return [...drafts, ...persisted];
  }, [currentScope, effectiveLane, laneDrafts, sessionList]);

  const loadPreviousHomeSession = useCallback(() => {
    const previousSession = visibleSessionList.find((session) => persistedSessionHasContent(session));
    if (!previousSession) {
      setCurrentScope('home');
      setActiveNav('chat');
      startNewChat();
      return;
    }
    const nextSessionId = previousSession.session_id;
    setCurrentScope('home');
    setChatLane(laneForSessionId(nextSessionId));
    setScopeSessionIds((prev) => ({ ...prev, home: nextSessionId }));
    setActiveNav('chat');
    setSendError('');
  }, [startNewChat, visibleSessionList]);

  const startNewHomeSession = useCallback(() => {
    setCurrentScope('home');
    setActiveNav('chat');
    startNewChat();
  }, [startNewChat]);

  const buildLaneMeta = (lane) => {
    const laneDetail = laneDetails?.[lane] || defaultLaneDetail;
    const route = routingSummary?.[lane] || null;
    if (route?.error) return route.error;
    const taskTitle = laneCurrentTaskTitles?.[lane] || '';
    const heartbeatLabel = formatLaneHeartbeat(laneDetail);
    const stepLabel = String(laneDetail.step_label || '').trim();
    const stepDetail = String(laneDetail.step_detail || '').trim();
    const activityReason = String(laneDetail.activity_reason || '').trim();
    const activityMode = String(laneDetail.activity_mode || '').trim().toUpperCase();
    const parts = [
      taskTitle || (activityMode === 'IDLE' ? 'No active task' : ''),
      activityMode === 'STALLED'
        ? (activityReason || stepDetail || stepLabel || 'Waiting for progress heartbeat')
        : '',
      heartbeatLabel,
      friendlyTransportLabel(route?.transport),
      friendlyProviderLabel(route?.provider),
      friendlyModelLabel(route?.selected_model || route?.model),
    ].filter(Boolean);
    if (parts.length) return parts.join(' · ');
    const laneStatus = laneStatuses?.[lane] || 'IDLE';
    const tierStatus = tiers?.[lane] || 'unknown';
    const defaultHint = lane === 'trainer' ? 'cloud preferred' : 'local preferred';
    if (workerApiStatus === 'ok') return `${defaultHint} · ${laneStatus.toLowerCase()} · tier ${tierStatus}`;
    return `${defaultHint} · routing unavailable`;
  };
  const pickFocusedTask = (nodes = []) => {
    if (!nodes.length) return null;
    const statusRank = { working: 0, blocked: 1, pending: 2, pushed: 3 };
    const ordered = nodes
      .slice()
      .sort((a, b) => {
        const rankDelta = (statusRank[a.status] ?? 9) - (statusRank[b.status] ?? 9);
        if (rankDelta !== 0) return rankDelta;
        return String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || ''));
      });
    return ordered[0] || null;
  };
  const findTaskPathById = (nodes, targetId) => {
    if (!targetId) return [];
    for (const node of Array.isArray(nodes) ? nodes : []) {
      if (String(node?.id || '') === String(targetId)) return [node];
      const childPath = findTaskPathById(node?.children || [], targetId);
      if (childPath.length) return [node, ...childPath];
    }
    return [];
  };
  const getFocusedTaskPath = (scopeId) => {
    if (scopeId === 'home') return [];
    const laneTaskId = String(laneDetails?.[scopeId]?.current_task_id || '').trim();
    if (laneTaskId) {
      const livePath = findTaskPathById(fullTaskTree, laneTaskId);
      if (livePath.length) return livePath;
    }
    const scopedTree = activeTaskTree.filter((task) => {
      const lane = laneForTask(task);
      return !lane || lane === scopeId;
    });
    const root = pickFocusedTask(scopedTree);
    if (!root) return [];
    const path = [root];
    let cursor = root;
    while (cursor?.children?.length) {
      const activeChildren = cursor.children.filter((child) => !['complete', 'abandoned', 'cancelled'].includes(child.status));
      const next = pickFocusedTask(activeChildren);
      if (!next) break;
      path.push(next);
      cursor = next;
    }
    return path;
  };
  const buildScopeTaskProgress = (scopeId) => {
    if (scopeId === 'home') {
      const scopedTasks = tasks;
      const trainerActive = tasks.some((task) => laneForTask(task) === 'trainer' && !['complete', 'abandoned', 'cancelled'].includes(task.status));
      const agentActive = tasks.some((task) => laneForTask(task) === 'agent' && !['complete', 'abandoned', 'cancelled'].includes(task.status));
      const blocked = scopedTasks.filter((task) => task.status === 'blocked').length;
      const working = scopedTasks.filter((task) => task.status === 'working').length;
      const activeLanes = [trainerActive, agentActive].filter(Boolean).length;
      return {
        percent: activeLanes === 0 ? 0 : Math.round(((trainerActive ? 1 : 0) + (agentActive ? 1 : 0)) / 2 * 100),
        summary: `${activeLanes || 0} active lane${activeLanes === 1 ? '' : 's'}`,
        currentTitle: blocked > 0 ? `${blocked} blocked` : working > 0 ? `${working} working` : 'Idle',
        label: blocked > 0 ? `${blocked} blocked` : working > 0 ? `${working} working` : 'Idle',
        countLabel: activeLanes > 0 ? `${activeLanes} active` : 'System idle',
        pathSegments: [],
        taskActionable: false,
      };
    }

    const laneDetail = scopeId === 'home' ? null : (laneDetails?.[scopeId] || defaultLaneDetail);
    const laneTaskId = String(laneDetail?.current_task_id || '').trim();
    const path = laneTaskId ? findTaskPathById(fullTaskTree, laneTaskId) : [];
    const root = path[0];
    if (!root) {
      const blocked = tasks.filter((task) => laneForTask(task) === scopeId && task.status === 'blocked').length;
      const laneMode = String(laneDetail?.activity_mode || '').trim().toUpperCase();
      const stalledReason = String(laneDetail?.step_detail || laneDetail?.step_label || '').trim();
      const laneTaskTitle = String(laneCurrentTaskTitles?.[scopeId] || laneDetail?.current_task_title || '').trim();
      const percent = laneMode === 'STALLED'
        ? 72
        : laneMode === 'GENERATING'
        ? 64
        : laneMode === 'RUNNING'
        ? 58
        : laneMode === 'QUEUED'
        ? 20
        : blocked > 0
        ? 18
        : 0;
      const stateLabel = laneMode === 'STALLED'
        ? (stalledReason || 'waiting for progress')
        : laneMode === 'GENERATING'
        ? 'generating'
        : laneMode === 'RUNNING'
        ? 'working'
        : laneMode === 'QUEUED'
        ? 'queued'
        : blocked > 0
        ? 'blocked'
        : 'idle';
      return {
        percent,
        summary: '',
        currentTitle: laneTaskTitle || (blocked > 0 ? `${blocked} blocked task${blocked === 1 ? '' : 's'}` : 'No active task'),
        currentStateLabel: stateLabel,
        label: laneTaskTitle || 'No active task',
        countLabel: blocked > 0 ? 'Needs attention' : (laneDetail?.activity_label || 'No active task'),
        pathSegments: [],
        taskId: laneDetail?.current_task_id || null,
        taskPaused: Boolean(laneDetail?.paused),
        taskActionable: blocked > 0 || Boolean(laneDetail?.current_task_id),
      };
    }

    const leaf = path[path.length - 1];
    const parent = path.length > 1 ? path[path.length - 2] : null;
    const siblings = parent?.children || [];
    const siblingComplete = siblings.filter((child) => ['complete', 'abandoned', 'cancelled'].includes(child.status)).length;
    const siblingTotal = siblings.length || 1;
    const localPercent = siblings.length ? Math.round((siblingComplete / siblingTotal) * 100) : (leaf.status === 'complete' ? 100 : leaf.status === 'working' ? 65 : leaf.status === 'blocked' ? 25 : 10);
    const blockedDescendants = path.filter((task) => task.status === 'blocked').length;
    const depth = Number.isFinite(leaf.depth) ? leaf.depth : Math.max(0, path.length - 1);
    const rootTitle = String(root.title || 'Untitled goal').trim();
    const leafTitle = String(leaf.title || rootTitle).trim();
    const leafPaused = Boolean(leaf.paused);
    const pathSegments = path.map((task) => {
      const childSet = Array.isArray(task.children) ? task.children : [];
      const completedChildren = childSet.filter((child) => ['complete', 'abandoned', 'cancelled'].includes(child.status)).length;
      const totalChildren = childSet.length || 1;
      const percent = childSet.length
        ? Math.round((completedChildren / totalChildren) * 100)
        : task.status === 'complete'
        ? 100
        : task.status === 'working'
        ? 65
        : task.status === 'blocked'
        ? 25
        : 10;
      return {
        percent,
        status: task.status,
      };
    });

    const laneActivityMode = String(laneDetail?.activity_mode || '').trim().toUpperCase();
    return {
      percent: localPercent,
      summary: rootTitle,
      currentTitle: leafTitle,
      currentStateLabel: leafPaused
        ? 'paused'
        : leaf.status === 'pushed'
        ? 'children in progress'
        : laneActivityMode === 'GENERATING'
        ? 'generating'
        : laneActivityMode === 'STALLED'
        ? (String(laneDetail?.step_detail || laneDetail?.step_label || '').trim() || 'waiting for progress')
        : blockedDescendants > 0
        ? 'blocked'
        : leaf.status === 'pending'
        ? 'queued'
        : leaf.status === 'working'
        ? 'working'
        : '',
      label: leafPaused
        ? `${leafTitle} · paused`
        : leaf.status === 'pushed'
        ? `${leafTitle} · decomposed`
        : blockedDescendants > 0
        ? `${leafTitle} · blocked`
        : leaf.status === 'working'
        ? leafTitle
        : leaf.status === 'pending'
        ? `${leafTitle} · queued`
        : leafTitle,
      countLabel: siblings.length
        ? `${siblingComplete}/${siblingTotal} here`
        : '',
      pathSegments,
      taskId: leaf.id,
      taskPaused: leafPaused,
      taskActionable: true,
    };
  };
  const buildTopScopeCardProgress = (scopeId) => {
    if (scopeId === 'home') {
      return buildScopeTaskProgress('home');
    }
    const laneDetail = laneDetails?.[scopeId] || defaultLaneDetail;
    const laneMode = String(laneDetail?.activity_mode || laneStatuses?.[scopeId] || '').trim().toUpperCase();
    const activeTaskId = String(laneDetail?.current_task_id || '').trim();
    const resolvedLivePath = activeTaskId ? findTaskPathById(fullTaskTree, activeTaskId) : [];
    const livePath = resolvedLivePath.length ? resolvedLivePath : getFocusedTaskPath(scopeId);
    const laneTasks = tasks.filter((task) => laneForTask(task) === scopeId);
    const blocked = laneTasks.filter((task) => task.status === 'blocked').length;
    const working = laneTasks.filter((task) => task.status === 'working').length;
    const pending = laneTasks.filter((task) => task.status === 'pending').length;
    const liveLeaf = livePath.length ? livePath[livePath.length - 1] : null;
    const currentTitle = String(liveLeaf?.title || laneCurrentTaskTitles?.[scopeId] || laneDetail?.current_task_title || '').trim();
    const stalledReason = String(laneDetail?.step_detail || laneDetail?.step_label || '').trim();
    const pathSegments = livePath.map((task) => {
      const childSet = Array.isArray(task.children) ? task.children : [];
      const completedChildren = childSet.filter((child) => ['complete', 'abandoned', 'cancelled'].includes(child.status)).length;
      const totalChildren = childSet.length || 1;
      return {
        percent: childSet.length
          ? Math.round((completedChildren / totalChildren) * 100)
          : task.status === 'complete'
          ? 100
          : task.status === 'working'
          ? 65
          : task.status === 'blocked'
          ? 25
          : 10,
        status: task.status,
      };
    });
    const percent = laneMode === 'STALLED'
      ? 72
      : laneMode === 'GENERATING'
      ? 64
      : laneMode === 'RUNNING'
      ? 58
      : laneMode === 'QUEUED'
      ? 20
      : working > 0
      ? 48
      : pending > 0
      ? 16
      : blocked > 0
      ? 18
      : 0;
    return {
      percent,
      summary: working > 0
        ? `${working} working`
        : pending > 0
        ? `${pending} queued`
        : blocked > 0
        ? `${blocked} blocked`
        : '',
      currentTitle: currentTitle || 'No active task',
      currentStateLabel: laneMode === 'STALLED'
        ? (stalledReason || 'waiting for progress')
        : laneMode === 'GENERATING'
        ? 'generating'
        : laneMode === 'RUNNING'
        ? 'working'
        : blocked > 0
        ? 'blocked'
        : laneMode === 'QUEUED'
        ? 'queued'
        : 'idle',
      label: currentTitle || 'No active task',
      countLabel: blocked > 0 ? 'Needs attention' : (laneDetail?.activity_label || 'No active task'),
      pathSegments,
      taskId: activeTaskId || liveLeaf?.id || null,
      taskPaused: Boolean(laneDetail?.paused),
      taskActionable: Boolean(activeTaskId) || blocked > 0 || pending > 0 || working > 0,
    };
  };
  const focusedTaskPaneTree = React.useMemo(() => {
    if (activeNav === 'dashboard' || currentScope === 'home') return visibleTaskTree;
    const path = getFocusedTaskPath(currentScope);
    if (!path.length) return visibleTaskTree;
    const contextNode = path.length > 1 ? path[path.length - 2] : path[0];
    return contextNode ? [contextNode] : visibleTaskTree;
  }, [activeNav, currentScope, visibleTaskTree, activeTaskTree, laneDetails, fullTaskTree]);
  const loopMeta = routingSummary?.supervision?.active_jobs?.length
    ? `${routingSummary.supervision.active_jobs.length} active bootstrap job${routingSummary.supervision.active_jobs.length > 1 ? 's' : ''}`
    : `trainer ${String(laneDetails?.trainer?.activity_label || 'Idle').toLowerCase()} · agent ${String(laneDetails?.agent?.activity_label || 'Idle').toLowerCase()}`;

  return (
    <div className="app-container" style={{ display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw', background: '#0a0a0c', fontFamily: "'Outfit', sans-serif" }}>
      {showCloudModal && (
        <div className="modal-overlay">
          <div className="modal-content glass">
            <h2>☁️ Cloud Inference Offline</h2>
            <p>The <b>trainer</b> tier is currently unreachable or missing an API key.</p>
            <div className="modal-actions">
              <button 
                className="secondary" 
                onClick={() => {
                  localStorage.setItem('skipCloudWarning', 'true');
                  setShowCloudModal(false);
                }}
              >
                Operate Local-Only
              </button>
              <button 
                className="primary" 
                onClick={() => {
                  setShowCloudModal(false);
                  setCurrentScope('trainer');
                  setActiveNav('settings');
                }}
              >
                Configure Cloud
              </button>
            </div>
          </div>
        </div>
      )}

      <div style={{ padding: '12px 18px', borderBottom: '1px solid rgba(255,255,255,0.06)', background: '#09090b', flexShrink: 0, boxShadow: 'inset 0 -1px 0 rgba(255,255,255,0.02)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', width: '100%', minWidth: 0, overflow: 'hidden' }}>
          <button
            type="button"
            onClick={() => {
              setCurrentScope('home');
              setActiveNav('dashboard');
              setScopeSessionIds((prev) => ({ ...prev, home: prev.home || sessionId }));
            }}
            style={{
              minWidth: '160px',
              display: 'flex',
              alignItems: 'center',
              gap: '12px',
              padding: '0 4px',
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              textAlign: 'left',
            }}
          >
            <div style={{ width: '28px', height: '28px', borderRadius: '8px', background: 'linear-gradient(135deg, rgba(130,87,229,0.9), rgba(94,51,186,0.92))', boxShadow: '0 0 18px rgba(130,87,229,0.24)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <div style={{ width: '10px', height: '10px', background: '#fff', borderRadius: '2px', transform: 'rotate(45deg)' }} />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
              <div style={{ fontSize: '16px', fontWeight: 800, letterSpacing: '0.04em', color: '#f0f1f7' }}>
                Strata
              </div>
              <div style={{ fontSize: '10px', fontWeight: 700, letterSpacing: '0.08em', color: '#7d8296', textTransform: 'uppercase' }}>
                v{APP_VERSION} | {APP_CHANNEL}
              </div>
            </div>
          </button>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '6px',
              borderRadius: '14px',
              border: '1px solid rgba(255,255,255,0.08)',
              background: 'rgba(255,255,255,0.03)',
              flexShrink: 0,
            }}
          >
            <span
              style={{
                fontSize: '10px',
                fontWeight: 800,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color: '#7d8296',
                padding: '0 6px 0 4px',
                whiteSpace: 'nowrap',
              }}
            >
              Throttle
            </span>
            {[
              { id: 'quiet', label: 'Quiet' },
              { id: 'turbo', label: 'Turbo' },
            ].map((mode) => {
              const active = throttleMode === mode.id;
              return (
                <button
                  key={mode.id}
                  type="button"
                  onClick={() => {
                    if (mode.id !== throttleMode && !throttleModeSaving) {
                      void handleSetThrottleMode(mode.id);
                    }
                  }}
                  disabled={throttleModeSaving}
                  style={{
                    height: '32px',
                    padding: '0 12px',
                    borderRadius: '10px',
                    border: active
                      ? '1px solid rgba(232, 190, 130, 0.32)'
                      : '1px solid rgba(255,255,255,0.08)',
                    background: active
                      ? 'linear-gradient(135deg, rgba(214,173,113,0.22), rgba(140,87,45,0.20))'
                      : 'rgba(255,255,255,0.03)',
                    color: active ? '#f3ddbf' : '#aaafc2',
                    fontSize: '12px',
                    fontWeight: 700,
                    cursor: throttleModeSaving ? 'default' : 'pointer',
                    opacity: throttleModeSaving && !active ? 0.65 : 1,
                    transition: 'all 0.18s ease',
                    whiteSpace: 'nowrap',
                  }}
                  title={mode.id === 'quiet'
                    ? 'Favor quieter, more conservative local behavior.'
                    : 'Favor faster, more aggressive throughput.'}
                >
                  {mode.label}
                </button>
              );
            })}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '12px', width: '100%', minWidth: 0 }}>
          {topTabs.map((tab) => {
            const active = currentScope === tab.id;
            const showGlobalControls = tab.id === 'home';
            const isLane = tab.id === 'trainer' || tab.id === 'agent';
            const progress = buildTopScopeCardProgress(tab.id);
            const agentEnabled = tab.id === 'home'
              ? desiredGlobalEnabled
              : desiredLaneEnabled[tab.id];
            const agentSuppressedByGlobal = isLane && globalPaused;
            return (
              <TopModeTab
                key={tab.id}
                label={tab.label}
                accent={tab.accent}
                active={active}
                detail={tab.id === 'home' ? loopMeta : buildLaneMeta(tab.id)}
                tickerItems={tab.id === 'home'
                  ? [...(laneDetails?.trainer?.ticker_items || []), ...(laneDetails?.agent?.ticker_items || [])].slice(-10)
                  : (laneDetails?.[tab.id]?.ticker_items || [])}
                status={tab.id === 'home' ? workerStatus : (laneDetails?.[tab.id]?.activity_mode || laneStatuses?.[tab.id] || 'IDLE')}
                agentEnabled={agentEnabled}
                agentSuppressedByGlobal={agentSuppressedByGlobal}
                apiStatus={workerApiStatus}
                showControls={isLane || showGlobalControls}
                progress={progress}
                toggleChecked={Boolean(agentEnabled)}
                togglePending={tab.id === 'home' ? reconnectingBackend : false}
                onPause={showGlobalControls ? (() => {
                  setDesiredGlobalEnabled(false);
                  void handlePause();
                }) : (isLane ? (() => {
                  setDesiredLaneEnabled((prev) => ({ ...prev, [tab.id]: false }));
                  void handlePause(tab.id);
                }) : undefined)}
                onResume={showGlobalControls ? (() => {
                  setDesiredGlobalEnabled(true);
                  void handleResume();
                }) : (isLane ? (() => {
                  setDesiredLaneEnabled((prev) => ({ ...prev, [tab.id]: true }));
                  void handleResume(tab.id);
                }) : undefined)}
                onReconnect={showGlobalControls ? (() => { void handleReconnect(); }) : undefined}
                onPauseTask={isLane ? (() => handlePauseTask(progress?.taskId)) : undefined}
                onResumeTask={isLane ? (() => handleResumeTask(progress?.taskId)) : undefined}
                onStopTask={isLane ? (() => handleStopTask(progress?.taskId)) : undefined}
                onClick={() => {
                  if (tab.id === 'home') {
                    setCurrentScope('home');
                    setScopeSessionIds((prev) => ({ ...prev, home: prev.home || sessionId }));
                    return;
                  }
                  setChatLane(tab.id);
                  setCurrentScope(tab.id);
                  setMessages([]);
                  setSendError('');
                }}
              />
            );
          })}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
      {/* ── COLUMN 1: ICON NAV ─────────────────────────────────────────────── */}
      <div style={{ width: '72px', borderRight: '1px solid rgba(255,255,255,0.05)', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '20px 0', gap: '8px' }}>
        {navItems.map(({ id, Icon, label }) => (
          <NavIcon
            key={id}
            icon={Icon}
            label={label}
            active={activeNav === id}
            onHover={
              id === 'dashboard' || id === 'knowledge' || id === 'tasks'
                ? preloadNonChatContent
                : id === 'settings'
                ? preloadSettingsView
                : undefined
            }
            onClick={() => {
              setActiveNav(id);
              if (id === 'dashboard') {
                setScopeSessionIds((prev) => ({ ...prev, [currentScope]: prev[currentScope] || sessionId }));
              }
              if (id === 'settings' && currentScope === 'home') {
                setCurrentScope('home');
              }
            }}
          />
        ))}

        {/* Spacer */}
        <div style={{ flex: 1 }} />
      </div>

      {/* ── COLUMN 2: SESSION / HISTORY PANEL ──────────────────────────────── */}
      {showSessionPane && (
      <div style={{ width: '240px', borderRight: '1px solid rgba(255,255,255,0.05)', display: 'flex', flexDirection: 'column', background: '#0c0c0e' }}>
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px' }}>
          <HistoryPane
            sessionList={visibleSessionList}
            sessionId={sessionId}
            setSessionId={(nextSessionId) => {
              setScopeSessionIds(prev => ({ ...prev, [currentScope]: nextSessionId }));
              if (currentScope === 'home' && nextSessionId) {
                setChatLane(laneForSessionId(nextSessionId));
              }
            }}
            deleteSession={deleteSession}
            renameSession={renameSession}
            onNewSession={startNewChat}
            showLaneBadge={currentScope === 'home'}
          />
        </div>
      </div>
      )}

      {/* ── COLUMN 3: CHAT / DASHBOARD ────────────────────────────────────── */}
      <section style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#0a0a0c', borderRight: '1px solid rgba(255,255,255,0.05)', minWidth: 0 }}>
        <header style={{ padding: activeNav === 'chat' ? '12px 28px 10px' : '20px 28px', minHeight: '60px', boxSizing: 'border-box', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
          <div
            style={{ minWidth: 0, flex: 1, textAlign: activeNav === 'chat' ? 'center' : 'left' }}
            onMouseEnter={() => setTitleHovered(true)}
            onMouseLeave={() => setTitleHovered(false)}
          >
            {activeNav === 'chat' ? (
              <div style={{ display: 'flex', justifyContent: 'center', minWidth: 0 }}>
                {editingSessionTitle ? (
                  <input
                    autoFocus
                    value={sessionTitleDraft}
                    onChange={(event) => setSessionTitleDraft(event.target.value)}
                    onBlur={() => void commitInlineSessionTitle()}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        event.preventDefault();
                        void commitInlineSessionTitle();
                      }
                      if (event.key === 'Escape') {
                        setEditingSessionTitle(false);
                        setSessionTitleDraft(sessionLabel);
                      }
                    }}
                    style={{
                      background: 'rgba(255,255,255,0.04)',
                      border: '1px solid rgba(255,255,255,0.1)',
                      borderRadius: '10px',
                      color: '#f7f8fc',
                      fontSize: '16px',
                      fontWeight: 700,
                      padding: '6px 10px',
                      minWidth: 0,
                      outline: 'none',
                      width: 'min(420px, 100%)',
                      textAlign: 'center',
                    }}
                  />
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setSessionTitleDraft(sessionLabel);
                      setEditingSessionTitle(true);
                    }}
                    style={{
                      background: 'none',
                      border: 'none',
                      color: 'white',
                      padding: 0,
                      margin: 0,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '8px',
                      minWidth: 0,
                      cursor: 'text',
                      width: '100%',
                    }}
                  >
                    <span style={{ fontSize: '16px', fontWeight: 700, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 'min(520px, 100%)' }}>
                      {sessionLabel}
                    </span>
                    {titleHovered && (
                      <span style={{ color: '#8e93a8', display: 'inline-flex' }}>
                        <Pencil size={13} />
                      </span>
                    )}
                  </button>
                )}
              </div>
            ) : (
              <h1 style={{ fontSize: '18px', fontWeight: 700, color: 'white' }}>
                {activeNav === 'dashboard'
                  ? (currentScope === 'home' ? 'Strata Home' : `${currentScope === 'trainer' ? 'Trainer' : 'Agent'} Dashboard`)
                  : activeNav === 'history'
                  ? (currentScope === 'home' ? 'History' : `${currentScope === 'trainer' ? 'Trainer' : 'Agent'} History`)
                  : activeNav === 'tasks'
                  ? (currentScope === 'home' ? 'Tasks' : `${currentScope === 'trainer' ? 'Trainer' : 'Agent'} Tasks`)
                  : activeNav === 'knowledge'
                  ? (currentScope === 'home' ? 'Knowledge Base' : `${currentScope === 'trainer' ? 'Trainer' : 'Agent'} Knowledge`)
                : activeNav === 'procedures'
                  ? (currentScope === 'home' ? 'Procedures' : `${currentScope === 'trainer' ? 'Trainer' : 'Agent'} Procedures`)
                  : activeNav === 'workbench'
                  ? (currentScope === 'home' ? 'Workbench' : `${currentScope === 'trainer' ? 'Trainer' : 'Agent'} Workbench`)
                  : 'Settings'}
              </h1>
            )}
            {activeNav !== 'chat' && (
              <p style={{ fontSize: '12px', color: '#555', marginTop: '2px' }}>
                {activeNav === 'dashboard'
                ? (currentScope === 'home' ? 'Shared telemetry, routing, and operator surfaces' : 'Scoped operational telemetry and runtime detail')
                : activeNav === 'history'
                ? (currentScope === 'home' ? 'Chronological event log with expandable runtime metadata' : 'Scoped event log and autopsy surface')
                : activeNav === 'tasks'
                ? (currentScope === 'home' ? 'Canonical queue, execution, and completion view' : 'Scoped task queue, execution progress, and recent completions')
                : activeNav === 'knowledge'
                ? (currentScope === 'home' ? 'Navigable system wiki' : 'Knowledge visible within this scope')
                : activeNav === 'procedures'
                ? (currentScope === 'home' ? 'Draft, tested, and vetted workflow registry' : 'Procedures visible within this scope')
                : activeNav === 'workbench'
                ? 'Universal debugger and process workbench'
                : activeNav === 'settings'
                ? 'Scoped system configuration'
                : sessionMetaLabel}
              </p>
            )}
            {activeNav === 'chat' && suggestedSessionTitle && (
              <div style={{ marginTop: '6px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', flexWrap: 'wrap' }}>
                <span style={{ fontSize: '11px', color: '#8ea0b7' }}>
                  Suggested rename: <span style={{ color: '#d7e7ff', fontWeight: 700 }}>{suggestedSessionTitle}</span>
                </span>
                <button
                  onClick={() => acceptSuggestedSessionTitle(sessionId, suggestedSessionTitle)}
                  style={{ background: 'rgba(130,87,229,0.16)', border: '1px solid rgba(130,87,229,0.28)', color: '#d8cfff', borderRadius: '999px', padding: '4px 10px', fontSize: '10px', fontWeight: 800, cursor: 'pointer', letterSpacing: '0.06em', textTransform: 'uppercase' }}
                >
                  Use Suggestion
                </button>
              </div>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginLeft: '18px', flexShrink: 0, minWidth: activeNav === 'knowledge' ? 'min(620px, 62vw)' : 0 }}>
            {activeNav === 'knowledge' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', width: '100%' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', background: '#141418', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '14px', padding: '10px 14px', flex: 1, minWidth: 0 }}>
                  <Search size={15} color="#696a7b" />
                  <input
                    type="text"
                    value={knowledgeQuery}
                    onChange={(event) => setKnowledgeQuery(event.target.value)}
                    placeholder="Search wiki titles, tags, aliases..."
                    style={{ flex: 1, minWidth: 0, background: 'transparent', border: 'none', outline: 'none', color: '#edeeef', fontSize: '13px' }}
                  />
                </div>
                <button
                  type="button"
                  onClick={handleCreateKnowledgePage}
                  style={{ background: 'rgba(130,87,229,0.18)', border: '1px solid rgba(130,87,229,0.28)', color: '#f1e8ff', borderRadius: '12px', padding: '10px 14px', fontSize: '12px', fontWeight: 800, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px', whiteSpace: 'nowrap' }}
                >
                  <Plus size={14} />
                  Add Page
                </button>
                <button
                  type="button"
                  onClick={handleEditKnowledgePage}
                  disabled={!selectedKnowledgePage}
                  style={{ background: selectedKnowledgePage ? 'rgba(255,255,255,0.05)' : 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.08)', color: selectedKnowledgePage ? '#edeeef' : '#666a78', borderRadius: '12px', padding: '10px 14px', fontSize: '12px', fontWeight: 800, cursor: selectedKnowledgePage ? 'pointer' : 'default', display: 'flex', alignItems: 'center', gap: '8px', whiteSpace: 'nowrap' }}
                >
                  <Pencil size={14} />
                  Edit Page
                </button>
              </div>
            )}
          </div>
        </header>

        {showSpecBanner && (specPendingCount > 0 || specClarificationCount > 0) && (
          <div style={{ padding: '14px 28px 0', flexShrink: 0 }}>
            <div style={{ background: 'rgba(255,184,77,0.07)', border: '1px solid rgba(255,184,77,0.16)', borderRadius: '12px', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <div style={{ fontSize: '11px', color: '#ffb84d', fontWeight: 800, letterSpacing: '0.08em' }}>SPEC REVIEW NEEDS ATTENTION</div>
              <div style={{ fontSize: '12px', color: '#f0d5aa', lineHeight: 1.5 }}>
                {specClarificationCount > 0
                  ? `${specClarificationCount} spec proposal${specClarificationCount > 1 ? 's need' : ' needs'} clarification. Reply in chat and Strata will route your answer back into the review loop.`
                  : `${specPendingCount} spec proposal${specPendingCount > 1 ? 's are' : ' is'} pending review.`}
              </div>
            </div>
          </div>
        )}

        {activeNav === 'dashboard' || activeNav === 'history' || activeNav === 'knowledge' || activeNav === 'tasks' || activeNav === 'procedures' || activeNav === 'workbench' ? (
          <Suspense
            fallback={(
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#8d8ea1', fontSize: '13px' }}>
                Loading {activeNav} view...
              </div>
            )}
          >
            <NonChatContent
              activeNav={activeNav}
              dashboardProps={{
                telemetry,
                dashboard,
                providerTelemetry,
                loadedContext,
                tiers,
                routingSummary,
                currentScope,
                chatLane: effectiveLane,
                activeChatRoute,
                scopeTasks: tasksForScope,
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
                onRunRetention: handleRunRetention,
                onCompactKnowledge: handleCompactKnowledge,
                onContextScan: handleContextScan,
                onQueueBootstrap: handleQueueBootstrap,
                onQueueSampleTick: handleQueueSampleTick,
                onResolveSpecProposal: handleResolveSpecProposal,
              }}
              knowledgeProps={{
                pages: knowledgePages,
                sources: knowledgeSources,
                query: knowledgeQuery,
                selectedPage: selectedKnowledgePage,
                selectedSlug: selectedKnowledgeSlug,
                selectedSource: selectedKnowledgeSource,
                selectedSourcePath: selectedKnowledgeSourcePath,
                onQueryChange: setKnowledgeQuery,
                onSelectSlug: setSelectedKnowledgeSlug,
                onSelectSource: setSelectedKnowledgeSourcePath,
                onCreatePage: handleCreateKnowledgePage,
                onEditPage: handleEditKnowledgePage,
                onQueueSource: handleQueueKnowledgeSource,
              }}
              tasksProps={{
                currentScope,
                activeTasks: scopedActiveTaskTree,
                queuedTasks: scopedQueuedTaskTree,
                finishedTasks: scopedFinishedTaskTree,
                workerStatus,
                laneStatuses,
                laneDetails,
                laneCurrentTaskTitles,
                scopeOperationalMetrics,
                scopeAttemptMetrics,
                onArchiveTask: handleArchiveTask,
                nowMs: activityNowMs,
              }}
              historyProps={{
                currentScope,
                activeTasks: scopedActiveTaskTree,
                finishedTasks: scopedFinishedTaskTree,
                messages,
                procedures,
                specProposals: specProposalSnapshot,
                evalJobs: evalJobsSnapshot,
                laneDetails,
                onOpenTask: () => setActiveNav('tasks'),
                onOpenProcedure: (procedureId) => {
                  setSelectedProcedureId(procedureId);
                  setActiveNav('procedures');
                },
                onOpenWorkbench: handleInspectWorkbenchTarget,
                onOpenSession: (nextSessionId) => {
                  const nextLane = laneForSessionId(nextSessionId);
                  setCurrentScope(nextLane);
                  setChatLane(nextLane);
                  setScopeSessionIds((prev) => ({ ...prev, [nextLane]: nextSessionId }));
                  setActiveNav('chat');
                },
              }}
              proceduresProps={{
                procedures,
                selectedProcedure,
                selectedProcedureId,
                onSelectProcedure: setSelectedProcedureId,
                onQueueProcedure: handleQueueProcedure,
                onOpenWorkbench: (target) => {
                  setWorkbenchTarget(target);
                  if (target?.procedureId) {
                    setSelectedProcedureId(target.procedureId);
                  }
                  setActiveNav('workbench');
                },
              }}
              workbenchProps={{
                apiBase: API,
                target: workbenchTarget,
                history: workbenchHistory,
                activeTasks: scopedActiveTaskTree,
                finishedTasks: scopedFinishedTaskTree,
                procedures,
                messages,
                onOpenTask: (taskId) => {
                  setWorkbenchTarget({ kind: 'task', taskId });
                  setActiveNav('workbench');
                },
                onOpenProcedure: (procedureId) => {
                  setSelectedProcedureId(procedureId);
                  setWorkbenchTarget({ kind: 'procedure', procedureId });
                  setActiveNav('workbench');
                },
                onInspectTarget: handleInspectWorkbenchTarget,
                onPauseTask: handlePauseTask,
                onResumeTask: handleResumeTask,
                onStopTask: handleStopTask,
                onReplayTask: handleReplayTask,
                onBranchTask: handleBranchTask,
                onMutateTask: handleMutateTask,
                onOpenSession: (nextSessionId) => {
                  const nextLane = laneForSessionId(nextSessionId);
                  setCurrentScope(nextLane);
                  setChatLane(nextLane);
                  setScopeSessionIds((prev) => ({ ...prev, [nextLane]: nextSessionId }));
                  setActiveNav('chat');
                },
                onSendMessage: (msg) => {
                  if (msg.simulation) {
                    setMessages(prev => [...prev, {
                      id: `sim-${Date.now()}`,
                      role: msg.role,
                      content: msg.content,
                      session_id: msg.session_id || null,
                      created_at: new Date().toISOString(),
                      message_metadata: {
                        simulation: true,
                        ...(msg.message_metadata || {}),
                      }
                    }]);
                  } else {
                    // fall back to normal send logic if needed, but workbench has its own
                  }
                },
                onSendWorkbenchPrompt: async ({ prompt, responseMode: requestedResponseMode, target, task, procedure }) => {
                  const linkedSessionId = String(target?.sessionId || '').trim();
                  const lane = normalizeLaneKey(target?.lane) || effectiveLane;
                  const targetSessionId = linkedSessionId || sessionId || defaultSessionIdForLane(lane);
                  await axios.post(`${API}/chat`, {
                    role: 'user',
                    content: prompt,
                    session_id: targetSessionId,
                    preferred_tier: lane,
                    response_mode: requestedResponseMode || responseMode,
                  });
                  setScopeSessionIds((prev) => ({ ...prev, [lane]: targetSessionId }));
                  if (task?.id) {
                    setOperatorNotice(`Workbench prompt sent for task ${task.id}`);
                  } else if (procedure?.procedure_id) {
                    setOperatorNotice(`Workbench prompt sent for procedure ${procedure.procedure_id}`);
                  } else {
                    setOperatorNotice('Workbench prompt sent');
                  }
                  await fetchData(true);
                },
              }}
            />
          </Suspense>
        ) : activeNav === 'settings' ? (
          <Suspense
            fallback={(
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#8d8ea1', fontSize: '13px' }}>
                Loading settings view...
              </div>
            )}
          >
            <LazySettingsView
              onResetDatabase={handleResetDatabase}
              apiUrl={API}
              currentScope={currentScope}
            />
          </Suspense>
        ) : (
        <div style={{ flex: 1, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', gap: '20px' }}>
          <AnimatePresence initial={false}>
            {messages.map((msg, i) => {
              const previousMessage = i > 0 ? messages[i - 1] : null;
              const groupedWithPrevious = shouldGroupMessages(msg, previousMessage, effectiveLane);
              return (
                <MemoMessageCard
                  key={msg.id || i}
                  message={msg}
                  lane={effectiveLane}
                  reactionBusyKey={reactionBusyKey}
                  openReactionMenuId={openReactionMenuId}
                  onOpenReactionMenu={setOpenReactionMenuId}
                onCloseReactionMenu={() => setOpenReactionMenuId('')}
                onReact={handleReactToMessage}
                onReply={handleReplyToMessage}
                onTypedResponse={handleTypedReactionResponse}
                showSenderTitle={!groupedWithPrevious}
                participantNames={participantNames}
              />
            );
          })}

            {messages.length === 0 && !isSending && (
              <MotionDiv
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                style={{ textAlign: 'center', color: '#333', marginTop: 'auto', marginBottom: 'auto', padding: '48px 32px' }}
              >
                <Zap size={32} color="#2a2a35" style={{ margin: '0 auto 16px' }} />
                <div style={{ fontSize: '15px', fontWeight: 600, color: '#3d3d4d', marginBottom: '6px' }}>No messages yet</div>
                <div style={{ fontSize: '13px', color: '#2d2d38' }}>Ask a question or start a conversation</div>
              </MotionDiv>
            )}

            {isSending && (
              <MotionDiv
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 0.7, y: 0 }}
                style={{ alignSelf: 'flex-start', background: '#1c1c22', padding: '12px 18px', borderRadius: '16px 16px 16px 4px', color: '#888', fontSize: '13px', fontStyle: 'italic', border: '1px solid rgba(255,255,255,0.05)' }}
              >
                <span style={{ display: 'inline-flex', gap: '4px', alignItems: 'center' }}>
                  Formation is formulating
                  {[0,1,2].map(i => (
                    <MotionSpan
                      key={i}
                      animate={{ opacity: [0.2, 1, 0.2] }}
                      transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.2 }}
                      style={{ fontSize: '18px', lineHeight: 0.6 }}
                    >·</MotionSpan>
                  ))}
                </span>
              </MotionDiv>
            )}
          </AnimatePresence>
          <div ref={messagesEndRef} />
        </div>
        )}

        {/* Input bar */}
        {activeNav === 'chat' && (
        <div style={{ padding: '20px 28px', borderTop: '1px solid rgba(255,255,255,0.05)', flexShrink: 0 }}>
          {sendError && (
            <div style={{ marginBottom: '10px', background: 'rgba(255,92,92,0.08)', border: '1px solid rgba(255,92,92,0.22)', borderRadius: '10px', padding: '10px 12px', color: '#ffb3b3', fontSize: '12px' }}>
              {sendError}
            </div>
          )}
          <div style={{ position: 'relative', background: '#141418', borderRadius: '12px', padding: '8px 10px', display: 'flex', alignItems: 'center', gap: '10px', border: '1px solid rgba(255,255,255,0.08)', transition: 'border-color 0.2s', minHeight: '52px' }}>
            <AnimatePresence>
            {(replyTarget || pendingAttachments.length > 0) && (
              <MotionDiv
                initial={{ opacity: 0, y: 4, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 4, scale: 0.98 }}
                transition={{ duration: 0.16, ease: 'easeOut' }}
                style={{
                  position: 'absolute',
                  left: '12px',
                  top: '-38px',
                  maxWidth: 'min(560px, calc(100% - 92px))',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  flexWrap: 'wrap',
                  background: 'rgba(10,10,12,0.96)',
                  border: '1px solid rgba(255,255,255,0.08)',
                  borderRadius: '999px',
                  padding: '6px 10px',
                  boxShadow: '0 12px 24px rgba(0,0,0,0.24)',
                  zIndex: 5,
                }}
              >
                {replyTarget && (
                  <>
                    <Reply size={12} color="#afb4c6" />
                    <span style={{ fontSize: '11px', color: '#d6d8e4', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '280px' }}>
                      <span style={{ color: '#afb4c6', fontWeight: 700, marginRight: '4px' }}>
                        {replyTargetSender}
                      </span>
                      <span>
                        {String(replyTarget.content || '').replace(/\s+/g, ' ').trim().slice(0, 120)}
                      </span>
                    </span>
                    <button
                      type="button"
                      onClick={() => setReplyTarget(null)}
                      style={{ background: 'none', border: 'none', color: '#8b90a5', cursor: 'pointer', display: 'flex', padding: '0', marginLeft: '2px' }}
                    >
                      <X size={12} />
                    </button>
                  </>
                )}
                {pendingAttachments.map((attachment) => (
                  <AttachmentPill
                    key={attachment.id}
                    attachment={attachment}
                    compact
                    onRemove={() => removePendingAttachment(attachment.id)}
                  />
                ))}
              </MotionDiv>
            )}
            </AnimatePresence>
            <input
              ref={attachmentInputRef}
              type="file"
              multiple
              style={{ display: 'none' }}
              onChange={(event) => {
                void addAttachments(event.target.files);
                event.target.value = '';
              }}
            />
            <button
              type="button"
              onClick={() => attachmentInputRef.current?.click()}
              style={{
                background: pendingAttachments.length > 0
                  ? 'linear-gradient(135deg, rgba(130,87,229,0.92), rgba(79,70,229,0.92))'
                  : 'linear-gradient(135deg, rgba(130,87,229,0.82), rgba(79,70,229,0.82))',
                border: 'none',
                color: '#ffffff',
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '8px',
                borderRadius: '10px',
                flexShrink: 0,
                width: '38px',
                height: '38px',
                boxShadow: '0 10px 22px rgba(79,70,229,0.18)',
              }}
              title="Attach files"
            >
              <Paperclip size={14} />
            </button>
            {currentScope === 'home' && (!sessionId || isDraftSession) && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0, paddingRight: '2px' }}>
                <span style={{ fontSize: '11px', color: '#8f94a7', fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                  To
                </span>
                <div style={{ display: 'inline-flex', borderRadius: '999px', padding: '3px', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
                  {CHAT_LANES.map((lane) => {
                    const active = chatLane === lane;
                    return (
                      <button
                        key={lane}
                        type="button"
                        onClick={() => setChatLane(lane)}
                        style={{
                          background: active
                            ? (lane === 'agent' ? 'rgba(0,187,145,0.2)' : 'rgba(130,87,229,0.22)')
                            : 'transparent',
                          color: active
                            ? (lane === 'agent' ? '#baffea' : '#dccfff')
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
                        {lane}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
              <span style={{ fontSize: '11px', color: '#8f94a7', fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                Mode
              </span>
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
            <input
              ref={inputRef}
              type="text"
              value={inputText}
              onChange={e => {
                const nextValue = e.target.value;
                setInputText(nextValue);
                if (isDraftSession) {
                  if (nextValue.trim() || pendingAttachments.length > 0) {
                    materializeDraftIfNeeded(effectiveLane, sessionId, nextValue, { attachments: pendingAttachments });
                  } else if (currentDraft) {
                    materializeDraftIfNeeded(effectiveLane, sessionId, '', { attachments: [] });
                  }
                }
              }}
              onPaste={(event) => {
                const items = Array.from(event.clipboardData?.items || []);
                const files = items
                  .map((item) => item.kind === 'file' ? item.getAsFile() : null)
                  .filter(Boolean);
                if (files.length > 0) {
                  event.preventDefault();
                  void addAttachments(files);
                }
              }}
              onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSendMessage()}
              placeholder="Ask a question or describe what you need..."
              style={{ flex: 1, background: 'transparent', border: 'none', color: '#edeeef', outline: 'none', fontSize: '14px', lineHeight: 1.5 }}
            />
            <button
              onClick={handleSendMessage}
              disabled={isSending || (!inputText.trim() && pendingAttachments.length === 0) || pendingAttachments.some((item) => String(item?.status || '').toLowerCase() === 'preparing')}
              style={{
                background: (inputText.trim() || pendingAttachments.length > 0) ? 'linear-gradient(135deg, #8257e5, #4f46e5)' : 'rgba(255,255,255,0.04)',
                border: 'none', borderRadius: '8px', padding: '10px 18px',
                color: (inputText.trim() || pendingAttachments.length > 0) ? '#fff' : '#444',
                fontWeight: 600, cursor: (inputText.trim() || pendingAttachments.length > 0) ? 'pointer' : 'default',
                display: 'flex', alignItems: 'center', gap: '7px', fontSize: '14px',
                transition: 'all 0.2s', flexShrink: 0
              }}
            >
              <Send size={15} /> Send
            </button>
          </div>
        </div>
        )}
      </section>

      {/* ── COLUMN 4: TASK FORMATION ───────────────────────────────────────── */}
      {showTaskPane && (
      <section style={{ width: '420px', display: 'flex', flexDirection: 'column', background: '#0a0a0c', flexShrink: 0 }}>
        <header style={{ padding: '12px 28px 10px', minHeight: '60px', boxSizing: 'border-box', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
          <h2 style={{ fontSize: '16px', fontWeight: 700, color: '#ffffff', margin: 0 }}>Active Tasks</h2>
        </header>

        <div style={{ flex: 1, overflowY: 'auto', padding: '20px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <Suspense
            fallback={(
              <div style={{ textAlign: 'center', color: '#8d8ea1', padding: '48px 24px', fontSize: '13px' }}>
                Loading task rail...
              </div>
            )}
          >
            <TaskPaneContent
              activeNav={activeNav}
              laneFinishedTasks={laneFinishedTasks}
              laneQueuedTasks={laneQueuedTasks}
              showFinishedTasks={showFinishedTasks}
              setShowFinishedTasks={setShowFinishedTasks}
              focusedTaskPaneTree={focusedTaskPaneTree}
              handleArchiveTask={handleArchiveTask}
              activityNowMs={activityNowMs}
              scopeHasAnyTasks={scopeHasAnyTasks}
              scopeOperationalMetrics={scopeOperationalMetrics}
              scopeAttemptMetrics={scopeAttemptMetrics}
              currentScope={currentScope}
              workerStatus={workerStatus}
              laneStatuses={laneStatuses}
              laneDetails={laneDetails}
              laneCurrentTaskTitles={laneCurrentTaskTitles}
              providerTelemetry={providerTelemetry}
              showStartupActions={currentScope === 'home' && activeNav === 'tasks' && (!sessionId || isDraftSession)}
              hasPersistedSessions={visibleSessionList.some((session) => persistedSessionHasContent(session))}
              onLoadPreviousSession={loadPreviousHomeSession}
              onStartNewSession={startNewHomeSession}
              onOpenProcedure={(procedureId) => {
                setSelectedProcedureId(procedureId);
                setActiveNav('procedures');
              }}
              onOpenTask={() => {
                setActiveNav('tasks');
              }}
              onOpenWorkbench={(target) => {
                setWorkbenchTarget(target);
                if (target?.procedureId) setSelectedProcedureId(target.procedureId);
                setActiveNav('workbench');
              }}
            />
          </Suspense>
        </div>
      </section>
      )}
      </div>

    </div>
  );
}

// ── Small helpers ──────────────────────────────────────────────────────────────
const NavIcon = ({ icon, label, active, onClick, onHover }) => {
  const IconComponent = icon;
  return (
  <button
    onClick={onClick}
    onMouseEnter={onHover}
    onFocus={onHover}
    title={label}
    style={{
      background: active ? 'rgba(130,87,229,0.15)' : 'transparent',
      border: 'none', borderRadius: '10px',
      width: '44px', height: '44px',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      cursor: 'pointer', color: active ? '#8257e5' : '#4d4d5a',
      transition: 'all 0.15s'
    }}
  >
    <IconComponent size={20} />
  </button>
  );
};

const TelemetryCell = ({ value, label }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
    <div style={{ fontSize: '16px', fontWeight: 800, color: '#edeeef', fontFamily: "'JetBrains Mono', monospace" }}>{value}</div>
    <div style={{ fontSize: '9px', color: '#444', fontWeight: 700, letterSpacing: '0.1em' }}>{label}</div>
  </div>
);

const TickerStrip = ({ text, active }) => {
  const content = String(text || '').trim();
  const containerRef = useRef(null);
  const contentRef = useRef(null);
  const [metrics, setMetrics] = useState({ shouldScroll: false, distancePx: 0 });
  const repeated = `${content}   •   ${content}`;
  if (!content) return null;

  useEffect(() => {
    const measure = () => {
      const container = containerRef.current;
      const inner = contentRef.current;
      if (!container || !inner) return;
      const singleWidth = inner.scrollWidth;
      const overflow = singleWidth > container.clientWidth - 20;
      setMetrics({
        shouldScroll: overflow,
        distancePx: overflow ? singleWidth + 24 : 0,
      });
    };
    measure();
    if (typeof window !== 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    return undefined;
  }, [content]);

  const PIXELS_PER_SECOND = 28;
  const animationDuration = metrics.shouldScroll && metrics.distancePx > 0
    ? Math.max(8, metrics.distancePx / PIXELS_PER_SECOND)
    : 0;

  return (
    <div
      ref={containerRef}
      style={{
        position: 'relative',
        overflow: 'hidden',
        borderRadius: '999px',
        border: `1px solid ${active ? 'rgba(255,255,255,0.14)' : 'rgba(255,255,255,0.08)'}`,
        background: active ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.03)',
        height: '22px',
        display: 'flex',
        alignItems: 'center',
      }}
    >
      <MotionDiv
        animate={metrics.shouldScroll ? { x: [0, -metrics.distancePx] } : { x: 0 }}
        transition={metrics.shouldScroll ? { duration: animationDuration, ease: 'linear', repeat: Infinity } : { duration: 0 }}
        style={{
          display: 'inline-flex',
          whiteSpace: 'nowrap',
          fontSize: '10px',
          color: active ? 'rgba(245,246,251,0.76)' : '#98a0b6',
          fontFamily: "'JetBrains Mono', monospace",
          paddingLeft: '10px',
          gap: '24px',
          minWidth: 'max-content',
        }}
      >
        <span ref={contentRef}>{content}</span>
        {metrics.shouldScroll && <span aria-hidden="true">{repeated}</span>}
      </MotionDiv>
    </div>
  );
};

const condenseTickerItems = (items) => {
  const source = Array.isArray(items) ? items : [];
  const condensed = [];
  const seen = new Set();
  for (const item of source) {
    const text = String(item || '').trim();
    if (!text) continue;
    if (condensed[condensed.length - 1] === text) continue;
    if (seen.has(text)) continue;
    condensed.push(text);
    seen.add(text);
  }
  return condensed;
};

const DashboardPanel = ({ title, children }) => (
  <div style={{ background: '#141418', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '14px', padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
    <div style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.12em' }}>{title}</div>
    {children}
  </div>
);

const TopModeTab = ({
  label,
  active,
  onClick,
  accent = 'neutral',
  detail = '',
  tickerItems = [],
  status = '',
  agentEnabled = true,
  agentSuppressedByGlobal = false,
  apiStatus = 'ok',
  showControls = false,
  progress = null,
  toggleChecked = false,
  togglePending = false,
  onPause,
  onResume,
  onReconnect,
  onPauseTask,
  onResumeTask,
  onStopTask,
}) => {
  const palette = {
    neutral: {
      activeBg: 'linear-gradient(135deg, rgba(255,255,255,0.12), rgba(255,255,255,0.06))',
      activeBorder: 'rgba(255,255,255,0.18)',
      activeText: '#f5f6fb',
      inactiveBg: 'rgba(255,255,255,0.04)',
      inactiveBorder: 'rgba(255,255,255,0.12)',
    },
    trainer: {
      activeBg: 'linear-gradient(135deg, rgba(205,96,52,0.3), rgba(156,63,41,0.18))',
      activeBorder: 'rgba(235,141,94,0.46)',
      activeText: '#fff1e8',
      inactiveBg: 'rgba(205,96,52,0.05)',
      inactiveBorder: 'rgba(235,141,94,0.14)',
    },
    agent: {
      activeBg: 'linear-gradient(135deg, rgba(93,131,137,0.24), rgba(63,112,103,0.18))',
      activeBorder: 'rgba(146,196,188,0.32)',
      activeText: '#ebfaf6',
      inactiveBg: 'rgba(93,131,137,0.05)',
      inactiveBorder: 'rgba(146,196,188,0.14)',
    },
  };
  const theme = palette[accent] || palette.neutral;
  const displayStatus = apiStatus === 'error'
    ? 'API DOWN'
    : agentSuppressedByGlobal && status !== 'STOPPED'
    ? 'PAUSED (GLOBAL)'
    : status === 'IDLE' && progress?.taskActionable
    ? (progress?.taskPaused ? 'TASK PAUSED' : 'READY')
    : (status || 'UNKNOWN');
  const statusColor = displayStatus === 'PAUSED'
    ? '#ffb84d'
    : displayStatus === 'RUNNING' || displayStatus === 'GENERATING'
    ? accent === 'trainer'
      ? '#ffb18c'
      : '#9ad8cd'
    : displayStatus === 'STALLED'
    ? '#ffb84d'
    : displayStatus === 'BLOCKED'
    ? '#ff9a9a'
    : displayStatus === 'QUEUED'
    ? '#8fd6ff'
    : displayStatus === 'OFFLINE'
    ? '#ff4d4d'
    : displayStatus === 'STOPPED'
    ? '#ff4d4d'
    : displayStatus === 'READY'
    ? '#8fd6ff'
    : displayStatus === 'PAUSED (GLOBAL)'
    ? '#ffb84d'
    : displayStatus === 'TASK PAUSED'
    ? '#ffb84d'
    : '#9ca1b4';
  const progressValue = Math.max(0, Math.min(100, Number(progress?.percent || 0)));
  const taskPaused = Boolean(progress?.taskPaused);
  const pathSegments = Array.isArray(progress?.pathSegments) ? progress.pathSegments : [];
  const topObjective = stripInlineMarkdown(progress?.summary || '');
  const currentObjective = stripInlineMarkdown(progress?.currentTitle || '');
  const currentStateLabel = String(progress?.currentStateLabel || '').trim();
  const currentObjectiveDisplay = currentObjective || '';
  const tickerText = condenseTickerItems(tickerItems)
    .slice(-8)
    .join('   •   ');
  const singleLineObjective = currentObjectiveDisplay || topObjective || 'No active task';
  const laneToggleDisabled = apiStatus !== 'ok' && label !== 'GLOBAL';
  const agentActivelyRunnable = agentEnabled && !agentSuppressedByGlobal && apiStatus === 'ok';
  const taskControlsEnabled = Boolean(showControls && apiStatus === 'ok' && progress?.taskActionable);
  const toggleIsActive = apiStatus === 'ok' ? toggleChecked : (label === 'GLOBAL' ? togglePending : toggleChecked);
  const agentToggleTrackBorder = apiStatus !== 'ok'
    ? 'rgba(255,255,255,0.14)'
    : agentSuppressedByGlobal
    ? 'rgba(255,255,255,0.14)'
    : agentEnabled
    ? accent === 'trainer'
      ? 'rgba(235,141,94,0.28)'
      : 'rgba(146,196,188,0.28)'
    : 'rgba(255,255,255,0.14)';
  const agentToggleTrackBg = apiStatus !== 'ok'
    ? 'rgba(255,255,255,0.06)'
    : agentSuppressedByGlobal
    ? 'rgba(255,255,255,0.08)'
    : agentEnabled
    ? accent === 'trainer'
      ? 'rgba(205,96,52,0.16)'
      : 'rgba(93,131,137,0.16)'
    : 'rgba(255,255,255,0.08)';
  const agentToggleKnobBg = apiStatus !== 'ok'
    ? '#a5abbb'
    : agentSuppressedByGlobal
    ? '#8f94a6'
    : agentEnabled
    ? accent === 'trainer'
      ? '#ffb18c'
      : '#9ad8cd'
    : '#b7bbca';
  const agentToggleKnobShadow = apiStatus !== 'ok'
    ? 'none'
    : agentSuppressedByGlobal || !agentEnabled
    ? 'none'
    : accent === 'trainer'
    ? '0 0 12px rgba(205,96,52,0.28)'
    : '0 0 12px rgba(93,131,137,0.28)';
  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onClick?.();
        }
      }}
      style={{
        flex: 1,
        minWidth: 0,
        textAlign: 'left',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
        padding: '11px 14px',
        borderRadius: '16px',
        border: active ? `1px solid ${theme.activeBorder}` : `1px solid ${theme.inactiveBorder}`,
        background: active ? theme.activeBg : theme.inactiveBg,
        color: active ? theme.activeText : '#c2c5d3',
        cursor: 'pointer',
        transition: 'all 0.18s ease',
        boxShadow: active ? '0 12px 28px rgba(0,0,0,0.22)' : 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
        <span style={{ fontSize: '12px', fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
          {label}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0, flexShrink: 0 }}>
          <span style={{ fontSize: '10px', fontWeight: 800, letterSpacing: '0.08em', color: apiStatus === 'error' ? '#ff4d4d' : statusColor, textTransform: 'uppercase', whiteSpace: 'nowrap' }}>
            {displayStatus}
          </span>
          {showControls && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
              <button
                type="button"
                role="switch"
                aria-checked={toggleIsActive}
                aria-label={`${toggleIsActive ? 'Pause' : 'Resume'} ${label} agent`}
                onClick={(event) => {
                  event.stopPropagation();
                  if (apiStatus !== 'ok') {
                    if (label === 'GLOBAL') {
                      onReconnect?.();
                    }
                    return;
                  }
                  if (toggleChecked) {
                    onPause?.();
                  } else {
                    onResume?.();
                  }
                }}
                title={apiStatus !== 'ok'
                  ? (label === 'GLOBAL' ? `Reconnect ${label}` : `${label} unavailable while API is down`)
                  : `${toggleChecked ? 'Pause' : 'Resume'} ${label} agent`}
                disabled={laneToggleDisabled}
                style={{
                  position: 'relative',
                  width: '34px',
                  height: '20px',
                  borderRadius: '999px',
                  border: `1px solid ${agentToggleTrackBorder}`,
                  background: agentToggleTrackBg,
                  cursor: laneToggleDisabled ? 'default' : 'pointer',
                  padding: 0,
                  transition: 'background 0.18s ease, border-color 0.18s ease',
                  opacity: laneToggleDisabled ? 0.6 : 1,
                }}
              >
                <span
                  style={{
                    position: 'absolute',
                    top: '2px',
                    left: toggleIsActive ? '16px' : '2px',
                    width: '14px',
                    height: '14px',
                    borderRadius: '999px',
                    background: agentToggleKnobBg,
                    boxShadow: agentToggleKnobShadow,
                    transition: 'left 0.18s ease, background 0.18s ease, box-shadow 0.18s ease',
                    animation: togglePending ? 'strata-toggle-pulse 1s ease-in-out infinite' : 'none',
                  }}
                />
              </button>
            </div>
          )}
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', fontSize: '10px' }}>
          <span style={{ color: active ? 'rgba(245,246,251,0.72)' : '#7d8296', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: '1 1 auto' }}>
            {singleLineObjective}
          </span>
          {currentStateLabel && (
            <span style={{ color: active ? 'rgba(245,246,251,0.6)' : '#7d8296', whiteSpace: 'nowrap', flexShrink: 0, textTransform: 'capitalize' }}>
              {currentStateLabel}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {pathSegments.slice(0, -1).length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
              {pathSegments.slice(0, -1).map((segment, index) => {
                const segmentPercent = Math.max(0, Math.min(100, Number(segment?.percent || 0)));
                const segmentTone = segment?.status === 'blocked'
                  ? 'linear-gradient(90deg, rgba(255,184,77,0.96), rgba(255,92,92,0.8))'
                  : accent === 'agent'
                  ? 'linear-gradient(90deg, rgba(146,196,188,0.95), rgba(93,131,137,0.82))'
                  : accent === 'trainer'
                  ? 'linear-gradient(90deg, rgba(255,177,140,0.96), rgba(205,96,52,0.9))'
                  : 'linear-gradient(90deg, rgba(255,255,255,0.88), rgba(190,196,215,0.82))';
                return (
                  <div
                    key={`${label}-crumb-${index}`}
                    style={{
                      width: '18px',
                      height: '5px',
                      borderRadius: '999px',
                      background: 'rgba(255,255,255,0.08)',
                      overflow: 'hidden',
                      opacity: 0.74,
                    }}
                  >
                    <div
                      style={{
                        width: `${segmentPercent}%`,
                        height: '100%',
                        borderRadius: '999px',
                        background: segmentTone,
                        transition: 'width 0.22s ease',
                      }}
                    />
                  </div>
                );
              })}
            </div>
          )}
          <div style={{ flex: 1, height: '5px', borderRadius: '999px', background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
            <div
              style={{
                width: `${progressValue}%`,
                height: '100%',
                borderRadius: '999px',
                background: accent === 'agent'
                  ? 'linear-gradient(90deg, rgba(146,196,188,0.95), rgba(93,131,137,0.82))'
                  : accent === 'trainer'
                  ? 'linear-gradient(90deg, rgba(255,177,140,0.96), rgba(205,96,52,0.9))'
                  : 'linear-gradient(90deg, rgba(255,255,255,0.88), rgba(190,196,215,0.82))',
              transition: 'width 0.22s ease',
            }}
          />
          </div>
          {showControls && apiStatus === 'ok' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
              {taskPaused ? (
                <button
                  onClick={(event) => { event.stopPropagation(); onResumeTask?.(); }}
                  title={`Resume current ${label} task`}
                  disabled={!taskControlsEnabled}
                  style={{
                    background: agentActivelyRunnable ? 'rgba(0,242,148,0.1)' : 'rgba(255,255,255,0.08)',
                    border: `1px solid ${agentActivelyRunnable ? (accent === 'trainer' ? 'rgba(235,141,94,0.24)' : 'rgba(146,196,188,0.24)') : 'rgba(255,255,255,0.14)'}`,
                    color: agentActivelyRunnable ? (accent === 'trainer' ? '#ffb18c' : '#9ad8cd') : '#9ea4b8',
                    borderRadius: '8px',
                    padding: '4px',
                    cursor: taskControlsEnabled ? 'pointer' : 'default',
                    display: 'flex',
                    opacity: taskControlsEnabled ? 1 : 0.24,
                    visibility: 'visible',
                  }}
                >
                  <Play size={13} fill={agentActivelyRunnable ? (accent === 'trainer' ? '#ffb18c' : '#9ad8cd') : '#9ea4b8'} />
                </button>
              ) : (
                <button
                  onClick={(event) => { event.stopPropagation(); onPauseTask?.(); }}
                  title={`Pause current ${label} task`}
                  disabled={!taskControlsEnabled}
                  style={{ background: 'rgba(255,184,77,0.1)', border: '1px solid rgba(255,184,77,0.22)', color: '#ffb84d', borderRadius: '8px', padding: '4px', cursor: taskControlsEnabled ? 'pointer' : 'default', display: 'flex', opacity: taskControlsEnabled ? 1 : 0.24, visibility: 'visible' }}
                >
                  <Pause size={13} fill="#ffb84d" />
                </button>
              )}
              <button
                onClick={(event) => { event.stopPropagation(); onStopTask?.(); }}
                title={`Cancel current ${label} task`}
                disabled={!taskControlsEnabled}
                style={{ background: 'rgba(255,77,77,0.1)', border: '1px solid rgba(255,77,77,0.22)', color: '#ff4d4d', borderRadius: '8px', padding: '4px', cursor: taskControlsEnabled ? 'pointer' : 'default', display: 'flex', opacity: taskControlsEnabled ? 1 : 0.24, visibility: 'visible' }}
              >
                <Square size={13} fill="#ff4d4d" />
              </button>
            </div>
          )}
        </div>
        <TickerStrip text={tickerText || singleLineObjective || 'No recent activity'} active={active} />
      </div>
    </div>
  );
};

const LaneToggle = ({ lane, active, onClick, compact = false }) => (
  <button
    onClick={onClick}
    style={{
      background: active
        ? lane === 'trainer'
          ? 'linear-gradient(135deg, rgba(205,96,52,0.26), rgba(156,63,41,0.18))'
          : 'linear-gradient(135deg, rgba(93,131,137,0.22), rgba(63,112,103,0.16))'
        : 'rgba(255,255,255,0.03)',
      border: active
        ? lane === 'trainer'
          ? '1px solid rgba(235,141,94,0.45)'
          : '1px solid rgba(146,196,188,0.28)'
        : '1px solid rgba(255,255,255,0.08)',
      color: active ? '#f2f6ff' : '#9a9cad',
      borderRadius: compact ? '12px' : '16px',
      padding: compact ? '10px 12px' : '12px 14px',
      fontSize: '12px',
      fontWeight: 800,
      letterSpacing: '0.08em',
      cursor: 'pointer',
      textTransform: 'uppercase',
      flex: 1,
      textAlign: 'left',
      display: 'flex',
      flexDirection: 'column',
      gap: compact ? '2px' : '4px',
      boxShadow: active ? '0 10px 28px rgba(0,0,0,0.22)' : 'none',
      transition: 'all 0.18s ease',
    }}
  >
    <span>{lane}</span>
    <span style={{ fontSize: compact ? '9px' : '10px', letterSpacing: '0.04em', textTransform: 'none', color: active ? 'rgba(242,246,255,0.8)' : '#6f7183', fontWeight: 600 }}>
      {lane === 'trainer' ? 'Bootstrap / high-capacity lane' : 'Agent-model execution lane'}
    </span>
  </button>
);

export default App;
