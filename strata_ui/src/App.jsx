import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { motion, AnimatePresence } from 'framer-motion';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Plus, RefreshCw, Zap,
  MessageSquare, Send, History, Cpu,
  Terminal, AlertCircle, X, Settings,
  Activity, Trash2, Database, LayoutDashboard,
  Pause, Play, Square, ChevronDown, ChevronRight,
  BookOpen, Search
} from 'lucide-react';
import TaskCard from './components/TaskCard';

const MotionDiv = motion.div;
const MotionSpan = motion.span;

const API_KEY_LINKS = {
  cerebras: 'https://cloud.cerebras.ai/',
  google: 'https://aistudio.google.com/apikey',
  openrouter: 'https://openrouter.ai/settings/keys',
};

const PROVIDER_SETUP_LINKS = [
  { label: 'Cerebras Key', href: 'https://cloud.cerebras.ai/' },
  { label: 'Google AI Studio Key', href: 'https://aistudio.google.com/apikey' },
  { label: 'OpenRouter Keys', href: 'https://openrouter.ai/settings/keys' },
];

const CHAT_LANES = ['strong', 'weak'];

const defaultSessionIdForLane = (lane) => `${lane}:default`;

const laneForSessionId = (sessionId) => {
  if (typeof sessionId !== 'string') return 'strong';
  if (sessionId.startsWith('weak:')) return 'weak';
  if (sessionId.startsWith('strong:')) return 'strong';
  return 'strong';
};

const displaySessionId = (sessionId) => {
  if (typeof sessionId !== 'string') return 'default';
  if (sessionId.startsWith('weak:')) return sessionId.slice('weak:'.length);
  if (sessionId.startsWith('strong:')) return sessionId.slice('strong:'.length);
  return sessionId;
};

const formatRelativeTime = (dateString) => {
  if (!dateString) return 'never';
  const deltaMs = Date.now() - new Date(dateString).getTime();
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
  return new Date(dateString).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
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


// ─── Settings Modal ────────────────────────────────────────────────────────────
const SettingsModal = ({ onClose, onResetDatabase, apiUrl }) => {
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
  const [savingSettings, setSavingSettings] = useState(false);

  // Model Registry Settings
  const [registryConfig, setRegistryConfig] = useState({ strong: [], weak: [] });
  const [registryPresets, setRegistryPresets] = useState({ strong: {}, weak: {} });
  const [savingRegistry, setSavingRegistry] = useState(false);

  const loadSettings = useCallback(async () => {
    try {
      const res = await axios.get(`${apiUrl}/admin/settings`);
      if (res.data.status === 'ok') {
        setMaxSyncIters(res.data.settings.max_sync_tool_iterations || 3);
        setAutomaticTaskGeneration(Boolean(res.data.settings.automatic_task_generation));
        setTestingMode(Boolean(res.data.settings.testing_mode));
        setReplayPendingOnStartup(Boolean(res.data.settings.replay_pending_tasks_on_startup));
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
        setRegistryPresets(res.data.presets || { strong: {}, weak: {} });
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
    if (pool === 'strong') next[pool][index].transport = 'cloud';
    if (pool === 'weak') next[pool][index].transport = 'local';
    // Ensure default provider if missing
    if (!next[pool][index].provider) next[pool][index].provider = pool === 'strong' ? 'openrouter' : 'lmstudio';
    
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

  return (
    <MotionDiv
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1000, backdropFilter: 'blur(4px)'
      }}
    >
      <MotionDiv
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 20 }}
        onClick={e => e.stopPropagation()}
        style={{
          background: '#141418', border: '1px solid rgba(255,255,255,0.1)',
          borderRadius: '20px', padding: '32px', width: '560px',
          display: 'flex', flexDirection: 'column', gap: '24px',
          maxHeight: '90vh', overflowY: 'auto'
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{ width: '36px', height: '36px', borderRadius: '10px', background: 'rgba(130,87,229,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Settings size={18} color="#8257e5" />
            </div>
            <div>
              <h2 style={{ fontSize: '16px', fontWeight: 700, color: '#edeeef' }}>Settings</h2>
              <p style={{ fontSize: '12px', color: '#888' }}>Advanced configuration</p>
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#888', cursor: 'pointer', padding: '4px' }}>
            <X size={20} />
          </button>
        </div>

        {/* ── Connection ───────────────────────────────────────────────────── */}
        <div>
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
        </div>

        {/* ── Model Registry ───────────────────────────────────────────────── */}
        <div>
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
            <div>
              <div style={{ ...sectionLabel, marginBottom: '6px' }}>STRONG PRESETS</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {Object.keys(registryPresets.strong || {}).map((presetKey) => (
                  <button
                    key={presetKey}
                    onClick={() => applyPreset('strong', presetKey)}
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
            <div>
              <div style={{ ...sectionLabel, marginBottom: '6px' }}>WEAK PRESETS</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {Object.keys(registryPresets.weak || {}).map((presetKey) => (
                  <button
                    key={presetKey}
                    onClick={() => applyPreset('weak', presetKey)}
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
          </div>
          
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {/* Strong Pool */}
            <div style={inputGroupStyle}>
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#8257e5', marginBottom: '4px', letterSpacing: '0.05em' }}>STRONG POOL (CLOUD)</div>
              {registryConfig.strong?.[0]?.provider && API_KEY_LINKS[registryConfig.strong[0].provider] && (
                <a
                  href={API_KEY_LINKS[registryConfig.strong[0].provider]}
                  target="_blank"
                  rel="noreferrer"
                  style={{ fontSize: '11px', color: '#bca9ff', textDecoration: 'none' }}
                >
                  Open {registryConfig.strong[0].provider} API key page
                </a>
              )}
              <input 
                style={infoValue} placeholder="Model (e.g. anthropic/claude-3.5-sonnet)"
                value={registryConfig.strong?.[0]?.model || ''}
                onChange={e => handleUpdateRegistry('strong', 'model', e.target.value)}
              />
              <input 
                style={infoValue} placeholder="Endpoint URL (e.g. https://openrouter.ai/api/v1/chat/completions)"
                value={registryConfig.strong?.[0]?.endpoint_url || ''}
                onChange={e => handleUpdateRegistry('strong', 'endpoint_url', e.target.value)}
              />
              <input 
                style={infoValue} placeholder="API Key Env (e.g. OPENROUTER_API_KEY)"
                value={registryConfig.strong?.[0]?.api_key_env || ''}
                onChange={e => handleUpdateRegistry('strong', 'api_key_env', e.target.value)}
              />
              <input
                type="number"
                style={infoValue} placeholder="Requests / minute (optional)"
                value={registryConfig.strong?.[0]?.requests_per_minute || ''}
                onChange={e => handleUpdateRegistry('strong', 'requests_per_minute', e.target.value ? parseInt(e.target.value, 10) : null)}
              />
              <input
                type="number"
                style={infoValue} placeholder="Max concurrency (optional)"
                value={registryConfig.strong?.[0]?.max_concurrency || ''}
                onChange={e => handleUpdateRegistry('strong', 'max_concurrency', e.target.value ? parseInt(e.target.value, 10) : null)}
              />
              <input
                type="number"
                style={infoValue} placeholder="Min interval ms (optional)"
                value={registryConfig.strong?.[0]?.min_interval_ms || ''}
                onChange={e => handleUpdateRegistry('strong', 'min_interval_ms', e.target.value ? parseInt(e.target.value, 10) : null)}
              />
            </div>

            {/* Weak Pool */}
            <div style={inputGroupStyle}>
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#00d9ff', marginBottom: '4px', letterSpacing: '0.05em' }}>WEAK POOL (LOCAL)</div>
              <input 
                style={infoValue} placeholder="Model (e.g. qwen3.5-9b-distilled)"
                value={registryConfig.weak?.[0]?.model || ''}
                onChange={e => handleUpdateRegistry('weak', 'model', e.target.value)}
              />
              <input 
                style={infoValue} placeholder="Endpoint URL (e.g. http://127.0.0.1:1234/v1/chat/completions)"
                value={registryConfig.weak?.[0]?.endpoint_url || ''}
                onChange={e => handleUpdateRegistry('weak', 'endpoint_url', e.target.value)}
              />
              <input
                type="number"
                style={infoValue} placeholder="Requests / minute (optional)"
                value={registryConfig.weak?.[0]?.requests_per_minute || ''}
                onChange={e => handleUpdateRegistry('weak', 'requests_per_minute', e.target.value ? parseInt(e.target.value, 10) : null)}
              />
              <input
                type="number"
                style={infoValue} placeholder="Max concurrency (optional)"
                value={registryConfig.weak?.[0]?.max_concurrency || ''}
                onChange={e => handleUpdateRegistry('weak', 'max_concurrency', e.target.value ? parseInt(e.target.value, 10) : null)}
              />
              <input
                type="number"
                style={infoValue} placeholder="Min interval ms (optional)"
                value={registryConfig.weak?.[0]?.min_interval_ms || ''}
                onChange={e => handleUpdateRegistry('weak', 'min_interval_ms', e.target.value ? parseInt(e.target.value, 10) : null)}
              />
            </div>
          </div>
        </div>

        {/* ── Orchestrator Settings ────────────────────────────────────────── */}
        <div>
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
          </div>
        </div>

        {/* ── Danger Zone ──────────────────────────────────────────────────── */}
        <div style={{ borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Database size={14} color="#ff4d4d" />
            <span style={{ fontSize: '11px', fontWeight: 700, color: '#ff4d4d', letterSpacing: '0.08em' }}>DANGER ZONE</span>
          </div>
          <div style={{
            background: 'rgba(255,77,77,0.04)', border: '1px solid rgba(255,77,77,0.15)',
            borderRadius: '12px', padding: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center'
          }}>
            <div>
              <div style={{ fontSize: '14px', fontWeight: 600, color: '#edeeef', marginBottom: '4px' }}>Reset Database</div>
              <div style={{ fontSize: '12px', color: '#888' }}>Wipes strata.db — all tasks and sessions are lost.</div>
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
              {resetting ? 'Resetting…' : resetDone ? '✓ Done' : resetConfirm ? 'Confirm Reset' : 'Reset DB'}
            </button>
          </div>
        </div>
      </MotionDiv>
    </MotionDiv>
  );
};

// ─── Session History Pane ──────────────────────────────────────────────────────
const HistoryPane = ({ sessionList, sessionId, setSessionId, deleteSession }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', padding: '8px 0' }}>
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
        onClick={() => setSessionId(session.session_id)}
        onDelete={() => deleteSession(session.session_id)}
      />
    ))}
  </div>
);

const SessionRow = ({ session, active, onClick, onDelete }) => {
  const [hovered, setHovered] = useState(false);
  const s = displaySessionId(session.session_id);
  const label = s === 'default' ? 'Genesis Session' : (() => {
    const ts = parseInt(s.replace('session-', ''), 10);
    if (isNaN(ts)) return s;
    return new Date(ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  })();

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
        color: active ? '#8257e5' : '#888',
        fontSize: '13px', transition: 'all 0.15s'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', overflow: 'hidden' }}>
        <MessageSquare size={14} style={{ opacity: active ? 1 : 0.5, flexShrink: 0 }} />
        <div style={{ overflow: 'hidden' }}>
          <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</div>
          <div style={{ fontSize: '10px', color: '#666', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {session.last_message_preview || 'No messages yet'}
          </div>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
        <span title={formatAbsoluteTime(session.last_message_at)} style={{ fontSize: '10px', color: '#666' }}>
          {formatRelativeTime(session.last_message_at)}
        </span>
        {(hovered || active) && (
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
  const [isSending, setIsSending]     = useState(false);
  const [sendError, setSendError]     = useState('');
  const [chatLane, setChatLane]       = useState('strong');
  const [laneSessionIds, setLaneSessionIds] = useState({
    strong: defaultSessionIdForLane('strong'),
    weak: defaultSessionIdForLane('weak'),
  });
  const [sessionList, setSessionList] = useState([]);
  const [activeNav, setActiveNav]     = useState('chat');   // 'chat' | 'history' | 'dashboard'
  const [showSettings, setShowSettings] = useState(false);
  const [apiStatus, setApiStatus]     = useState('connecting'); // 'ok' | 'error' | 'connecting'
  const messagesEndRef = useRef(null);
  const isSendingRef = useRef(false);
  const fetchGenRef = useRef(0);       // generation counter for stale-poll rejection
  const [workerStatus, setWorkerStatus] = useState('RUNNING'); // RUNNING, PAUSED, STOPPED
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
  const [operatorNotice, setOperatorNotice] = useState('');
  const [showFinishedTasks, setShowFinishedTasks] = useState(false);
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

  const [tiers, setTiers] = useState({ Strong: 'unknown', Weak: 'unknown' });
  const [showCloudModal, setShowCloudModal] = useState(false);
  const sessionId = laneSessionIds[chatLane] || defaultSessionIdForLane(chatLane);

  const fetchWorkerStatus = useCallback(async () => {
    try {
      const res = await axios.get(`${API}/admin/worker/status`);
      setWorkerStatus(res.data.status.worker);
      setTiers(res.data.status.tiers);
      if (res.data.status.tiers.Strong === 'error' && !localStorage.getItem('skipCloudWarning')) {
        setShowCloudModal(true);
      }
    } catch (e) { console.error('Failed to fetch worker status', e); }
  }, [API]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void fetchWorkerStatus();
    }, 0);
    const interval = setInterval(fetchWorkerStatus, 5000);
    return () => {
      clearTimeout(timer);
      clearInterval(interval);
    };
  }, [fetchWorkerStatus]);

  const handleReboot = async () => {
    setRebooting(true);
    try {
      await axios.post(`${API}/admin/reboot`);
      setTimeout(() => {
        setRebooting(false);
        fetchData(true);
      }, 3000);
    } catch (e) {
      console.error('Reboot failed', e);
      setRebooting(false);
    }
  };

  const handlePause = async () => {
    try {
      await axios.post(`${API}/admin/worker/pause`);
      setWorkerStatus('PAUSED');
    } catch (e) { console.error(e); }
  };

  const handleResume = async () => {
    try {
      await axios.post(`${API}/admin/worker/resume`);
      setWorkerStatus('RUNNING');
    } catch (e) { console.error(e); }
  };

  const handleStop = async () => {
    try {
      const res = await axios.post(`${API}/admin/worker/stop`);
      if (res.data.aborted) {
        // Option to display notification "Task Aborted"
      }
      fetchWorkerStatus();
    } catch (e) { console.error(e); }
  };

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const fetchData = useCallback(async (force = false) => {
    // Skip polling while a message is in-flight (unless forced)
    if (!force && isSendingRef.current) return;

    // Increment generation — any older in-flight fetch will see a mismatch and bail
    const gen = ++fetchGenRef.current;

    try {
      const [tasksRes, msgsRes, sessionsRes, telemetryRes, providerTelemetryRes, dashboardRes, loadedContextRes, routingRes, specsRes, specProposalsRes, knowledgePagesRes, retentionRes, variantRatingsRes, predictionTrustRes] = await Promise.all([
        axios.get(`${API}/tasks`),
        axios.get(`${API}/messages?session_id=${sessionId}`),
        axios.get(`${API}/sessions`),
        axios.get(`${API}/admin/telemetry?limit=8`),
        axios.get(`${API}/admin/providers/telemetry`),
        axios.get(`${API}/admin/dashboard?limit=6`),
        axios.get(`${API}/admin/context/loaded`),
        axios.get(`${API}/admin/routing`),
        axios.get(`${API}/admin/specs`),
        axios.get(`${API}/admin/spec_proposals?limit=6`),
        axios.get(`${API}/admin/knowledge/pages?limit=6&audience=operator`),
        axios.get(`${API}/admin/storage/retention`),
        activeNav === 'dashboard' ? axios.get(`${API}/admin/variants/ratings`) : Promise.resolve({ data: null }),
        activeNav === 'dashboard' ? axios.get(`${API}/admin/predictions/trust`) : Promise.resolve({ data: null })
      ]);

      // If a newer fetch was launched while we were awaiting, discard this result
      if (gen !== fetchGenRef.current) return;

      setTasks(tasksRes.data);
      setMessages(msgsRes.data);
      const sessions = (Array.isArray(sessionsRes.data) ? sessionsRes.data.slice() : [])
        .filter((session) => laneForSessionId(session.session_id) === chatLane);
      if (!sessions.some((s) => s.session_id === sessionId)) {
        sessions.push({
          session_id: sessionId,
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
      setRoutingSummary(routingRes.data.routing || null);
      setSpecsSnapshot(specsRes.data.specs || null);
      setSpecProposalSnapshot(specProposalsRes.data.proposals || []);
      setKnowledgePagesSnapshot(knowledgePagesRes.data.pages || []);
      setRetentionSnapshot(retentionRes.data || null);
      setVariantRatingsSnapshot(variantRatingsRes?.data?.ratings || null);
      setPredictionTrustSnapshot(predictionTrustRes?.data?.trust || null);
      setApiStatus('ok');
    } catch (err) {
      if (gen !== fetchGenRef.current) return;
      console.error('Fetch failed', err);
      setApiStatus('error');
    }
  }, [chatLane, sessionId]);

  useEffect(() => {
    fetchData(true);
    
    // Replace polling with Server-Sent Events (SSE)
    const es = new EventSource(`${API}/events`);
    
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        console.log('SSE Event:', data);
        if (data.type === 'task_update' || data.type === 'message') {
          fetchData(true);
        }
      } catch (err) {
        console.error('SSE Parse Error:', err);
      }
    };

    es.onerror = (err) => {
      console.error('SSE Error:', err);
      // Fallback: stay on polling if SSE fails
    };

    return () => es.close();
  }, [fetchData]);

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

  const handleSendMessage = async () => {
    if (!inputText.trim() || isSending) return;
    const text = inputText;
    const tempId = `temp-${Date.now()}`;
    setInputText('');
    setSendError('');
    setIsSending(true);
    isSendingRef.current = true;
    // Optimistic update: show the user's message immediately
    setMessages(prev => [...prev, { id: tempId, role: 'user', content: text, pending: true }]);
    try {
      await axios.post(`${API}/chat`, {
        role: 'user',
        content: text,
        session_id: sessionId,
        preferred_tier: chatLane,
      });
      await fetchData(true);
    } catch (err) {
      console.error('Failed to send message.', err);
      const detail = err?.response?.data?.detail;
      const message = typeof detail === 'string' ? detail : 'Message failed to send. Please retry.';
      setSendError(message);
      setMessages(prev => prev.map(msg => (
        msg.id === tempId ? { ...msg, pending: false, failed: true } : msg
      )));
    }
    setIsSending(false);
    isSendingRef.current = false;
  };

  const startNewChat = () => {
    const newId = `${chatLane}:session-${Date.now()}`;
    setLaneSessionIds(prev => ({ ...prev, [chatLane]: newId }));
    setMessages([]);
    setInputText('');
    setSendError('');
  };

  const deleteSession = async (idToDelete) => {
    try {
      await axios.delete(`${API}/sessions/${idToDelete}`);
      setSessionList(prev => prev.filter((session) => session.session_id !== idToDelete));
      if (sessionId === idToDelete) {
        setLaneSessionIds(prev => ({ ...prev, [chatLane]: defaultSessionIdForLane(chatLane) }));
        setMessages([]);
      }
    } catch (err) {
      console.error('Failed to delete session.', err);
    }
  };

  const handleResetDatabase = async () => {
    await axios.post(`${API}/admin/reset`);
    setSessionList([]);
    setLaneSessionIds({
      strong: defaultSessionIdForLane('strong'),
      weak: defaultSessionIdForLane('weak'),
    });
    setMessages([]);
    setTasks([]);
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
    axios.post(`${API}/admin/experiments/bootstrap_cycle`, { queue: true, proposer_tiers: ['strong'], run_count: 1, auto_promote: true })
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
  const completedCount  = tasks.filter(t => t.status === 'complete').length;
  const runningCount    = tasks.filter(t => t.status === 'working').length;
  const totalCount      = tasks.length;
  const blockedCount    = tasks.filter(t => t.status === 'blocked').length;
  const passRate        = totalCount > 0 ? Math.round((completedCount / totalCount) * 100) : '—';
  const specPendingCount = dashboard?.spec_governance?.pending_count ?? 0;
  const specClarificationCount = dashboard?.spec_governance?.clarification_count ?? 0;

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

  const visibleSessionId = displaySessionId(sessionId);
  const sessionLabel = visibleSessionId === 'default'
    ? 'Genesis Session'
    : (() => {
        const ts = parseInt(visibleSessionId.replace('session-', ''), 10);
        return isNaN(ts) ? visibleSessionId : new Date(ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
      })();

  // ── Icon Nav items ───────────────────────────────────────────────────────────
  const navItems = [
    { id: 'chat',      Icon: MessageSquare,  label: 'Chat'      },
    { id: 'history',   Icon: History,        label: 'History'   },
    { id: 'knowledge', Icon: BookOpen,       label: 'Knowledge' },
    { id: 'dashboard', Icon: LayoutDashboard,label: 'Dashboard' },
  ];

  const finishedTaskTree = taskTree.filter(task => ['complete', 'abandoned', 'cancelled'].includes(task.status));
  const activeTaskTree = taskTree.filter(task => !['complete', 'abandoned', 'cancelled'].includes(task.status));
  const visibleTaskTree = activeNav === 'dashboard' ? taskTree : activeTaskTree;

  return (
    <div className="app-container" style={{ display: 'flex', height: '100vh', width: '100vw', background: '#0a0a0c', fontFamily: "'Outfit', sans-serif" }}>
      {showCloudModal && (
        <div className="modal-overlay">
          <div className="modal-content glass">
            <h2>☁️ Cloud Inference Offline</h2>
            <p>The <b>Strong</b> model tier (OpenRouter) is currently unreachable or missing an API key.</p>
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
                  setActiveNav('settings');
                }}
              >
                Configure Cloud
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── COLUMN 1: ICON NAV ─────────────────────────────────────────────── */}
      <div style={{ width: '72px', borderRight: '1px solid rgba(255,255,255,0.05)', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '20px 0', gap: '8px' }}>
        {/* Logo */}
        <MotionDiv
          animate={{ rotate: 360 }}
          transition={{ duration: 20, repeat: Infinity, ease: 'linear' }}
          style={{ width: '38px', height: '38px', borderRadius: '11px', background: 'linear-gradient(135deg, #8257e5, #5e33ba)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: '16px', boxShadow: '0 0 18px rgba(130,87,229,0.3)' }}
        >
          <div style={{ width: '12px', height: '12px', background: 'white', borderRadius: '2px', transform: 'rotate(45deg)' }} />
        </MotionDiv>

        {navItems.map(({ id, Icon, label }) => (
          <NavIcon
            key={id}
            icon={Icon}
            label={label}
            active={activeNav === id}
            onClick={() => setActiveNav(id)}
          />
        ))}

        {/* Spacer */}
        <div style={{ flex: 1 }} />

        {/* Force refresh */}
        <NavIcon
          icon={RefreshCw}
          label="Refresh"
          active={false}
          onClick={fetchData}
        />

        {/* Settings */}
        <NavIcon
          icon={Cpu}
          label="Settings"
          active={showSettings}
          onClick={() => setShowSettings(true)}
        />

        {/* API status dot */}
        <div
          title={apiStatus === 'ok' ? 'API connected' : apiStatus === 'error' ? 'API unreachable' : 'Connecting…'}
          style={{ width: '8px', height: '8px', borderRadius: '50%', marginTop: '8px', marginBottom: '4px', background: apiStatus === 'ok' ? '#00f294' : apiStatus === 'error' ? '#ff4d4d' : '#ffb84d', boxShadow: apiStatus === 'ok' ? '0 0 6px #00f29488' : 'none' }}
        />
      </div>

      {/* ── COLUMN 2: SESSION / HISTORY PANEL ──────────────────────────────── */}
      <div style={{ width: '240px', borderRight: '1px solid rgba(255,255,255,0.05)', display: 'flex', flexDirection: 'column', background: '#0c0c0e' }}>
        <header style={{ padding: '20px 16px 16px', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', width: '100%' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h2 style={{ fontSize: '11px', fontWeight: 700, color: '#555', letterSpacing: '0.12em' }}>
                {activeNav === 'history' ? 'ALL SESSIONS' : 'SESSIONS'}
              </h2>
              <button
                onClick={startNewChat}
                title="New Chat"
                style={{ background: 'rgba(130,87,229,0.15)', border: '1px solid rgba(130,87,229,0.3)', borderRadius: '6px', color: '#8257e5', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '4px', padding: '5px 10px', fontSize: '12px', fontWeight: 600 }}
              >
                <Plus size={13} /> New
              </button>
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              {CHAT_LANES.map((lane) => (
                <LaneToggle
                  key={lane}
                  lane={lane}
                  active={chatLane === lane}
                  onClick={() => {
                    setChatLane(lane);
                    setMessages([]);
                    setSendError('');
                  }}
                />
              ))}
            </div>
          </div>
        </header>

        <div style={{ flex: 1, overflowY: 'auto', padding: '8px' }}>
          <HistoryPane
            sessionList={sessionList}
            sessionId={sessionId}
            setSessionId={(nextSessionId) => setLaneSessionIds(prev => ({ ...prev, [chatLane]: nextSessionId }))}
            deleteSession={deleteSession}
          />
        </div>
      </div>

      {/* ── COLUMN 3: CHAT / DASHBOARD ────────────────────────────────────── */}
      <section style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#0a0a0c', borderRight: '1px solid rgba(255,255,255,0.05)', minWidth: 0 }}>
        <header style={{ padding: '20px 28px', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
          <div>
            <h1 style={{ fontSize: '18px', fontWeight: 700, color: 'white' }}>
              {activeNav === 'dashboard'
                ? 'Operator Dashboard'
                : activeNav === 'knowledge'
                ? 'Knowledge Base'
                : `Orchestrator Chat · ${chatLane.toUpperCase()}`}
            </h1>
            <p style={{ fontSize: '12px', color: '#555', marginTop: '2px' }}>
              {activeNav === 'knowledge' ? 'Navigable system wiki' : sessionLabel}
            </p>
            {routingSummary && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginTop: '10px' }}>
                <RoutePill
                  label="CHAT"
                  value={routingSummary?.chat?.error ? routingSummary.chat.error : `${routingSummary?.chat?.mode || '—'} · ${routingSummary?.chat?.provider || '—'} · ${routingSummary?.chat?.selected_model || routingSummary?.chat?.model || '—'}`}
                  tone={routingSummary?.chat?.status === 'error' ? 'danger' : 'neutral'}
                />
                <RoutePill
                  label="WEAK"
                  value={routingSummary?.weak?.error ? routingSummary.weak.error : `${routingSummary?.weak?.transport || '—'} · ${routingSummary?.weak?.provider || '—'} · ${routingSummary?.weak?.selected_model || routingSummary?.weak?.model || '—'}`}
                  tone={routingSummary?.weak?.status === 'ok' ? 'success' : 'warning'}
                />
                <RoutePill
                  label="LOOP"
                  value={routingSummary?.supervision?.active_jobs?.length ? `${routingSummary.supervision.active_jobs.length} supervision job${routingSummary.supervision.active_jobs.length > 1 ? 's' : ''}` : 'idle'}
                  tone={routingSummary?.supervision?.active_jobs?.length ? 'success' : 'neutral'}
                />
              </div>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {/* Control Buttons */}
            {apiStatus === 'ok' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginRight: '4px', paddingRight: '12px', borderRight: '1px solid rgba(255,255,255,0.05)' }}>
                {workerStatus === 'PAUSED' ? (
                  <button 
                    onClick={handleResume} title="Resume Processing"
                    style={{ background: 'rgba(0,242,148,0.1)', border: '1px solid rgba(0,242,148,0.2)', color: '#00f294', borderRadius: '4px', padding: '4px', cursor: 'pointer', display: 'flex' }}
                  >
                    <Play size={14} fill="#00f294" />
                  </button>
                ) : (
                  <button 
                    onClick={handlePause} title="Pause Gracefully"
                    style={{ background: 'rgba(255,184,77,0.1)', border: '1px solid rgba(255,184,77,0.2)', color: '#ffb84d', borderRadius: '4px', padding: '4px', cursor: 'pointer', display: 'flex' }}
                  >
                    <Pause size={14} fill="#ffb84d" />
                  </button>
                )}
                <button 
                  onClick={handleStop} title="Emergency Stop Current Task"
                  style={{ background: 'rgba(255,77,77,0.1)', border: '1px solid rgba(255,77,77,0.2)', color: '#ff4d4d', borderRadius: '4px', padding: '4px', cursor: 'pointer', display: 'flex' }}
                >
                  <Square size={14} fill="#ff4d4d" />
                </button>
              </div>
            )}
            
            {apiStatus === 'ok' && (
              <span style={{ fontSize: '11px', color: workerStatus === 'PAUSED' ? '#ffb84d' : '#00f294', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '4px' }}>
                <Activity size={12} /> {workerStatus}
              </span>
            )}
            {apiStatus === 'error' && (
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <span style={{ fontSize: '11px', color: '#ff4d4d', fontWeight: 600 }}>API DOWN</span>
                <button 
                  onClick={() => fetchData(true)}
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

        {activeNav !== 'dashboard' && (specPendingCount > 0 || specClarificationCount > 0) && (
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
            specsSnapshot={specsSnapshot}
            specProposalSnapshot={specProposalSnapshot}
            knowledgePagesSnapshot={knowledgePagesSnapshot}
            retentionSnapshot={retentionSnapshot}
            variantRatingsSnapshot={variantRatingsSnapshot}
            predictionTrustSnapshot={predictionTrustSnapshot}
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
          />
        ) : (
        <div style={{ flex: 1, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <AnimatePresence initial={false}>
            {messages.map((msg, i) => {
              const display = formatMessageForDisplay(msg.content);
              return (
              <MotionDiv
                key={msg.id || i}
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.2 }}
                style={{
                  alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
                  background: msg.is_intervention
                    ? 'rgba(255, 77, 77, 0.05)'
                    : msg.role === 'user'
                    ? 'linear-gradient(135deg, #8257e5, #6440c4)'
                    : '#141418',
                  padding: '14px 18px',
                  borderRadius: msg.role === 'user' ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
                  border: msg.is_intervention ? '1px solid rgba(255,77,77,0.3)' : '1px solid rgba(255,255,255,0.05)',
                  maxWidth: '78%',
                  color: msg.role === 'user' ? 'white' : '#edeeef',
                  boxShadow: msg.role === 'user' ? '0 4px 16px rgba(130,87,229,0.2)' : 'none'
                }}
              >
                {msg.is_intervention && (
                  <div style={{ color: '#ff4d4d', fontSize: '10px', fontWeight: 800, marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '4px', letterSpacing: '0.08em' }}>
                    <AlertCircle size={12} /> ACTION REQUIRED
                  </div>
                )}
                <div className="markdown-body" style={{ fontSize: '14px', lineHeight: '1.65' }}>
                  {display.lead && (
                    <div style={{ fontSize: '13px', color: msg.role === 'user' ? 'rgba(255,255,255,0.92)' : '#e8e9f2', fontWeight: 600, marginBottom: '10px' }}>
                      {display.lead}
                    </div>
                  )}
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{display.body}</ReactMarkdown>
                </div>
                {(msg.pending || msg.failed) && (
                  <div style={{ marginTop: '8px', fontSize: '10px', fontWeight: 700, letterSpacing: '0.06em', color: msg.failed ? 'rgba(255,230,230,0.92)' : 'rgba(255,255,255,0.78)' }}>
                    {msg.failed ? 'SEND FAILED' : 'SENDING'}
                  </div>
                )}
                <div title={formatRelativeTime(msg.created_at)} style={{ marginTop: '8px', fontSize: '10px', color: msg.role === 'user' ? 'rgba(255,255,255,0.7)' : '#666' }}>
                  {formatAbsoluteTime(msg.created_at)}
                </div>
              </MotionDiv>
            )})}

            {messages.length === 0 && !isSending && (
              <MotionDiv
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                style={{ textAlign: 'center', color: '#333', marginTop: 'auto', marginBottom: 'auto', padding: '48px 32px' }}
              >
                <Zap size={32} color="#2a2a35" style={{ margin: '0 auto 16px' }} />
                <div style={{ fontSize: '15px', fontWeight: 600, color: '#3d3d4d', marginBottom: '6px' }}>Formation at rest</div>
                <div style={{ fontSize: '13px', color: '#2d2d38' }}>Describe a goal to initialize the formation</div>
              </MotionDiv>
            )}

            {isSending && (
              <MotionDiv
                initial={{ opacity: 0 }}
                animate={{ opacity: 0.7 }}
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
        {activeNav !== 'dashboard' && activeNav !== 'knowledge' && (
        <div style={{ padding: '20px 28px', borderTop: '1px solid rgba(255,255,255,0.05)', flexShrink: 0 }}>
          {sendError && (
            <div style={{ marginBottom: '10px', background: 'rgba(255,92,92,0.08)', border: '1px solid rgba(255,92,92,0.22)', borderRadius: '10px', padding: '10px 12px', color: '#ffb3b3', fontSize: '12px' }}>
              {sendError}
            </div>
          )}
          <div style={{ background: '#141418', borderRadius: '12px', padding: '8px 8px 8px 16px', display: 'flex', alignItems: 'center', gap: '10px', border: '1px solid rgba(255,255,255,0.08)', transition: 'border-color 0.2s' }}>
            <input
              type="text"
              value={inputText}
              onChange={e => setInputText(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSendMessage()}
              placeholder="Describe a goal or intervene…"
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
      <section style={{ width: '420px', display: 'flex', flexDirection: 'column', background: '#0a0a0c', flexShrink: 0 }}>
        <header style={{ padding: '20px 24px', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: '16px', fontWeight: 700, color: '#edeeef' }}>Active Formation</h2>
          <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
            {runningCount > 0 && (
              <MotionSpan
                animate={{ opacity: [1, 0.5, 1] }}
                transition={{ duration: 2, repeat: Infinity }}
                style={{ fontSize: '11px', color: '#00d9ff', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '4px' }}
              >
                <Activity size={11} /> {runningCount} RUNNING
              </MotionSpan>
            )}
            <span style={{ fontSize: '11px', color: '#00f294', fontWeight: 700 }}>{completedCount} DONE</span>
          </div>
        </header>

        <div style={{ flex: 1, overflowY: 'auto', padding: '20px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
          {activeNav !== 'dashboard' && finishedTaskTree.length > 0 && (
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
                <span>Recent Finished · {finishedTaskTree.length}</span>
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
                    {finishedTaskTree.map(task => (
                      <TaskCard key={task.id} task={task} onArchive={() => handleArchiveTask(task.id)} />
                    ))}
                  </MotionDiv>
                )}
              </AnimatePresence>
            </div>
          )}
          <div style={{ background: '#101015', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '12px', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <div style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.12em' }}>LOADED CONTEXT</div>
            <div style={{ fontSize: '11px', color: '#888' }}>
              {loadedContext.files?.length || 0} pinned files · budget {loadedContext.budget_tokens || 0} tokens
            </div>
            {(loadedContext.files || []).slice(0, 3).map((entry) => (
              <div key={entry.path} style={{ fontSize: '11px', color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {entry.path} · {entry.estimated_tokens} tok
              </div>
            ))}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px', marginBottom: '8px' }}>
            <TelemetryCell value={runningCount || '—'} label="WORKING" />
            <TelemetryCell value={blockedCount || '—'} label="BLOCKED" />
            <TelemetryCell value={specClarificationCount || '—'} label="SPEC ASK" />
            <TelemetryCell value={activeTaskTree.length || '—'} label="ACTIVE" />
          </div>
          <AnimatePresence>
            {visibleTaskTree.map(task => (
              <TaskCard key={task.id} task={task} onArchive={() => handleArchiveTask(task.id)} />
            ))}
          </AnimatePresence>

          {visibleTaskTree.length === 0 && (
            <div style={{ textAlign: 'center', color: '#2d2d38', padding: '48px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px' }}>
              <Terminal size={28} color="#222228" />
              <div style={{ fontSize: '13px', color: '#333' }}>No tasks yet</div>
            </div>
          )}
        </div>

        {/* Telemetry — computed from live data */}
        <div style={{ padding: '16px 20px', borderTop: '1px solid rgba(255,255,255,0.05)', background: '#0c0c0e', flexShrink: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
            <span style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.12em' }}>FORMATION TELEMETRY</span>
            <Terminal size={12} style={{ color: '#333' }} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
            <TelemetryCell value={totalCount === 0 ? '—' : `${passRate}%`} label="PASS RATE" />
            <TelemetryCell value={runningCount || '—'} label="ACTIVE" />
            <TelemetryCell value={totalCount || '—'} label="TOTAL" />
          </div>
          <div style={{ marginTop: '12px', display: 'flex', flexDirection: 'column', gap: '6px', fontSize: '11px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
              <span style={{ color: '#7f8091' }}>weak eval / experiments</span>
              <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
                {telemetry?.overview?.weak_eval_runs || '—'} / {telemetry?.overview?.unique_experiments || '—'}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
              <span style={{ color: '#7f8091' }}>tiers</span>
              <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>{`${tiers.Weak}/${tiers.Strong}`}</span>
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

      {/* ── SETTINGS MODAL ──────────────────────────────────────────────────── */}
      <AnimatePresence>
        {showSettings && (
          <SettingsModal
            onClose={() => setShowSettings(false)}
            onResetDatabase={handleResetDatabase}
            apiUrl={API}
          />
        )}
      </AnimatePresence>
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

const LaneToggle = ({ lane, active, onClick }) => (
  <button
    onClick={onClick}
    style={{
      background: active ? 'rgba(130,87,229,0.18)' : 'rgba(255,255,255,0.03)',
      border: active ? '1px solid rgba(130,87,229,0.4)' : '1px solid rgba(255,255,255,0.08)',
      color: active ? '#f2ecff' : '#9a9cad',
      borderRadius: '999px',
      padding: '6px 12px',
      fontSize: '11px',
      fontWeight: 700,
      letterSpacing: '0.08em',
      cursor: 'pointer',
      textTransform: 'uppercase',
    }}
  >
    {lane}
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

const KnowledgeView = ({
  pages,
  query,
  selectedPage,
  selectedSlug,
  onQueryChange,
  onSelectSlug,
}) => {
  const relatedPages = selectedPage?.related_pages || [];

  return (
    <div style={{ flex: 1, overflow: 'hidden', display: 'grid', gridTemplateColumns: '320px minmax(0, 1fr)', gap: '0', minHeight: 0 }}>
      <div style={{ borderRight: '1px solid rgba(255,255,255,0.05)', background: '#0d0d11', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div style={{ padding: '20px', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ fontSize: '11px', color: '#7f8091', letterSpacing: '0.12em', fontWeight: 800 }}>KNOWLEDGE INDEX</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', background: '#141418', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '12px', padding: '10px 12px' }}>
            <Search size={15} color="#696a7b" />
            <input
              type="text"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              placeholder="Search titles, tags, aliases..."
              style={{ flex: 1, background: 'transparent', border: 'none', outline: 'none', color: '#edeeef', fontSize: '13px' }}
            />
          </div>
          <div style={{ fontSize: '12px', color: '#8d8ea1' }}>
            {pages.length ? `${pages.length} page${pages.length === 1 ? '' : 's'} visible` : 'No indexed pages yet'}
          </div>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: '10px' }}>
          {pages.map((page) => {
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

          {!pages.length && (
            <div style={{ padding: '18px 12px', color: '#8d8ea1', fontSize: '12px', lineHeight: 1.6 }}>
              Strata supports synthesized knowledge pages, but there are no indexed wiki pages yet. Once pages are compacted or written into the knowledge store, they will show up here.
            </div>
          )}
        </div>
      </div>

      <div style={{ minWidth: 0, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '18px' }}>
        {selectedPage ? (
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
        ) : (
          <div style={{ margin: 'auto 0', padding: '48px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '14px', textAlign: 'center' }}>
            <BookOpen size={34} color="#2f3040" />
            <div style={{ fontSize: '18px', fontWeight: 700, color: '#c7c8d6' }}>Knowledge wiki is ready</div>
            <div style={{ maxWidth: '520px', fontSize: '14px', lineHeight: 1.7, color: '#8d8ea1' }}>
              This view is wired up, but the indexed knowledge store is currently empty. Once Strata writes or compacts pages into the knowledge base, they will be navigable here like a wiki.
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
  specsSnapshot,
  specProposalSnapshot,
  knowledgePagesSnapshot,
  retentionSnapshot,
  variantRatingsSnapshot,
  predictionTrustSnapshot,
  operatorNotice,
  onRunRetention,
  onCompactKnowledge,
  onContextScan,
  onQueueBootstrap,
  onQueueSampleTick,
  onResolveSpecProposal,
}) => {
  const primaryDomainRatings = Object.entries(variantRatingsSnapshot?.by_domain?.['eval_harness_full_eval:bootstrap_mcq_v1'] || {})
    .sort((a, b) => (b[1]?.rating || 0) - (a[1]?.rating || 0))
    .slice(0, 5);
  const strongTrust = predictionTrustSnapshot?.by_tier?.strong;

  return (
  <div style={{ flex: 1, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
    <DashboardPanel title="SYSTEM STATUS">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
        <TelemetryCell value={telemetry?.overview?.weak_eval_runs || '—'} label="WEAK EVAL" />
        <TelemetryCell value={telemetry?.overview?.unique_experiments || '—'} label="EXPERIMENTS" />
        <TelemetryCell value={dashboard?.ignition?.detected ? 'LIVE' : 'NO'} label="IGNITION" />
        <TelemetryCell value={`${tiers.Weak}/${tiers.Strong}`} label="WEAK/STRONG" />
      </div>
    </DashboardPanel>

    <DashboardPanel title="FAILURE & GOVERNANCE">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
        <TelemetryCell value={dashboard?.failure_pressure?.recent_failures ?? '—'} label="FAIL PRESSURE" />
        <TelemetryCell value={dashboard?.failure_pressure?.recent_research_failures ?? '—'} label="RESEARCH FAIL" />
        <TelemetryCell value={dashboard?.context_pressure?.warning_count ?? '—'} label="CTX WARN" />
        <TelemetryCell value={dashboard?.spec_governance?.pending_count ?? '—'} label="SPEC PENDING" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
        <div style={{ fontSize: '12px', color: '#a9aaba' }}>Current promoted</div>
        <div style={{ fontSize: '12px', color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {dashboard?.current_promoted_candidate || '—'}
        </div>
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
              <Sparkline values={accuracySeries} color={variant.mode === 'strong' ? '#00f294' : '#8257e5'} />
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
          {routingSummary?.chat?.error ? routingSummary.chat.error : `${routingSummary?.chat?.mode || '—'} · ${routingSummary?.chat?.provider || '—'} · ${routingSummary?.chat?.selected_model || routingSummary?.chat?.model || '—'}`}
        </span>
        <span style={{ color: '#8d8ea1' }}>Strong tier</span>
        <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
          {routingSummary?.strong?.error ? routingSummary.strong.error : `${routingSummary?.strong?.transport || '—'} · ${routingSummary?.strong?.provider || '—'} · ${routingSummary?.strong?.selected_model || routingSummary?.strong?.model || '—'} (${routingSummary?.strong?.status || 'unknown'})`}
        </span>
        <span style={{ color: '#8d8ea1' }}>Weak tier</span>
        <span style={{ color: '#e7e8ef', fontFamily: "'JetBrains Mono', monospace" }}>
          {routingSummary?.weak?.error ? routingSummary.weak.error : `${routingSummary?.weak?.transport || '—'} · ${routingSummary?.weak?.provider || '—'} · ${routingSummary?.weak?.selected_model || routingSummary?.weak?.model || '—'} (${routingSummary?.weak?.status || 'unknown'})`}
        </span>
        <span style={{ color: '#8d8ea1' }}>Supervision</span>
        <span style={{ color: '#e7e8ef' }}>
          {routingSummary?.supervision?.active_jobs?.length
            ? `${routingSummary.supervision.active_jobs.length} supervision job${routingSummary.supervision.active_jobs.length > 1 ? 's' : ''}`
            : 'No bootstrap jobs queued'}
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
        <TelemetryCell value={strongTrust ? strongTrust.trust.toFixed(3) : '—'} label="STRONG TRUST" />
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
