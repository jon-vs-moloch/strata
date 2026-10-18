/**
 * Demo transport: answers the workbench's API calls from recorded sample data
 * so the UI can be explored without a backend or a local model.
 *
 * - GET requests replay responses recorded from the real API against a seeded
 *   sample database (see scripts/build_demo_fixtures.py). Timestamps are shifted
 *   so the sample run looks like it happened just now.
 * - Worker status is a small scripted loop (lanes stepping through their
 *   current tasks) because live worker state is in-memory, not recordable.
 * - Writes are never sent anywhere. Chat messages and pause/resume update a
 *   local in-memory copy so the controls respond; everything else returns an
 *   "ok" and raises a visible "Demo mode" notice.
 *
 * The UI labels all of this as "Demo data". Installed only when DEMO_MODE is on.
 */
import axios from 'axios';
import recorded from './recorded.json';
import { API_BASE } from '../lib/runtime';
import { DEMO_NOTICE_EVENT, DEMO_STREAM_EVENT } from './events';

const API_PREFIXES = [API_BASE, 'http://localhost:8000', 'http://127.0.0.1:8000'];
const TS_RE = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})?$/;

const notify = (text) => {
  window.dispatchEvent(new CustomEvent(DEMO_NOTICE_EVENT, { detail: text }));
};

const clone = (value) => (value == null ? value : JSON.parse(JSON.stringify(value)));

/** Shift every ISO timestamp so the recorded run ends "now". Keeps the original format. */
const shiftTimestamps = (value, deltaMs) => {
  if (Array.isArray(value)) return value.map((item) => shiftTimestamps(item, deltaMs));
  if (value && typeof value === 'object') {
    const out = {};
    for (const [key, item] of Object.entries(value)) out[key] = shiftTimestamps(item, deltaMs);
    return out;
  }
  if (typeof value === 'string') {
    const match = TS_RE.exec(value);
    if (!match) return value;
    const hasZone = Boolean(match[3]);
    const parsed = Date.parse(hasZone ? value : `${value}Z`);
    if (Number.isNaN(parsed)) return value;
    const iso = new Date(parsed + deltaMs).toISOString();
    return hasZone ? iso : iso.slice(0, 19);
  }
  return value;
};

const nowIso = (offsetMs = 0) => new Date(Date.now() - offsetMs).toISOString();

/* ── Scripted live lanes ─────────────────────────────────────────────────── */

const LANE_SCRIPTS = {
  trainer: {
    lane: 'trainer',
    taskId: 't-bootstrap',
    title: 'Bootstrap cycle: propose eval-harness improvements',
    steps: [
      { step: 'evaluation', label: 'Scoring candidate c-04', detail: 'run 2 of 3 on bootstrap_mcq_v1' },
      { step: 'evaluation', label: 'Scoring candidate c-04', detail: 'run 3 of 3 on bootstrap_mcq_v1' },
      { step: 'judging', label: 'Comparing against baseline', detail: '+0.6 pt, below the promotion bar' },
      { step: 'review', label: 'Writing experiment report', detail: 'c-04: insufficient signal' },
      { step: 'proposal', label: 'Asking both tiers for the next candidate', detail: 'agent proposal pending' },
    ],
  },
  local_agent: {
    lane: 'agent',
    taskId: 't-web-retry',
    title: 'Add bounded retry with jittered backoff',
    steps: [
      { step: 'research', label: 'Re-reading the failed attempt', detail: 'verifier: retry loop ignored Retry-After' },
      { step: 'implementation', label: 'Drafting patch', detail: 'web_search: honour Retry-After, cap 3 retries' },
      { step: 'verification', label: 'Verifier checking output', detail: 'cadence: every output' },
      { step: 'implementation', label: 'Revising patch', detail: 'add jitter to backoff' },
      { step: 'verification', label: 'Verifier checking output', detail: 'replaying recorded 429 traces' },
    ],
  },
  remote_agent: {
    lane: 'agent',
    taskId: 't-split-dispatch',
    title: 'Split chat tool dispatch out of chat_runtime',
    steps: [
      { step: 'decomposition', label: 'Planning the split', detail: '3 subtasks proposed' },
      { step: 'research', label: 'Mapping call sites', detail: 'chat_runtime → chat_tool_executor' },
      { step: 'decomposition', label: 'Reviewing plan', detail: 'plan health: healthy' },
    ],
  },
};

const STEP_MS = 6000;

const createLiveState = () => ({
  startedAt: Date.now(),
  globalPaused: false,
  pausedPools: new Set(),
});

const laneSnapshot = (state, poolKey) => {
  const script = LANE_SCRIPTS[poolKey];
  const paused = state.globalPaused || state.pausedPools.has(poolKey);
  const elapsed = Date.now() - state.startedAt;
  const offset = { trainer: 0, local_agent: 2, remote_agent: 1 }[poolKey] || 0;
  const tick = Math.floor(elapsed / STEP_MS) + offset;
  const current = script.steps[tick % script.steps.length];
  const recent = [];
  for (let back = Math.min(5, tick); back >= 0; back -= 1) {
    const event = script.steps[(tick - back) % script.steps.length];
    recent.push({ ...event, at: nowIso(back * STEP_MS + (elapsed % STEP_MS)) });
  }
  return {
    status: paused ? 'PAUSED' : 'RUNNING',
    tier_health: 'ok',
    activity_mode: paused ? 'PAUSED' : 'GENERATING',
    activity_label: paused ? 'Paused' : current.label,
    activity_reason: paused ? 'Paused by the operator.' : current.detail,
    queue_depth: poolKey === 'local_agent' ? 2 : 1,
    current_task_id: script.taskId,
    current_task_started_at: nowIso(6 * 60 * 1000),
    current_task_title: script.title,
    current_task_state: 'working',
    active_attempt_id: `demo-${poolKey}`,
    step: current.step,
    step_label: current.label,
    step_detail: current.detail,
    step_updated_at: nowIso(elapsed % STEP_MS),
    progress_label: `step ${(tick % script.steps.length) + 1} of ${script.steps.length}`,
    recent_steps: recent,
    ticker_items: recent.map((item) => `${item.label}${item.detail ? `: ${item.detail}` : ''}`),
    last_activity_at: nowIso(elapsed % STEP_MS),
    heartbeat_state: paused ? 'paused' : 'healthy',
    heartbeat_age_s: Math.round((elapsed % STEP_MS) / 1000),
  };
};

const workerStatus = (state) => {
  const pools = {
    trainer: laneSnapshot(state, 'trainer'),
    local_agent: laneSnapshot(state, 'local_agent'),
    remote_agent: laneSnapshot(state, 'remote_agent'),
  };
  const pausedPools = [...state.pausedPools];
  return {
    status: {
      worker: state.globalPaused ? 'PAUSED' : 'RUNNING',
      global_paused: state.globalPaused,
      paused_lanes: [],
      paused_work_pools: pausedPools,
      tiers: { trainer: 'ok', agent: 'ok' },
      lanes: { trainer: pools.trainer.status, agent: pools.local_agent.status },
      lane_details: { trainer: pools.trainer, agent: pools.local_agent },
      work_pools: pools,
      demo: true,
    },
  };
};

/* ── Router ──────────────────────────────────────────────────────────────── */

const createRouter = () => {
  const delta = Date.now() - Date.parse(recorded.anchor);
  const responses = shiftTimestamps(recorded.responses, delta);
  const live = createLiveState();
  const messages = {};
  for (const [key, value] of Object.entries(responses)) {
    if (key.startsWith('/messages?session_id=')) {
      messages[decodeURIComponent(key.split('=')[1])] = clone(value);
    }
  }
  const sessions = clone(responses['/sessions']) || [];

  const recordedFor = (pathname, search) => {
    if (responses[`${pathname}${search}`] !== undefined) return clone(responses[`${pathname}${search}`]);
    if (responses[pathname] !== undefined) return clone(responses[pathname]);
    return undefined;
  };

  const touchSession = (sessionId, preview, role) => {
    let session = sessions.find((item) => item.session_id === sessionId);
    if (!session) {
      session = { session_id: sessionId, message_count: 0, session_metadata: { title: 'Demo conversation' } };
      sessions.unshift(session);
    }
    session.message_count = (session.message_count || 0) + 1;
    session.last_message_at = nowIso();
    session.last_message_preview = preview.slice(0, 120);
    session.last_message_role = role;
  };

  const appendMessage = (sessionId, role, content) => {
    const list = messages[sessionId] || (messages[sessionId] = []);
    const message = {
      id: `demo-${Date.now()}-${list.length}`,
      message_id: `demo-${Date.now()}-${list.length}`,
      session_id: sessionId,
      role,
      content,
      created_at: nowIso().slice(0, 19),
      message_metadata: { demo: true, source_actor: role === 'user' ? 'user' : sessionId.split(':')[0] },
    };
    list.push(message);
    touchSession(sessionId, content, role);
    return message;
  };

  const handleChat = (body) => {
    const sessionId = body?.session_id || 'trainer:default';
    const lane = String(sessionId).split(':')[0] === 'agent' ? 'agent' : 'trainer';
    appendMessage(sessionId, 'user', String(body?.content || ''));
    window.setTimeout(() => {
      appendMessage(
        sessionId,
        'assistant',
        `**Demo mode:** no model is attached, so this message wasn't sent anywhere.\n\n` +
          `In a real run it goes to the **${lane}** lane. The orchestrator either answers directly or opens a task, ` +
          'which is decomposed, attempted, verified, and shown in the task rail with its attempt history.',
      );
      window.dispatchEvent(new CustomEvent(DEMO_STREAM_EVENT, { detail: { type: 'message' } }));
    }, 900);
    return { status: 'ok', demo: true, session_id: sessionId };
  };

  const handleWorkerControl = (action, params) => {
    const pool = params?.work_pool;
    if (action === 'pause') {
      if (pool) live.pausedPools.add(pool);
      else live.globalPaused = true;
    } else {
      if (pool) live.pausedPools.delete(pool);
      else live.globalPaused = false;
    }
    notify(`Demo mode: ${action} applied to the simulated ${pool ? pool.replace('_', ' ') : 'worker'} only.`);
    return { status: 'ok', demo: true };
  };

  return (method, rawUrl, body, params) => {
    const url = new URL(rawUrl);
    if (params) {
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
      }
    }
    const { pathname } = url;
    const search = url.search;
    const verb = method.toUpperCase();

    if (verb === 'GET') {
      if (pathname === '/admin/worker/status') return [200, workerStatus(live)];
      if (pathname === '/admin/health') return [200, { status: 'ok', database: 'connected', worker: 'alive', demo: true }];
      if (pathname === '/sessions') return [200, clone(sessions)];
      if (pathname === '/messages') return [200, clone(messages[url.searchParams.get('session_id')] || [])];
      if (pathname === '/tasks') {
        // Working tasks in the sample keep "just updated" so the live lanes don't read as stale.
        const tasks = recordedFor(pathname, '') || [];
        for (const task of tasks) {
          if (task.status === 'working') task.updated_at = nowIso(20000).slice(0, 19);
        }
        return [200, tasks];
      }
      const hit = recordedFor(pathname, search);
      if (hit !== undefined) return [200, hit];
      if (pathname.startsWith('/admin/knowledge/pages/')) return [404, { detail: 'Not in the demo sample.' }];
      return [200, { status: 'ok', demo: true }];
    }

    if (verb === 'POST' && pathname === '/chat') return [200, handleChat(body)];
    if (verb === 'POST' && pathname === '/admin/worker/pause') return [200, handleWorkerControl('pause', Object.fromEntries(url.searchParams))];
    if (verb === 'POST' && pathname === '/admin/worker/resume') return [200, handleWorkerControl('resume', Object.fromEntries(url.searchParams))];
    if (verb === 'POST' && pathname.startsWith('/sessions/') && pathname.endsWith('/read')) return [200, { status: 'ok' }];

    notify(`Demo mode: ${verb} ${pathname} was not sent. Run Strata locally to use this control.`);
    return [200, { status: 'ok', demo: true, message: 'Demo mode: nothing was changed.' }];
  };
};

/* ── Wiring: axios, fetch, EventSource ──────────────────────────────────── */

const isApiUrl = (url) => API_PREFIXES.some((prefix) => String(url).startsWith(prefix));

const parseBody = (data) => {
  if (data == null) return null;
  if (typeof data === 'string') {
    try {
      return JSON.parse(data);
    } catch {
      return data;
    }
  }
  return data;
};

class DemoEventSource {
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.onmessage = null;
    this.onerror = null;
    this.onopen = null;
    this.listeners = {};
    this.emit = (payload) => {
      const event = { data: JSON.stringify(payload) };
      if (typeof this.onmessage === 'function') this.onmessage(event);
      (this.listeners.message || []).forEach((fn) => fn(event));
    };
    this.timer = window.setInterval(() => this.emit({ type: 'worker_status', demo: true }), STEP_MS);
    this.relay = (event) => this.emit({ ...(event.detail || {}), demo: true });
    window.addEventListener(DEMO_STREAM_EVENT, this.relay);
  }

  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }

  removeEventListener(type, fn) {
    this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== fn);
  }

  close() {
    this.readyState = 2;
    window.clearInterval(this.timer);
    window.removeEventListener(DEMO_STREAM_EVENT, this.relay);
  }
}

export const installDemoTransport = () => {
  const route = createRouter();

  axios.defaults.adapter = async (config) => {
    const fullUrl = axios.getUri(config);
    if (!isApiUrl(fullUrl)) {
      notify('Demo mode: requests outside the sample API are not sent.');
      const error = new Error('Demo mode: external request blocked');
      error.config = config;
      throw error;
    }
    const [status, data] = route(config.method || 'get', fullUrl, parseBody(config.data));
    await new Promise((resolve) => window.setTimeout(resolve, 60));
    const response = { data, status, statusText: status === 200 ? 'OK' : 'Not Found', headers: {}, config, request: {} };
    if (status >= 400) {
      const error = new Error(`Request failed with status code ${status}`);
      error.config = config;
      error.response = response;
      throw error;
    }
    return response;
  };

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input?.url;
    if (!isApiUrl(url)) return realFetch(input, init);
    const [status, data] = route(init.method || 'GET', url, parseBody(init.body));
    return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
  };

  const RealEventSource = window.EventSource;
  window.EventSource = function DemoAwareEventSource(url, options) {
    if (isApiUrl(url)) return new DemoEventSource(url);
    return new RealEventSource(url, options);
  };
};
