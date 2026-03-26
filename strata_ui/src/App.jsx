import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { motion, AnimatePresence } from 'framer-motion';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Plus, RefreshCw, Zap,
  MessageSquare, Send, History, Cpu,
  Terminal, AlertCircle, X, Settings,
  Activity, Trash2, Database,
  Pause, Play, Square
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
    {sessionList.map(s => (
      <SessionRow
        key={s}
        s={s}
        active={sessionId === s}
        onClick={() => setSessionId(s)}
        onDelete={() => deleteSession(s)}
      />
    ))}
  </div>
);

const SessionRow = ({ s, active, onClick, onDelete }) => {
  const [hovered, setHovered] = useState(false);
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
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
      </div>
      {(hovered || active) && (
        <button
          onClick={e => { e.stopPropagation(); onDelete(); }}
          style={{ background: 'none', border: 'none', color: '#ff4d4d', cursor: 'pointer', padding: '2px', display: 'flex', opacity: 0.7, flexShrink: 0 }}
        >
          <Trash2 size={13} />
        </button>
      )}
    </div>
  );
};

// ─── App ──────────────────────────────────────────────────────────────────────
function App() {
  const [messages, setMessages]       = useState([]);
  const [tasks, setTasks]             = useState([]);
  const [inputText, setInputText]     = useState('');
  const [isSending, setIsSending]     = useState(false);
  const [sessionId, setSessionId]     = useState('default');
  const [sessionList, setSessionList] = useState([]);
  const [activeNav, setActiveNav]     = useState('chat');   // 'chat' | 'history' | 'settings'
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
      const [tasksRes, msgsRes, sessionsRes, telemetryRes, providerTelemetryRes, dashboardRes] = await Promise.all([
        axios.get(`${API}/tasks`),
        axios.get(`${API}/messages?session_id=${sessionId}`),
        axios.get(`${API}/sessions`),
        axios.get(`${API}/admin/telemetry?limit=8`),
        axios.get(`${API}/admin/providers/telemetry`),
        axios.get(`${API}/admin/dashboard?limit=6`)
      ]);

      // If a newer fetch was launched while we were awaiting, discard this result
      if (gen !== fetchGenRef.current) return;

      setTasks(tasksRes.data);
      setMessages(msgsRes.data);
      const sessions = sessionsRes.data;
      if (!sessions.includes(sessionId)) sessions.push(sessionId);
      setSessionList(sessions);
      setTelemetry(telemetryRes.data.telemetry);
      setProviderTelemetry(providerTelemetryRes.data.providers || {});
      setDashboard(dashboardRes.data.dashboard || null);
      setApiStatus('ok');
    } catch (err) {
      if (gen !== fetchGenRef.current) return;
      console.error('Fetch failed', err);
      setApiStatus('error');
    }
  }, [sessionId]);

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

  const handleSendMessage = async () => {
    if (!inputText.trim() || isSending) return;
    const text = inputText;
    setInputText('');
    setIsSending(true);
    isSendingRef.current = true;
    // Optimistic update: show the user's message immediately
    setMessages(prev => [...prev, { id: `temp-${Date.now()}`, role: 'user', content: text }]);
    try {
      await axios.post(`${API}/chat`, { role: 'user', content: text, session_id: sessionId });
    } catch (err) {
      console.error('Failed to send message.', err);
    }
    // Force-fetch BEFORE clearing the sending lock, so no poll can sneak in
    await fetchData(true);
    setIsSending(false);
    isSendingRef.current = false;
  };

  const startNewChat = () => {
    const newId = `session-${Date.now()}`;
    setSessionId(newId);
    setMessages([]);
    setInputText('');
  };

  const deleteSession = async (idToDelete) => {
    try {
      await axios.delete(`${API}/sessions/${idToDelete}`);
      setSessionList(prev => prev.filter(s => s !== idToDelete));
      if (sessionId === idToDelete) {
        setSessionId('default');
        setMessages([]);
      }
    } catch (err) {
      console.error('Failed to delete session.', err);
    }
  };

  const handleResetDatabase = async () => {
    await axios.post(`${API}/admin/reset`);
    setSessionList([]);
    setSessionId('default');
    setMessages([]);
    setTasks([]);
  };

  // Derived telemetry from live data
  const completedCount  = tasks.filter(t => t.status === 'complete').length;
  const runningCount    = tasks.filter(t => t.status === 'working').length;
  const totalCount      = tasks.length;
  const passRate        = totalCount > 0 ? Math.round((completedCount / totalCount) * 100) : '—';

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

    // Sort roots for main display
    roots.sort((a, b) => {
      const wA = ['complete', 'abandoned', 'cancelled'].includes(a.status) ? 1 : ['working', 'blocked', 'pushed'].includes(a.status) ? 2 : 3;
      const wB = ['complete', 'abandoned', 'cancelled'].includes(b.status) ? 1 : ['working', 'blocked', 'pushed'].includes(b.status) ? 2 : 3;
      if (wA !== wB) return wA - wB;
      return a.id.localeCompare(b.id);
    });

    return roots;
  }, [tasks, archivedTasks]);

  const sessionLabel = sessionId === 'default'
    ? 'Genesis Session'
    : (() => {
        const ts = parseInt(sessionId.replace('session-', ''), 10);
        return isNaN(ts) ? sessionId : new Date(ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
      })();

  // ── Icon Nav items ───────────────────────────────────────────────────────────
  const navItems = [
    { id: 'chat',    Icon: MessageSquare, label: 'Chat'     },
    { id: 'history', Icon: History,       label: 'History'  },
  ];

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
        </header>

        <div style={{ flex: 1, overflowY: 'auto', padding: '8px' }}>
          <HistoryPane
            sessionList={sessionList}
            sessionId={sessionId}
            setSessionId={setSessionId}
            deleteSession={deleteSession}
          />
        </div>
      </div>

      {/* ── COLUMN 3: CHAT ─────────────────────────────────────────────────── */}
      <section style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#0a0a0c', borderRight: '1px solid rgba(255,255,255,0.05)', minWidth: 0 }}>
        <header style={{ padding: '20px 28px', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
          <div>
            <h1 style={{ fontSize: '18px', fontWeight: 700, color: 'white' }}>Orchestrator Chat</h1>
            <p style={{ fontSize: '12px', color: '#555', marginTop: '2px' }}>{sessionLabel}</p>
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

        {/* Messages */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <AnimatePresence initial={false}>
            {messages.map((msg, i) => (
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
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                </div>
              </MotionDiv>
            ))}

            {messages.length === 0 && !isSending && (
              <MotionDiv
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                style={{ textAlign: 'center', color: '#333', marginTop: 'auto', marginBottom: 'auto', padding: '48px 32px' }}
              >
                <Zap size={32} color="#2a2a35" style={{ margin: '0 auto 16px' }} />
                <div style={{ fontSize: '15px', fontWeight: 600, color: '#3d3d4d', marginBottom: '6px' }}>Swarm at rest</div>
                <div style={{ fontSize: '13px', color: '#2d2d38' }}>Describe a goal to initialize the swarm</div>
              </MotionDiv>
            )}

            {isSending && (
              <MotionDiv
                initial={{ opacity: 0 }}
                animate={{ opacity: 0.7 }}
                style={{ alignSelf: 'flex-start', background: '#1c1c22', padding: '12px 18px', borderRadius: '16px 16px 16px 4px', color: '#888', fontSize: '13px', fontStyle: 'italic', border: '1px solid rgba(255,255,255,0.05)' }}
              >
                <span style={{ display: 'inline-flex', gap: '4px', alignItems: 'center' }}>
                  Swarm is formulating
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

        {/* Input bar */}
        <div style={{ padding: '20px 28px', borderTop: '1px solid rgba(255,255,255,0.05)', flexShrink: 0 }}>
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
      </section>

      {/* ── COLUMN 4: TASK SWARM ────────────────────────────────────────────── */}
      <section style={{ width: '420px', display: 'flex', flexDirection: 'column', background: '#0a0a0c', flexShrink: 0 }}>
        <header style={{ padding: '20px 24px', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: '16px', fontWeight: 700, color: '#edeeef' }}>Active Swarm</h2>
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
          <AnimatePresence>
            {taskTree.map(task => (
              <TaskCard key={task.id} task={task} onArchive={() => handleArchiveTask(task.id)} />
            ))}
          </AnimatePresence>

          {taskTree.length === 0 && (
            <div style={{ textAlign: 'center', color: '#2d2d38', padding: '48px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px' }}>
              <Terminal size={28} color="#222228" />
              <div style={{ fontSize: '13px', color: '#333' }}>No tasks yet</div>
            </div>
          )}
        </div>

        {/* Telemetry — computed from live data */}
        <div style={{ padding: '16px 20px', borderTop: '1px solid rgba(255,255,255,0.05)', background: '#0c0c0e', flexShrink: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
            <span style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.12em' }}>SWARM TELEMETRY</span>
            <Terminal size={12} style={{ color: '#333' }} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
            <TelemetryCell value={totalCount === 0 ? '—' : `${passRate}%`} label="PASS RATE" />
            <TelemetryCell value={runningCount || '—'} label="ACTIVE" />
            <TelemetryCell value={totalCount || '—'} label="TOTAL" />
          </div>
          {telemetry && (
            <div style={{ marginTop: '14px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
                <TelemetryCell value={telemetry.overview.weak_eval_runs || '—'} label="WEAK EVAL" />
                <TelemetryCell value={telemetry.overview.unique_experiments || '—'} label="EXPERIMENTS" />
                <TelemetryCell value={`${tiers.Weak}/${tiers.Strong}`} label="WEAK/STRONG" />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {telemetry.rollups.slice(0, 3).map((rollup) => (
                  <div key={rollup.metric_name} style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', fontSize: '11px' }}>
                    <span style={{ color: '#7f8091', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{rollup.metric_name}</span>
                    <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
                      {rollup.avg_value} avg · {rollup.count}
                    </span>
                  </div>
                ))}
              </div>
              {Object.keys(providerTelemetry).length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '4px' }}>
                  <div style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.12em' }}>TRANSPORT</div>
                  {Object.entries(providerTelemetry).slice(0, 2).map(([key, stats]) => (
                    <div key={key} style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', fontSize: '11px' }}>
                      <span style={{ color: '#7f8091', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{key}</span>
                      <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace" }}>
                        {stats.rate_limit_hits} rl · {stats.avg_wait_ms}ms wait
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {dashboard && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginTop: '6px' }}>
                  <div style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.12em' }}>BOOTSTRAP</div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
                    <TelemetryCell
                      value={dashboard.ignition?.detected ? 'LIVE' : 'NO'}
                      label="IGNITION"
                    />
                    <TelemetryCell value={dashboard.promotion_counts?.weak ?? '—'} label="WEAK WINS" />
                    <TelemetryCell value={dashboard.promotion_counts?.strong ?? '—'} label="STRONG WINS" />
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
                    <TelemetryCell value={dashboard.failure_pressure?.recent_failures ?? '—'} label="FAIL PRESSURE" />
                    <TelemetryCell value={dashboard.failure_pressure?.recent_research_failures ?? '—'} label="RESEARCH FAIL" />
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', fontSize: '11px' }}>
                      <span style={{ color: '#7f8091', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Current promoted</span>
                      <span style={{ color: '#c7c8d6', fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {dashboard.current_promoted_candidate || '—'}
                      </span>
                    </div>
                    {dashboard.reports?.slice(0, 3).map((report) => (
                      <div key={report.candidate_change_id} style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', fontSize: '11px' }}>
                        <span style={{ color: '#7f8091', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {report.proposal_metadata?.proposer_tier || 'unknown'} · {report.candidate_change_id}
                        </span>
                        <span style={{ color: report.recommendation === 'promote' ? '#00f294' : '#ffb84d', fontFamily: "'JetBrains Mono', monospace" }}>
                          {report.recommendation}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
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

export default App;
