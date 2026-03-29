import React, { memo, useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { motion, AnimatePresence } from 'framer-motion';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Plus, Zap,
  MessageSquare, Send,
  Terminal, AlertCircle, X, Settings,
  Activity, Trash2, Database, LayoutDashboard,
  Pause, Play, Square, ChevronDown, ChevronRight, Pencil,
  BookOpen, Search, ThumbsUp, ThumbsDown, Heart, Reply, Sparkles
} from 'lucide-react';
import TaskCard from './components/TaskCard';

const MotionDiv = motion.div;
const MotionSpan = motion.span;

const API_KEY_LINKS = {
  cerebras: 'https://cloud.cerebras.ai/',
  google: 'https://aistudio.google.com/apikey',
  openrouter: 'https://openrouter.ai/settings/keys',
};

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

const PROVIDER_SETUP_LINKS = [
  { label: 'Cerebras Key', href: 'https://cloud.cerebras.ai/' },
  { label: 'Google AI Studio Key', href: 'https://aistudio.google.com/apikey' },
  { label: 'OpenRouter Keys', href: 'https://openrouter.ai/settings/keys' },
];

const CHAT_LANES = ['trainer', 'agent'];

const defaultSessionIdForLane = (lane) => `${lane}:default`;
const draftSessionIdForLane = (lane) => `${lane}:draft-${Date.now()}`;
const isDraftSessionId = (sessionId) => typeof sessionId === 'string' && /^(trainer|agent):draft-\d+$/.test(sessionId);
const draftHasContent = (draft) => {
  if (!draft) return false;
  const title = String(draft.title || '').trim();
  const body = String(draft.draftMessage || '').trim();
  return Boolean(body) || (Boolean(title) && title !== 'New Session');
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
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{display.body}</ReactMarkdown>
        </div>
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


// ─── Settings View ─────────────────────────────────────────────────────────────
const SettingsView = ({ onResetDatabase, apiUrl, currentScope = 'home' }) => {
  const [resetConfirm, setResetConfirm] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetDone, setResetDone] = useState(false);

  // Test connection state
  const [testResult, setTestResult] = useState(null); // null | { ok, latency?, error?, llm? }
  const [testing, setTesting] = useState(false);

  // Orchestrator Settings
  const [maxSyncIters, setMaxSyncIters] = useState(3);
  const [automaticTaskGeneration, setAutomaticTaskGeneration] = useState(false);
  const [testingMode, setTestingMode] = useState(false);
  const [replayPendingOnStartup, setReplayPendingOnStartup] = useState(false);
  const [allowCloudOnlyBoot, setAllowCloudOnlyBoot] = useState(false);
  const [heavyReflectionMode, setHeavyReflectionMode] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);

  // Model Registry Settings
  const [registryConfig, setRegistryConfig] = useState({ trainer: [], agent: [] });
  const [registryPresets, setRegistryPresets] = useState({ trainer: {}, agent: {} });
  const [savingRegistry, setSavingRegistry] = useState(false);

  const loadSettings = useCallback(async () => {
    try {
      const res = await axios.get(`${apiUrl}/admin/settings`);
      if (res.data.status === 'ok') {
        setMaxSyncIters(res.data.settings.max_sync_tool_iterations || 3);
        setAutomaticTaskGeneration(Boolean(res.data.settings.automatic_task_generation));
        setTestingMode(Boolean(res.data.settings.testing_mode));
        setReplayPendingOnStartup(Boolean(res.data.settings.replay_pending_tasks_on_startup));
        setAllowCloudOnlyBoot(Boolean(res.data.settings.allow_cloud_only_boot));
        setHeavyReflectionMode(Boolean(res.data.settings.heavy_reflection_mode));
      }
    } catch (e) { console.error('Failed to load settings', e); }
  }, [apiUrl]);

  const loadRegistry = useCallback(async () => {
    try {
      const res = await axios.get(`${apiUrl}/admin/registry`);
      setRegistryConfig(res.data.config);
    } catch (e) {
      console.error('Failed to load registry', e);
    }
  }, [apiUrl]);

  const loadRegistryPresets = useCallback(async () => {
    try {
      const res = await axios.get(`${apiUrl}/admin/registry/presets`);
      if (res.data.status === 'ok') {
        setRegistryPresets(res.data.presets || { trainer: {}, agent: {} });
      }
    } catch (e) {
      console.error('Failed to load registry presets', e);
    }
  }, [apiUrl]);

  useEffect(() => {
    void loadSettings();
    void loadRegistry();
    void loadRegistryPresets();
  }, [loadRegistry, loadRegistryPresets, loadSettings]);

  const handleUpdateRegistry = async (pool, field, value, index = 0) => {
    const next = { ...registryConfig };
    if (!next[pool]) next[pool] = [{}];
    if (!next[pool][index]) next[pool][index] = {};
    next[pool][index][field] = value;
    
    // Ensure default transport if missing
    if (pool === 'trainer') next[pool][index].transport = 'cloud';
    if (pool === 'agent') next[pool][index].transport = 'local';
    // Ensure default provider if missing
    if (!next[pool][index].provider) next[pool][index].provider = pool === 'trainer' ? 'openrouter' : 'lmstudio';
    
    setRegistryConfig({ ...next });
    
    setSavingRegistry(true);
    try {
      await axios.post(`${apiUrl}/admin/registry`, next);
    } catch (e) {
      console.error('Failed to save registry', e);
    } finally {
      setSavingRegistry(false);
    }
  };

  const applyPreset = async (pool, presetKey) => {
    const preset = registryPresets?.[pool]?.[presetKey];
    if (!preset) return;
    const next = { ...registryConfig, [pool]: [{ ...preset }] };
    setRegistryConfig(next);
    setSavingRegistry(true);
    try {
      await axios.post(`${apiUrl}/admin/registry`, next);
    } catch (e) {
      console.error('Failed to apply preset', e);
    } finally {
      setSavingRegistry(false);
    }
  };

  const persistSettings = async (overrides = {}) => {
    const payload = {
      max_sync_tool_iterations: parseInt(overrides.max_sync_tool_iterations ?? maxSyncIters, 10) || 3,
      automatic_task_generation: overrides.automatic_task_generation ?? automaticTaskGeneration,
      testing_mode: overrides.testing_mode ?? testingMode,
      replay_pending_tasks_on_startup: overrides.replay_pending_tasks_on_startup ?? replayPendingOnStartup,
      allow_cloud_only_boot: overrides.allow_cloud_only_boot ?? allowCloudOnlyBoot,
      heavy_reflection_mode: overrides.heavy_reflection_mode ?? heavyReflectionMode,
    };
    setSavingSettings(true);
    try {
      await axios.post(`${apiUrl}/admin/settings`, payload);
    } catch (e) {
      console.error('Failed to save settings', e);
    } finally {
      setSavingSettings(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    const start = performance.now();
    try {
      const res = await axios.get(`${apiUrl}/admin/test`, { timeout: 5000 });
      const latency = Math.round(performance.now() - start);
      setTestResult({ ok: true, latency, llm: res.data.llm_endpoint });
    } catch (error) {
      setTestResult({ ok: false, error: error.message || 'Connection failed' });
    } finally {
      setTesting(false);
    }
  };

  const handleReset = async () => {
    if (!resetConfirm) { setResetConfirm(true); return; }
    setResetting(true);
    try {
      await onResetDatabase();
      setResetDone(true);
      setResetConfirm(false);
    } catch (e) {
      console.error('Reset failed', e);
    } finally {
      setResetting(false);
    }
  };

  const sectionLabel = { fontSize: '11px', fontWeight: 700, color: '#9499ad', letterSpacing: '0.08em', marginBottom: '8px' };
  const infoValue = {
    background: '#1c1c22', border: '1px solid rgba(255,255,255,0.05)',
    borderRadius: '8px', padding: '10px 14px', color: '#ccc',
    fontSize: '13px', fontFamily: "'JetBrains Mono', monospace",
    width: '100%', outline: 'none'
  };

  const inputGroupStyle = {
    background: 'rgba(255,255,255,0.02)', padding: '12px', 
    borderRadius: '12px', border: '1px solid rgba(255,255,255,0.05)',
    display: 'flex', flexDirection: 'column', gap: '8px'
  };

  const visiblePools = currentScope === 'home' ? ['trainer', 'agent'] : [currentScope];

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
      <DashboardPanel title="SETTINGS SCOPE">
        <div style={{ fontSize: '13px', color: '#c7c8d6', lineHeight: 1.6 }}>
          {currentScope === 'home'
            ? 'Global settings view. Shared controls are shown here, along with both trainer and agent model-pool configuration.'
            : `${currentScope === 'trainer' ? 'Trainer' : 'Agent'} scope. This page is focused on settings relevant to the currently selected lane.`}
        </div>
      </DashboardPanel>

        {/* ── Connection ───────────────────────────────────────────────────── */}
        <DashboardPanel title="CONNECTION">
          <div style={sectionLabel}>CONNECTION</div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <div style={{ ...infoValue, flex: 1, color: '#888' }}>{apiUrl}</div>
            <button
              onClick={handleTest}
              disabled={testing}
              style={{
                background: testing ? 'rgba(255,255,255,0.04)' : 'rgba(130,87,229,0.15)',
                border: '1px solid rgba(130,87,229,0.3)', borderRadius: '8px',
                padding: '0 16px', height: '40px', color: '#8257e5', fontSize: '12px',
                fontWeight: 700, cursor: 'pointer', whiteSpace: 'nowrap',
                transition: 'all 0.2s', display: 'flex', alignItems: 'center', gap: '6px'
              }}
            >
              {testing ? 'Testing…' : 'Test'}
            </button>
          </div>
          {testResult && (
            <div style={{ fontSize: '11px', color: testResult.ok ? '#00f294' : '#ff4d4d', marginTop: '8px' }}>
              {testResult.ok ? `Connected in ${testResult.latency}ms` : testResult.error}
            </div>
          )}
        </DashboardPanel>

        {/* ── Model Registry ───────────────────────────────────────────────── */}
        <DashboardPanel title="MODEL REGISTRY">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
            <div style={sectionLabel}>MODEL REGISTRY</div>
            {savingRegistry && <span style={{ fontSize: '10px', color: '#8257e5', fontWeight: 700 }}>SAVING…</span>}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '12px' }}>
            {PROVIDER_SETUP_LINKS.map((link) => (
              <a
                key={link.href}
                href={link.href}
                target="_blank"
                rel="noreferrer"
                style={{
                  background: 'rgba(255,255,255,0.04)',
                  border: '1px solid rgba(255,255,255,0.08)',
                  borderRadius: '999px',
                  color: '#d8d9e6',
                  padding: '6px 10px',
                  fontSize: '11px',
                  textDecoration: 'none'
                }}
              >
                {link.label}
              </a>
            ))}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '12px' }}>
            {visiblePools.includes('trainer') && (
            <div>
              <div style={{ ...sectionLabel, marginBottom: '6px' }}>STRONG PRESETS</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {Object.keys(registryPresets.trainer || {}).map((presetKey) => (
                  <button
                    key={presetKey}
                    onClick={() => applyPreset('trainer', presetKey)}
                    style={{
                      background: 'rgba(130,87,229,0.15)',
                      border: '1px solid rgba(130,87,229,0.3)',
                      borderRadius: '999px',
                      color: '#cfc3ff',
                      padding: '6px 10px',
                      fontSize: '11px',
                      cursor: 'pointer'
                    }}
                  >
                    {presetKey}
                  </button>
                ))}
              </div>
            </div>
            )}
            {visiblePools.includes('agent') && (
            <div>
              <div style={{ ...sectionLabel, marginBottom: '6px' }}>WEAK PRESETS</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {Object.keys(registryPresets.agent || {}).map((presetKey) => (
                  <button
                    key={presetKey}
                    onClick={() => applyPreset('agent', presetKey)}
                    style={{
                      background: 'rgba(0,217,255,0.12)',
                      border: '1px solid rgba(0,217,255,0.25)',
                      borderRadius: '999px',
                      color: '#9fefff',
                      padding: '6px 10px',
                      fontSize: '11px',
                      cursor: 'pointer'
                    }}
                  >
                    {presetKey}
                  </button>
                ))}
              </div>
            </div>
            )}
          </div>
          
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {/* Trainer Pool */}
            {visiblePools.includes('trainer') && (
            <div style={inputGroupStyle}>
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#8257e5', marginBottom: '4px', letterSpacing: '0.05em' }}>STRONG POOL (CLOUD)</div>
              {registryConfig.trainer?.[0]?.provider && API_KEY_LINKS[registryConfig.trainer[0].provider] && (
                <a
                  href={API_KEY_LINKS[registryConfig.trainer[0].provider]}
                  target="_blank"
                  rel="noreferrer"
                  style={{ fontSize: '11px', color: '#bca9ff', textDecoration: 'none' }}
                >
                  Open {registryConfig.trainer[0].provider} API key page
                </a>
              )}
              <input 
                style={infoValue} placeholder="Model (e.g. anthropic/claude-3.5-sonnet)"
                value={registryConfig.trainer?.[0]?.model || ''}
                onChange={e => handleUpdateRegistry('trainer', 'model', e.target.value)}
              />
              <input 
                style={infoValue} placeholder="Endpoint URL (e.g. https://openrouter.ai/api/v1/chat/completions)"
                value={registryConfig.trainer?.[0]?.endpoint_url || ''}
                onChange={e => handleUpdateRegistry('trainer', 'endpoint_url', e.target.value)}
              />
              <input 
                style={infoValue} placeholder="API Key Env (e.g. OPENROUTER_API_KEY)"
                value={registryConfig.trainer?.[0]?.api_key_env || ''}
                onChange={e => handleUpdateRegistry('trainer', 'api_key_env', e.target.value)}
              />
              <input
                type="number"
                style={infoValue} placeholder="Requests / minute (optional)"
                value={registryConfig.trainer?.[0]?.requests_per_minute || ''}
                onChange={e => handleUpdateRegistry('trainer', 'requests_per_minute', e.target.value ? parseInt(e.target.value, 10) : null)}
              />
              <input
                type="number"
                style={infoValue} placeholder="Max concurrency (optional)"
                value={registryConfig.trainer?.[0]?.max_concurrency || ''}
                onChange={e => handleUpdateRegistry('trainer', 'max_concurrency', e.target.value ? parseInt(e.target.value, 10) : null)}
              />
              <input
                type="number"
                style={infoValue} placeholder="Min interval ms (optional)"
                value={registryConfig.trainer?.[0]?.min_interval_ms || ''}
                onChange={e => handleUpdateRegistry('trainer', 'min_interval_ms', e.target.value ? parseInt(e.target.value, 10) : null)}
              />
            </div>
            )}

            {/* Agent Pool */}
            {visiblePools.includes('agent') && (
            <div style={inputGroupStyle}>
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#00d9ff', marginBottom: '4px', letterSpacing: '0.05em' }}>WEAK POOL (LOCAL)</div>
              <input 
                style={infoValue} placeholder="Model (e.g. qwen3.5-9b-distilled)"
                value={registryConfig.agent?.[0]?.model || ''}
                onChange={e => handleUpdateRegistry('agent', 'model', e.target.value)}
              />
              <input 
                style={infoValue} placeholder="Endpoint URL (e.g. http://127.0.0.1:1234/v1/chat/completions)"
                value={registryConfig.agent?.[0]?.endpoint_url || ''}
                onChange={e => handleUpdateRegistry('agent', 'endpoint_url', e.target.value)}
              />
              <input
                type="number"
                style={infoValue} placeholder="Requests / minute (optional)"
                value={registryConfig.agent?.[0]?.requests_per_minute || ''}
                onChange={e => handleUpdateRegistry('agent', 'requests_per_minute', e.target.value ? parseInt(e.target.value, 10) : null)}
              />
              <input
                type="number"
                style={infoValue} placeholder="Max concurrency (optional)"
                value={registryConfig.agent?.[0]?.max_concurrency || ''}
                onChange={e => handleUpdateRegistry('agent', 'max_concurrency', e.target.value ? parseInt(e.target.value, 10) : null)}
              />
              <input
                type="number"
                style={infoValue} placeholder="Min interval ms (optional)"
                value={registryConfig.agent?.[0]?.min_interval_ms || ''}
                onChange={e => handleUpdateRegistry('agent', 'min_interval_ms', e.target.value ? parseInt(e.target.value, 10) : null)}
              />
            </div>
            )}
          </div>
        </DashboardPanel>

        {/* ── Orchestrator Settings ────────────────────────────────────────── */}
        <DashboardPanel title="ORCHESTRATOR">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
            <div style={sectionLabel}>ORCHESTRATOR</div>
            {savingSettings && <span style={{ fontSize: '10px', color: '#8257e5', fontWeight: 700 }}>SAVING…</span>}
          </div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <span style={{ fontSize: '13px', color: '#888', flex: 1 }}>Max Synchronous Tool Iterations</span>
            <input 
              type="number" 
              value={maxSyncIters} 
              onChange={e => setMaxSyncIters(e.target.value)}
              onBlur={e => void persistSettings({ max_sync_tool_iterations: e.target.value })}
              min="1" max="10"
              style={{ ...infoValue, width: '60px', textAlign: 'center', opacity: savingSettings ? 0.5 : 1, padding: '10px 0' }}
            />
          </div>
          <div style={{ fontSize: '11px', color: '#555', marginTop: '6px' }}>
            Limits how many times the model can independently recurse tools on a single message.
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginTop: '16px' }}>
            <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', color: '#ccc', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={automaticTaskGeneration}
                onChange={e => {
                  const checked = e.target.checked;
                  setAutomaticTaskGeneration(checked);
                  void persistSettings({ automatic_task_generation: checked });
                }}
              />
              <span>
                Automatically generate tasks
                <div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>
                  Lets the chat model spawn background research and implementation tasks on its own. Default is off for quieter testing.
                </div>
              </span>
            </label>
            <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', color: '#ccc', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={testingMode}
                onChange={e => {
                  const checked = e.target.checked;
                  setTestingMode(checked);
                  void persistSettings({ testing_mode: checked });
                }}
              />
              <span>
                Testing mode
                <div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>
                  Suppresses autonomous idle task generation so you can run focused evaluations without extra noise.
                </div>
              </span>
            </label>
            <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', color: '#ccc', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={replayPendingOnStartup}
                onChange={e => {
                  const checked = e.target.checked;
                  setReplayPendingOnStartup(checked);
                  void persistSettings({ replay_pending_tasks_on_startup: checked });
                }}
              />
              <span>
                Replay pending backlog on startup
                <div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>
                  Re-enqueues old pending tasks after a reboot. Leave this off unless you intentionally want to resume backlog work.
                </div>
              </span>
            </label>
            <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', color: '#ccc', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={allowCloudOnlyBoot}
                onChange={e => {
                  const checked = e.target.checked;
                  setAllowCloudOnlyBoot(checked);
                  void persistSettings({ allow_cloud_only_boot: checked });
                }}
              />
              <span>
                Allow cloud-only boot
                <div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>
                  Lets the worker start on the trainer tier when the local agent endpoint is unavailable instead of failing startup.
                </div>
              </span>
            </label>
            <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', color: '#ccc', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={heavyReflectionMode}
                onChange={e => {
                  const checked = e.target.checked;
                  setHeavyReflectionMode(checked);
                  void persistSettings({ heavy_reflection_mode: checked });
                }}
              />
              <span>
                Heavy reflection mode
                <div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>
                  Makes the trainer lane seed larger bootstrap supervision batches when idle so overnight runs synthesize telemetry faster.
                </div>
              </span>
            </label>
          </div>
        </DashboardPanel>

        {/* ── Danger Zone ──────────────────────────────────────────────────── */}
        <DashboardPanel title="DANGER ZONE">
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Database size={14} color="#ff4d4d" />
            <span style={{ fontSize: '11px', fontWeight: 700, color: '#ff4d4d', letterSpacing: '0.08em' }}>DANGER ZONE</span>
          </div>
          <div style={{
            background: 'rgba(255,77,77,0.04)', border: '1px solid rgba(255,77,77,0.15)',
            borderRadius: '12px', padding: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center'
          }}>
            <div>
              <div style={{ fontSize: '14px', fontWeight: 600, color: '#edeeef', marginBottom: '4px' }}>Fresh Start</div>
              <div style={{ fontSize: '12px', color: '#888' }}>Stops active work, clears runtime state, wipes task history, and leaves the worker paused.</div>
            </div>
            <button
              onClick={handleReset}
              disabled={resetting}
              style={{
                background: resetConfirm ? 'rgba(255,77,77,0.8)' : 'rgba(255,77,77,0.15)',
                border: '1px solid rgba(255,77,77,0.4)',
                borderRadius: '8px', padding: '8px 16px',
                color: resetConfirm ? '#fff' : '#ff4d4d',
                fontSize: '12px', fontWeight: 700, cursor: 'pointer',
                whiteSpace: 'nowrap', transition: 'all 0.2s'
              }}
            >
              {resetting ? 'Refreshing…' : resetDone ? '✓ Done' : resetConfirm ? 'Confirm Fresh Start' : 'Fresh Start'}
            </button>
          </div>
        </DashboardPanel>
    </div>
  );
};

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
  const [reactionBusyKey, setReactionBusyKey] = useState('');
  const [openReactionMenuId, setOpenReactionMenuId] = useState('');
  const [replyTarget, setReplyTarget] = useState(null);
  const [chatLane, setChatLane]       = useState('trainer');
  const [currentScope, setCurrentScope] = useState('trainer');
  const [scopeSessionIds, setScopeSessionIds] = useState({
    home: null,
    trainer: defaultSessionIdForLane('trainer'),
    agent: defaultSessionIdForLane('agent'),
  });
  const [sessionList, setSessionList] = useState([]);
  const [activeNav, setActiveNav]     = useState('chat');   // 'chat' | 'tasks' | 'knowledge' | 'dashboard' | 'settings'
  const [apiStatus, setApiStatus]     = useState('connecting'); // 'ok' | 'error' | 'connecting'
  const [workerApiStatus, setWorkerApiStatus] = useState('connecting'); // 'ok' | 'error' | 'connecting'
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const sessionListRef = useRef([]);
  const isSendingRef = useRef(false);
  const fetchGenRef = useRef(0);       // generation counter for stale-poll rejection
  const fetchPromiseRef = useRef(null);
  const pendingFetchRef = useRef(false);
  const workerStatusPromiseRef = useRef(null);
  const pendingWorkerStatusRef = useRef(false);
  const refreshTimerRef = useRef(null);
  const [activityNowMs, setActivityNowMs] = useState(() => Date.now());
  const [workerStatus, setWorkerStatus] = useState('RUNNING'); // RUNNING, PAUSED, STOPPED
  const [laneStatuses, setLaneStatuses] = useState({ trainer: 'IDLE', agent: 'IDLE' });
  const [globalPaused, setGlobalPaused] = useState(false);
  const [pausedLanes, setPausedLanes] = useState([]);
  const [rebooting, setRebooting] = useState(false);
  const [telemetry, setTelemetry] = useState(null);
  const [providerTelemetry, setProviderTelemetry] = useState({});
  const [dashboard, setDashboard] = useState(null);
  const [loadedContext, setLoadedContext] = useState({ files: [], budget_tokens: 0 });
  const [routingSummary, setRoutingSummary] = useState(null);
  const [specsSnapshot, setSpecsSnapshot] = useState(null);
  const [specProposalSnapshot, setSpecProposalSnapshot] = useState([]);
  const [knowledgePagesSnapshot, setKnowledgePagesSnapshot] = useState([]);
  const [knowledgeQuery, setKnowledgeQuery] = useState('');
  const [knowledgePages, setKnowledgePages] = useState([]);
  const [selectedKnowledgeSlug, setSelectedKnowledgeSlug] = useState('');
  const [selectedKnowledgePage, setSelectedKnowledgePage] = useState(null);
  const [retentionSnapshot, setRetentionSnapshot] = useState(null);
  const [variantRatingsSnapshot, setVariantRatingsSnapshot] = useState(null);
  const [predictionTrustSnapshot, setPredictionTrustSnapshot] = useState(null);
  const [proposalConfigSnapshot, setProposalConfigSnapshot] = useState(null);
  const [evalJobsSnapshot, setEvalJobsSnapshot] = useState([]);
  const [operatorNotice, setOperatorNotice] = useState('');
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

  useEffect(() => {
    sessionListRef.current = sessionList;
  }, [sessionList]);

  useEffect(() => {
    setOpenReactionMenuId('');
  }, [messages, sessionId]);

  useEffect(() => {
    if (isDraftSession) {
      setInputText(currentDraft?.draftMessage || '');
      setMessages([]);
      setSendError('');
      return;
    }
      setInputText('');
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
        const nextTiers = normalizeTierStatusMap(res.data.status.tiers);
        setLaneStatuses(nextLaneStatuses);
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

  useEffect(() => {
    const timer = setTimeout(() => {
      void fetchWorkerStatus();
    }, 0);
    return () => {
      clearTimeout(timer);
    };
  }, [fetchWorkerStatus]);

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
      await Promise.all([fetchWorkerStatus(), fetchData(true)]);
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
        const needsDashboardData = activeNav === 'dashboard';
        const needsChatBannerData = activeNav === 'chat';
        const shouldLoadDashboardSnapshot = needsDashboardData || needsChatBannerData;
        const [tasksRes, msgsRes, sessionsRes, telemetryRes, providerTelemetryRes, dashboardRes, loadedContextRes, routingRes, specsRes, specProposalsRes, knowledgePagesRes, retentionRes, variantRatingsRes, predictionTrustRes, proposalConfigRes, evalJobsRes] = await Promise.all([
          axios.get(`${API}/tasks`),
          !sessionId || isDraftSession ? Promise.resolve({ data: [] }) : axios.get(`${API}/messages?session_id=${sessionId}`),
          axios.get(`${API}/sessions`, { params: sessionParams }),
          needsDashboardData ? axios.get(`${API}/admin/telemetry?limit=8`) : Promise.resolve({ data: { telemetry: null } }),
          needsDashboardData ? axios.get(`${API}/admin/providers/telemetry`) : Promise.resolve({ data: { providers: {} } }),
          shouldLoadDashboardSnapshot ? axios.get(`${API}/admin/dashboard?limit=6`) : Promise.resolve({ data: { dashboard: null } }),
          needsDashboardData ? axios.get(`${API}/admin/context/loaded`) : Promise.resolve({ data: { loaded: { files: [], budget_tokens: 0 } } }),
          axios.get(`${API}/admin/routing`),
          needsDashboardData ? axios.get(`${API}/admin/specs`) : Promise.resolve({ data: { specs: null } }),
          needsDashboardData ? axios.get(`${API}/admin/spec_proposals?limit=6`) : Promise.resolve({ data: { proposals: [] } }),
          needsDashboardData ? axios.get(`${API}/admin/knowledge/pages?limit=6&audience=operator`) : Promise.resolve({ data: { pages: [] } }),
          needsDashboardData ? axios.get(`${API}/admin/storage/retention`) : Promise.resolve({ data: null }),
          needsDashboardData ? axios.get(`${API}/admin/variants/ratings`) : Promise.resolve({ data: null }),
          needsDashboardData ? axios.get(`${API}/admin/predictions/trust`) : Promise.resolve({ data: null }),
          needsDashboardData ? axios.get(`${API}/admin/evals/proposal_config`) : Promise.resolve({ data: null }),
          needsDashboardData ? axios.get(`${API}/admin/evals/jobs`) : Promise.resolve({ data: null })
        ]);

        // If a newer fetch was launched while we were awaiting, discard this result
        if (gen !== fetchGenRef.current) return;

        setTasks(tasksRes.data);
        setMessages(msgsRes.data);
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
        setSessionList(sessions);
        setTelemetry(telemetryRes.data.telemetry);
        setProviderTelemetry(providerTelemetryRes.data.providers || {});
        setDashboard(dashboardRes.data.dashboard || null);
        setLoadedContext(loadedContextRes.data.loaded || { files: [], budget_tokens: 0 });
        setRoutingSummary(normalizeRoutingSummary(routingRes.data.routing));
        setSpecsSnapshot(specsRes.data.specs || null);
        setSpecProposalSnapshot(specProposalsRes.data.proposals || []);
        setKnowledgePagesSnapshot(knowledgePagesRes.data.pages || []);
        setRetentionSnapshot(retentionRes.data || null);
        setVariantRatingsSnapshot(variantRatingsRes?.data?.ratings || null);
        setPredictionTrustSnapshot(predictionTrustRes?.data?.trust || null);
        setProposalConfigSnapshot(proposalConfigRes?.data?.config || null);
        setEvalJobsSnapshot(evalJobsRes?.data?.jobs || []);
        setApiStatus('ok');
        if (activeNav === 'chat' && currentScope === 'home' && !sessionId && sessions.length) {
          setScopeSessionIds((prev) => ({ ...prev, home: sessions[0].session_id }));
        }
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

  const scheduleRefresh = useCallback((force = false) => {
    pendingFetchRef.current = pendingFetchRef.current || force;
    pendingWorkerStatusRef.current = true;
    if (refreshTimerRef.current != null) return;
    refreshTimerRef.current = window.setTimeout(() => {
      refreshTimerRef.current = null;
      const shouldForceFetch = pendingFetchRef.current;
      pendingFetchRef.current = false;
      pendingWorkerStatusRef.current = false;
      void Promise.all([
        fetchWorkerStatus(),
        fetchData(shouldForceFetch),
      ]);
    }, 100);
  }, [fetchData, fetchWorkerStatus]);

  useEffect(() => {
    scheduleRefresh(true);

    let fallbackInterval = null;
    const es = new EventSource(`${API}/events`);

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (fallbackInterval) {
          clearInterval(fallbackInterval);
          fallbackInterval = null;
        }
        if (data.type === 'task_update' || data.type === 'message' || data.type === 'worker_status') {
          scheduleRefresh(true);
        }
      } catch (err) {
        console.error('SSE Parse Error:', err);
      }
    };

    es.onerror = (err) => {
      console.error('SSE Error:', err);
      if (!fallbackInterval) {
        fallbackInterval = setInterval(() => {
          scheduleRefresh(true);
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
        const nextPages = pagesRes.data.pages || [];
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

    void loadKnowledge();
    return () => {
      cancelled = true;
    };
  }, [API, activeNav, knowledgeQuery, selectedKnowledgeSlug]);

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

  const handleSendMessage = async () => {
    if (!inputText.trim() || isSending) return;
    const text = inputText;
    const replyPrefix = replyTarget
      ? `Replying to "${String(replyTarget.content || '').replace(/\s+/g, ' ').trim().slice(0, 120)}": `
      : '';
    const outboundText = `${replyPrefix}${text}`;
    const tempId = `temp-${Date.now()}`;
    setInputText('');
    if (isDraftSession) {
      setLaneDrafts((prev) => ({
        ...prev,
        [effectiveLane]: (prev[effectiveLane] || []).map((draft) => (
          draft?.session_id === sessionId
            ? { ...draft, draftMessage: '', last_message_preview: 'Draft' }
            : draft
        )),
      }));
    }
    setSendError('');
    setIsSending(true);
    isSendingRef.current = true;
    const targetSessionId = isDraftSession ? `${effectiveLane}:session-${Date.now()}` : sessionId;
    // Optimistic update: show the user's message immediately
    setMessages(prev => [...prev, { id: tempId, role: 'user', content: outboundText, pending: true }]);
    try {
      await axios.post(`${API}/chat`, {
        role: 'user',
        content: outboundText,
        session_id: targetSessionId,
        preferred_tier: effectiveLane,
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
        setLaneDrafts((prev) => ({
          ...prev,
          [effectiveLane]: (prev[effectiveLane] || []).map((draft) => (
            draft?.session_id === sessionId
              ? { ...draft, draftMessage: outboundText, last_message_preview: outboundText }
              : draft
          )),
        }));
        setInputText(outboundText);
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
    if (activeDraft && !draftHasContent(activeDraft)) {
      setScopeSessionIds(prev => ({ ...prev, [currentScope]: activeDraft.session_id }));
      setMessages([]);
      setInputText(activeDraft.draftMessage || '');
      setSendError('');
      return;
    }
    const draftId = draftSessionIdForLane(draftLane);
    setLaneDrafts((prev) => ({
      ...prev,
      [draftLane]: [{
        session_id: draftId,
        title: 'New Session',
        draft: true,
        draftMessage: '',
        unread_count: 0,
        created_at: new Date().toISOString(),
        last_message_at: null,
        last_message_preview: 'Draft',
        session_metadata: {
          participant_names: { user: 'You', trainer: 'Trainer', agent: 'Agent', system: 'System' },
        },
      }, ...(prev[draftLane] || [])],
    }));
    setScopeSessionIds(prev => ({ ...prev, [currentScope]: draftId }));
    setMessages([]);
    setInputText('');
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
        setScopeSessionIds(prev => ({ ...prev, [currentScope]: currentScope === 'home' ? null : defaultSessionIdForLane(draftLane) }));
        setMessages([]);
        setInputText('');
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
      });
      await fetchData(true);
    } catch (err) {
      console.error('Failed to send typed response.', err);
    }
  }, [API, effectiveLane, fetchData, sessionId]);

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
  const specPendingCount = dashboard?.spec_governance?.pending_count ?? 0;
  const specClarificationCount = dashboard?.spec_governance?.clarification_count ?? 0;

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

  // Build and sort Task Tree
  const taskTree = React.useMemo(() => {
    const map = {};
    const filteredTasks = tasks.filter(t => !archivedTasks.includes(t.id));

    // First pass: create nodes
    filteredTasks.forEach(t => {
      map[t.id] = { ...t, children: [] };
    });
    
    const roots = [];
    // Second pass: link children with cycle detection
    filteredTasks.forEach(t => {
      if (t.parent_id && map[t.parent_id] && t.parent_id !== t.id) {
        map[t.parent_id].children.push(map[t.id]);
      } else if (!t.parent_id || t.parent_id === t.id) {
        roots.push(map[t.id]);
      }
    });

    roots.sort((a, b) => String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || '')));

    return roots;
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
    { id: 'knowledge', Icon: BookOpen,        label: 'Knowledge' },
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
  const laneVisibleTasks = activeNav === 'chat' && currentScope !== 'home'
    ? scopedActiveTaskTree
    : activeTaskTree;
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
      if (currentScope === 'home') return Boolean(explicitLaneForSessionId(session.session_id));
      return sessionMatchesLane(session.session_id, effectiveLane);
    });
    return [...drafts, ...persisted];
  }, [currentScope, effectiveLane, laneDrafts, sessionList]);
  const buildLaneMeta = (lane) => {
    const route = routingSummary?.[lane] || null;
    if (route?.error) return route.error;
    const parts = [
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
  const getFocusedTaskPath = (scopeId) => {
    if (scopeId === 'home') return [];
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

    const path = getFocusedTaskPath(scopeId);
    const root = path[0];
    if (!root) {
      const blocked = tasks.filter((task) => laneForTask(task) === scopeId && task.status === 'blocked').length;
      return {
        percent: 0,
        summary: buildLaneMeta(scopeId),
        currentTitle: blocked > 0 ? `${blocked} blocked` : 'Idle',
        label: blocked > 0 ? `${blocked} blocked` : 'Idle',
        countLabel: blocked > 0 ? 'Needs attention' : 'No active task',
        pathSegments: [],
        taskActionable: blocked > 0,
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

    return {
      percent: localPercent,
      summary: rootTitle,
      currentTitle: leafTitle,
      currentStateLabel: leafPaused
        ? 'paused'
        : blockedDescendants > 0
        ? 'blocked'
        : leaf.status === 'pending'
        ? 'queued'
        : leaf.status === 'working'
        ? 'working'
        : '',
      label: leafPaused
        ? `${leafTitle} · paused`
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
  const focusedTaskPaneTree = React.useMemo(() => {
    if (activeNav === 'dashboard' || currentScope === 'home') return visibleTaskTree;
    const path = getFocusedTaskPath(currentScope);
    if (!path.length) return visibleTaskTree;
    const contextNode = path.length > 1 ? path[path.length - 2] : path[0];
    return contextNode ? [contextNode] : visibleTaskTree;
  }, [activeNav, currentScope, visibleTaskTree, activeTaskTree]);
  const loopMeta = routingSummary?.supervision?.active_jobs?.length
    ? `${routingSummary.supervision.active_jobs.length} active bootstrap job${routingSummary.supervision.active_jobs.length > 1 ? 's' : ''}`
    : 'loop idle';

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
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', width: '100%' }}>
          <button
            type="button"
            onClick={() => {
              setCurrentScope('home');
              setActiveNav('dashboard');
              setScopeSessionIds((prev) => ({ ...prev, home: prev.home || sessionId }));
            }}
            style={{
              minWidth: '180px',
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
            </div>
          </button>
          <div style={{ display: 'flex', gap: '12px', width: '100%' }}>
          {topTabs.map((tab) => {
            const active = currentScope === tab.id;
            const showGlobalControls = tab.id === 'home';
            const isLane = tab.id === 'trainer' || tab.id === 'agent';
            const progress = buildScopeTaskProgress(tab.id);
            const agentEnabled = tab.id === 'home'
              ? (workerStatus !== 'PAUSED' && workerStatus !== 'STOPPED')
              : !pausedLanes.includes(tab.id);
            const agentSuppressedByGlobal = isLane && globalPaused;
            return (
              <TopModeTab
                key={tab.id}
                label={tab.label}
                accent={tab.accent}
                active={active}
                detail={tab.id === 'home' ? loopMeta : buildLaneMeta(tab.id)}
                status={tab.id === 'home' ? workerStatus : (laneStatuses?.[tab.id] || 'IDLE')}
                agentEnabled={agentEnabled}
                agentSuppressedByGlobal={agentSuppressedByGlobal}
                apiStatus={workerApiStatus}
                showControls={workerApiStatus === 'ok' && (isLane || showGlobalControls)}
                progress={progress}
                onPause={showGlobalControls ? (() => handlePause()) : (isLane ? (() => handlePause(tab.id)) : undefined)}
                onResume={showGlobalControls ? (() => handleResume()) : (isLane ? (() => handleResume(tab.id)) : undefined)}
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
                  : activeNav === 'tasks'
                  ? (currentScope === 'home' ? 'Tasks' : `${currentScope === 'trainer' ? 'Trainer' : 'Agent'} Tasks`)
                  : activeNav === 'knowledge'
                  ? (currentScope === 'home' ? 'Knowledge Base' : `${currentScope === 'trainer' ? 'Trainer' : 'Agent'} Knowledge`)
                  : 'Settings'}
              </h1>
            )}
            {activeNav !== 'chat' && (
              <p style={{ fontSize: '12px', color: '#555', marginTop: '2px' }}>
                {activeNav === 'dashboard'
                ? (currentScope === 'home' ? 'Shared telemetry, routing, and operator surfaces' : 'Scoped operational telemetry and runtime detail')
                : activeNav === 'tasks'
                ? (currentScope === 'home' ? 'Canonical queue, execution, and completion view' : 'Scoped task queue, execution progress, and recent completions')
                : activeNav === 'knowledge'
                ? (currentScope === 'home' ? 'Navigable system wiki' : 'Knowledge visible within this scope')
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
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginLeft: '18px', flexShrink: 0 }}>
            {apiStatus === 'error' && (
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <span style={{ fontSize: '11px', color: '#ff4d4d', fontWeight: 600 }}>API DOWN</span>
                <button 
                  onClick={() => {
                    setApiStatus('connecting');
                    setWorkerApiStatus('connecting');
                    void Promise.all([fetchWorkerStatus(), fetchData(true)]);
                  }}
                  style={{ background: 'rgba(130,87,229,0.15)', border: '1px solid rgba(130,87,229,0.3)', color: '#8257e5', borderRadius: '4px', padding: '2px 8px', fontSize: '10px', fontWeight: 800, cursor: 'pointer' }}
                >
                  RECONNECT
                </button>
                <button 
                  onClick={handleReboot}
                  disabled={rebooting}
                  style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border)', color: '#ccc', borderRadius: '4px', padding: '2px 8px', fontSize: '10px', fontWeight: 800, cursor: 'pointer' }}
                >
                  {rebooting ? '...' : 'REBOOT'}
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

        {activeNav === 'dashboard' ? (
          <DashboardView
            telemetry={telemetry}
            dashboard={dashboard}
            providerTelemetry={providerTelemetry}
            loadedContext={loadedContext}
            tiers={tiers}
            routingSummary={routingSummary}
            currentScope={currentScope}
            chatLane={effectiveLane}
            activeChatRoute={activeChatRoute}
            scopeTasks={tasksForScope}
            scopeOperationalMetrics={scopeOperationalMetrics}
            scopeAttemptMetrics={scopeAttemptMetrics}
            specsSnapshot={specsSnapshot}
            specProposalSnapshot={specProposalSnapshot}
            knowledgePagesSnapshot={knowledgePagesSnapshot}
            retentionSnapshot={retentionSnapshot}
            variantRatingsSnapshot={variantRatingsSnapshot}
            predictionTrustSnapshot={predictionTrustSnapshot}
            proposalConfigSnapshot={proposalConfigSnapshot}
            evalJobsSnapshot={evalJobsSnapshot}
            operatorNotice={operatorNotice}
            onRunRetention={handleRunRetention}
            onCompactKnowledge={handleCompactKnowledge}
            onContextScan={handleContextScan}
            onQueueBootstrap={handleQueueBootstrap}
            onQueueSampleTick={handleQueueSampleTick}
            onResolveSpecProposal={handleResolveSpecProposal}
          />
        ) : activeNav === 'knowledge' ? (
          <KnowledgeView
            pages={knowledgePages}
            query={knowledgeQuery}
            selectedPage={selectedKnowledgePage}
            selectedSlug={selectedKnowledgeSlug}
            onQueryChange={setKnowledgeQuery}
            onSelectSlug={setSelectedKnowledgeSlug}
            onCreatePage={handleCreateKnowledgePage}
            onEditPage={handleEditKnowledgePage}
            onQueueSource={handleQueueKnowledgeSource}
          />
        ) : activeNav === 'tasks' ? (
          <TasksView
            currentScope={currentScope}
            activeTasks={scopedActiveTaskTree}
            finishedTasks={scopedFinishedTaskTree}
            workerStatus={workerStatus}
            laneStatuses={laneStatuses}
            scopeOperationalMetrics={scopeOperationalMetrics}
            scopeAttemptMetrics={scopeAttemptMetrics}
            onArchiveTask={handleArchiveTask}
            nowMs={activityNowMs}
          />
        ) : activeNav === 'settings' ? (
          <SettingsView
            onResetDatabase={handleResetDatabase}
            apiUrl={API}
            currentScope={currentScope}
          />
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
                <div style={{ fontSize: '13px', color: '#2d2d38' }}>Describe a goal to get started</div>
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
          <div style={{ position: 'relative', background: '#141418', borderRadius: '12px', padding: '8px 8px 8px 16px', display: 'flex', alignItems: 'center', gap: '10px', border: '1px solid rgba(255,255,255,0.08)', transition: 'border-color 0.2s', minHeight: '52px' }}>
            <AnimatePresence>
            {replyTarget && (
              <MotionDiv
                initial={{ opacity: 0, y: 4, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 4, scale: 0.98 }}
                transition={{ duration: 0.16, ease: 'easeOut' }}
                style={{
                  position: 'absolute',
                  left: '12px',
                  top: '-16px',
                  maxWidth: 'min(360px, calc(100% - 92px))',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  background: 'rgba(10,10,12,0.96)',
                  border: '1px solid rgba(255,255,255,0.08)',
                  borderRadius: '999px',
                  padding: '6px 10px',
                  boxShadow: '0 12px 24px rgba(0,0,0,0.24)',
                  zIndex: 5,
                }}
              >
                <Reply size={12} color="#afb4c6" />
                <span style={{ fontSize: '11px', color: '#d6d8e4', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
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
              </MotionDiv>
            )}
            </AnimatePresence>
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
            <input
              ref={inputRef}
              type="text"
              value={inputText}
              onChange={e => {
                const nextValue = e.target.value;
                setInputText(nextValue);
                if (isDraftSession) {
                  setLaneDrafts((prev) => ({
                    ...prev,
                    [effectiveLane]: (prev[effectiveLane] || []).map((draft) => (
                      draft?.session_id === sessionId
                        ? {
                            ...draft,
                            draftMessage: nextValue,
                            last_message_preview: nextValue.trim() || 'Draft',
                          }
                        : draft
                    )),
                  }));
                }
              }}
              onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSendMessage()}
              placeholder="Describe a goal..."
              style={{ flex: 1, background: 'transparent', border: 'none', color: '#edeeef', outline: 'none', fontSize: '14px', lineHeight: 1.5 }}
            />
            <button
              onClick={handleSendMessage}
              disabled={isSending || !inputText.trim()}
              style={{
                background: inputText.trim() ? 'linear-gradient(135deg, #8257e5, #4f46e5)' : 'rgba(255,255,255,0.04)',
                border: 'none', borderRadius: '8px', padding: '10px 18px',
                color: inputText.trim() ? '#fff' : '#444',
                fontWeight: 600, cursor: inputText.trim() ? 'pointer' : 'default',
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
          {activeNav !== 'dashboard' && laneFinishedTasks.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <button
                onClick={() => setShowFinishedTasks(!showFinishedTasks)}
                style={{
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.05)',
                  color: '#8f90a3',
                  borderRadius: '10px',
                  padding: '10px 12px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  cursor: 'pointer',
                  fontSize: '11px',
                  fontWeight: 800,
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase'
                }}
              >
                <span>Recent Finished · {laneFinishedTasks.length}</span>
                {showFinishedTasks ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>
              <AnimatePresence>
                {showFinishedTasks && (
                  <MotionDiv
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column', gap: '10px' }}
                  >
                    {laneFinishedTasks.map(task => (
                      <TaskCard key={task.id} task={task} onArchive={() => handleArchiveTask(task.id)} nowMs={activityNowMs} />
                    ))}
                  </MotionDiv>
                )}
              </AnimatePresence>
            </div>
          )}
          <AnimatePresence>
            {focusedTaskPaneTree.map(task => (
              <TaskCard key={task.id} task={task} onArchive={() => handleArchiveTask(task.id)} nowMs={activityNowMs} />
            ))}
          </AnimatePresence>

          {focusedTaskPaneTree.length === 0 && (
            <div style={{ textAlign: 'center', color: '#2d2d38', padding: '48px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px' }}>
              <Terminal size={28} color="#222228" />
              <div style={{ fontSize: '13px', color: '#333' }}>
                {laneFinishedTasks.length > 0 || scopeHasAnyTasks ? 'No current tasks' : 'No tasks yet'}
              </div>
            </div>
          )}
        </div>

        {/* Telemetry — computed from scoped live data */}
        <div style={{ padding: '16px 20px', borderTop: '1px solid rgba(255,255,255,0.05)', background: '#0c0c0e', flexShrink: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
            <span style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.12em' }}>OPERATIONAL TELEMETRY</span>
            <Terminal size={12} style={{ color: '#333' }} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px', marginBottom: '12px' }}>
            <TelemetryCell value={scopeOperationalMetrics.working || '—'} label="TASKS RUNNING NOW" />
            <TelemetryCell value={scopeOperationalMetrics.queued || '—'} label="TASKS QUEUED NEXT" />
            <TelemetryCell value={scopeOperationalMetrics.blocked || '—'} label="TASKS BLOCKED" />
            <TelemetryCell value={scopeOperationalMetrics.needsYou || '—'} label="TASKS WAITING ON YOU" />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px', marginBottom: '12px' }}>
            <TelemetryCell value={scopeAttemptMetrics.success10 || '—'} label="SUCCEEDED OF LAST 10 ATTEMPTS" />
            <TelemetryCell value={scopeAttemptMetrics.success50 || '—'} label="SUCCEEDED OF LAST 50 ATTEMPTS" />
            <TelemetryCell value={scopeAttemptMetrics.averageDurationLabel || '—'} label="AVERAGE TIME PER ATTEMPT" />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', fontSize: '11px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
              <span style={{ color: '#7f8091' }}>context</span>
              <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>{scopeOperationalMetrics.loadedContextCount} files · {scopeOperationalMetrics.loadedContextBudget} tok</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
              <span style={{ color: '#7f8091' }}>scope health</span>
              <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
                {currentScope === 'home' ? workerStatus : (laneStatuses?.[currentScope] || 'IDLE')}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
              <span style={{ color: '#7f8091' }}>transport wait</span>
              <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
                {Object.values(providerTelemetry || {})[0]?.avg_wait_ms ?? '—'}ms
              </span>
            </div>
          </div>
        </div>
      </section>
      )}
      </div>

    </div>
  );
}

// ── Small helpers ──────────────────────────────────────────────────────────────
const NavIcon = ({ icon, label, active, onClick }) => {
  const IconComponent = icon;
  return (
  <button
    onClick={onClick}
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

const TopModeTab = ({
  label,
  active,
  onClick,
  accent = 'neutral',
  detail = '',
  status = '',
  agentEnabled = true,
  agentSuppressedByGlobal = false,
  apiStatus = 'ok',
  showControls = false,
  progress = null,
  onPause,
  onResume,
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
    : displayStatus === 'RUNNING'
    ? accent === 'trainer'
      ? '#ffb18c'
      : '#9ad8cd'
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
  const topObjective = stripInlineMarkdown(progress?.summary || detail || '');
  const currentObjective = stripInlineMarkdown(progress?.currentTitle || '');
  const currentStateLabel = String(progress?.currentStateLabel || '').trim();
  const currentObjectiveDisplay = currentObjective
    ? `${currentObjective}${currentStateLabel ? ` · ${currentStateLabel}` : ''}`
    : '';
  const showSplitObjectives = Boolean(topObjective && currentObjective && topObjective !== currentObjective);
  const singleLineObjective = currentObjectiveDisplay || topObjective || 'No active task';
  const agentActivelyRunnable = agentEnabled && !agentSuppressedByGlobal && apiStatus === 'ok';
  const taskControlsEnabled = Boolean(showControls && apiStatus === 'ok' && progress?.taskActionable);
  const agentToggleTrackBorder = agentSuppressedByGlobal
    ? 'rgba(255,255,255,0.14)'
    : agentEnabled
    ? accent === 'trainer'
      ? 'rgba(235,141,94,0.28)'
      : 'rgba(146,196,188,0.28)'
    : 'rgba(255,255,255,0.14)';
  const agentToggleTrackBg = agentSuppressedByGlobal
    ? 'rgba(255,255,255,0.08)'
    : agentEnabled
    ? accent === 'trainer'
      ? 'rgba(205,96,52,0.16)'
      : 'rgba(93,131,137,0.16)'
    : 'rgba(255,255,255,0.08)';
  const agentToggleKnobBg = agentSuppressedByGlobal
    ? '#8f94a6'
    : agentEnabled
    ? accent === 'trainer'
      ? '#ffb18c'
      : '#9ad8cd'
    : '#b7bbca';
  const agentToggleKnobShadow = agentSuppressedByGlobal || !agentEnabled
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
          {showControls && apiStatus === 'ok' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
              <button
                type="button"
                role="switch"
                aria-checked={agentEnabled}
                aria-label={`${agentEnabled ? 'Pause' : 'Resume'} ${label} agent`}
                onClick={(event) => {
                  event.stopPropagation();
                  if (agentEnabled) {
                    onPause?.();
                  } else {
                    onResume?.();
                  }
                }}
                title={`${agentEnabled ? 'Pause' : 'Resume'} ${label} agent`}
                style={{
                  position: 'relative',
                  width: '34px',
                  height: '20px',
                  borderRadius: '999px',
                  border: `1px solid ${agentToggleTrackBorder}`,
                  background: agentToggleTrackBg,
                  cursor: 'pointer',
                  padding: 0,
                  transition: 'background 0.18s ease, border-color 0.18s ease',
                }}
              >
                <span
                  style={{
                    position: 'absolute',
                    top: '2px',
                    left: agentEnabled ? '16px' : '2px',
                    width: '14px',
                    height: '14px',
                    borderRadius: '999px',
                    background: agentToggleKnobBg,
                    boxShadow: agentToggleKnobShadow,
                    transition: 'left 0.18s ease, background 0.18s ease, box-shadow 0.18s ease',
                  }}
                />
              </button>
            </div>
          )}
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', fontSize: '10px' }}>
          <span style={{ color: active ? 'rgba(245,246,251,0.72)' : '#7d8296', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: showSplitObjectives ? '0 1 42%' : '1 1 auto' }}>
            {showSplitObjectives ? topObjective : singleLineObjective}
          </span>
          <span style={{ color: active ? 'rgba(245,246,251,0.82)' : '#a6abc0', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', textAlign: 'right', flex: showSplitObjectives ? '1 1 58%' : '0 0 auto' }}>
            {showSplitObjectives ? currentObjectiveDisplay : ''}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {pathSegments.slice(0, -1).length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '3px', flexShrink: 0 }}>
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

export default App;
