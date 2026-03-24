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


// ─── Settings Modal ────────────────────────────────────────────────────────────
const SettingsModal = ({ onClose, onResetDatabase, apiUrl }) => {
  const [resetConfirm, setResetConfirm] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetDone, setResetDone] = useState(false);

  // Test connection state
  const [testResult, setTestResult] = useState(null); // null | { ok, latency?, error?, llm? }
  const [testing, setTesting] = useState(false);

  // Model selector state
  const [models, setModels] = useState([]);
  const [currentModel, setCurrentModel] = useState(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState(null);
  const [selectingModel, setSelectingModel] = useState(null);

  // Orchestrator Settings
  const [maxSyncIters, setMaxSyncIters] = useState(3);
  const [savingSettings, setSavingSettings] = useState(false);

  // Load state on mount
  useEffect(() => {
    loadModels();
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const res = await axios.get(`${apiUrl}/admin/settings`);
      if (res.data.status === 'ok') {
        setMaxSyncIters(res.data.settings.max_sync_tool_iterations || 3);
      }
    } catch (e) { console.error('Failed to load settings', e); }
  };

  const handleSaveSettings = async (val) => {
    setSavingSettings(true);
    try {
      await axios.post(`${apiUrl}/admin/settings`, { max_sync_tool_iterations: parseInt(val, 10) || 3 });
    } catch (e) {
      console.error('Failed to save settings', e);
    } finally {
      setSavingSettings(false);
    }
  };

  const loadModels = async () => {
    setModelsLoading(true);
    setModelsError(null);
    try {
      const res = await axios.get(`${apiUrl}/models`);
      if (res.data.status === 'ok') {
        setModels(res.data.models);
        setCurrentModel(res.data.current);
      } else {
        setModelsError(res.data.message || 'Could not reach model provider');
        setModels([]);
      }
    } catch (e) {
      setModelsError('Strata API unreachable — is the backend running?');
      setModels([]);
    } finally {
      setModelsLoading(false);
    }
  };

  const handleSelectModel = async (modelId) => {
    setSelectingModel(modelId);
    try {
      await axios.post(`${apiUrl}/models/select`, { model: modelId });
      setCurrentModel(modelId);
    } catch (e) {
      console.error('Model selection failed', e);
    } finally {
      setSelectingModel(null);
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
    } catch (e) {
      setTestResult({ ok: false, error: e.message || 'Connection failed' });
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
    borderRadius: '8px', padding: '10px 14px', color: '#888',
    fontSize: '13px', fontFamily: "'JetBrains Mono', monospace",
  };

  return (
    <motion.div
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
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 20 }}
        onClick={e => e.stopPropagation()}
        style={{
          background: '#141418', border: '1px solid rgba(255,255,255,0.1)',
          borderRadius: '20px', padding: '32px', width: '520px',
          display: 'flex', flexDirection: 'column', gap: '20px',
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
            <div style={{ ...infoValue, flex: 1 }}>{apiUrl}</div>
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
            <div style={{ marginTop: '8px', fontSize: '12px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: testResult.ok ? '#00f294' : '#ff4d4d' }}>
                {testResult.ok
                  ? <><Activity size={12} /> Strata API — connected ({testResult.latency}ms)</>
                  : <><AlertCircle size={12} /> {testResult.error}</>
                }
              </div>
              {testResult.ok && testResult.llm && (
                <div style={{ color: '#555', fontSize: '11px' }}>
                  LLM provider → <span style={{ color: '#888', fontFamily: "'JetBrains Mono', monospace" }}>{testResult.llm}</span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Orchestrator Settings ────────────────────────────────────────── */}
        <div>
          <div style={sectionLabel}>ORCHESTRATOR</div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <span style={{ fontSize: '13px', color: '#888', flex: 1 }}>Max Synchronous Tool Iterations</span>
            <input 
              type="number" 
              value={maxSyncIters} 
              onChange={e => setMaxSyncIters(e.target.value)}
              onBlur={e => handleSaveSettings(e.target.value)}
              min="1" max="10"
              style={{ ...infoValue, width: '60px', textAlign: 'center', opacity: savingSettings ? 0.5 : 1 }}
            />
          </div>
          <div style={{ fontSize: '11px', color: '#555', marginTop: '6px' }}>
            Limits how many times the model can independently recurse tools on a single message.
          </div>
        </div>

        {/* ── Model Selector ───────────────────────────────────────────────── */}
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
            <div style={sectionLabel}>MODEL</div>
            <button
              onClick={loadModels}
              disabled={modelsLoading}
              style={{ background: 'none', border: 'none', color: '#555', cursor: 'pointer', padding: '2px', display: 'flex' }}
              title="Refresh model list"
            >
              <RefreshCw size={13} style={{ animation: modelsLoading ? 'spin 1s linear infinite' : 'none' }} />
            </button>
          </div>

          {modelsError && (
            <div style={{ fontSize: '12px', color: '#ff4d4d', display: 'flex', alignItems: 'center', gap: '6px', padding: '10px 12px', background: 'rgba(255,77,77,0.05)', borderRadius: '8px', border: '1px solid rgba(255,77,77,0.15)', marginBottom: '8px' }}>
              <AlertCircle size={13} /> {modelsError}
            </div>
          )}

          {modelsLoading && models.length === 0 && (
            <div style={{ fontSize: '12px', color: '#555', padding: '12px', textAlign: 'center' }}>Loading models…</div>
          )}

          {models.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', maxHeight: '180px', overflowY: 'auto' }}>
              {models.map(m => (
                <button
                  key={m}
                  onClick={() => handleSelectModel(m)}
                  disabled={selectingModel === m}
                  style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    background: m === currentModel ? 'rgba(130,87,229,0.12)' : 'rgba(255,255,255,0.02)',
                    border: m === currentModel ? '1px solid rgba(130,87,229,0.4)' : '1px solid rgba(255,255,255,0.05)',
                    borderRadius: '8px', padding: '10px 14px', cursor: 'pointer',
                    color: m === currentModel ? '#8257e5' : '#aaa',
                    fontSize: '12px', fontFamily: "'JetBrains Mono', monospace",
                    textAlign: 'left', transition: 'all 0.15s'
                  }}
                >
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m}</span>
                  {m === currentModel && <span style={{ fontSize: '10px', fontWeight: 800, color: '#8257e5', flexShrink: 0, marginLeft: '8px' }}>ACTIVE</span>}
                  {selectingModel === m && <span style={{ fontSize: '10px', color: '#888', flexShrink: 0, marginLeft: '8px' }}>…</span>}
                </button>
              ))}
            </div>
          )}

          {models.length === 0 && !modelsLoading && !modelsError && (
            <div style={{ fontSize: '12px', color: '#555', padding: '12px', textAlign: 'center', background: 'rgba(255,255,255,0.02)', borderRadius: '8px' }}>
              No models available — is the LLM provider running?
            </div>
          )}
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
          {resetConfirm && !resetting && (
            <p style={{ fontSize: '11px', color: '#ff4d4d', textAlign: 'center' }}>
              Click "Confirm Reset" to proceed. This cannot be undone.
            </p>
          )}
        </div>
      </motion.div>
    </motion.div>
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

  const fetchWorkerStatus = useCallback(async () => {
    try {
      const res = await axios.get(`${API}/admin/worker/status`);
      setWorkerStatus(res.data.status);
    } catch (e) { console.error('Failed to fetch worker status', e); }
  }, [API]);

  useEffect(() => {
    fetchWorkerStatus();
    const interval = setInterval(fetchWorkerStatus, 5000);
    return () => clearInterval(interval);
  }, [fetchWorkerStatus]);

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
      const [tasksRes, msgsRes, sessionsRes] = await Promise.all([
        axios.get(`${API}/tasks`),
        axios.get(`${API}/messages?session_id=${sessionId}`),
        axios.get(`${API}/sessions`)
      ]);

      // If a newer fetch was launched while we were awaiting, discard this result
      if (gen !== fetchGenRef.current) return;

      setTasks(tasksRes.data);
      setMessages(msgsRes.data);
      const sessions = sessionsRes.data;
      if (!sessions.includes(sessionId)) sessions.push(sessionId);
      setSessionList(sessions);
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
    // Second pass: link children
    filteredTasks.forEach(t => {
      if (t.parent_id && map[t.parent_id]) {
        map[t.parent_id].children.push(map[t.id]);
      } else if (!t.parent_id) {
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

      {/* ── COLUMN 1: ICON NAV ─────────────────────────────────────────────── */}
      <div style={{ width: '72px', borderRight: '1px solid rgba(255,255,255,0.05)', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '20px 0', gap: '8px' }}>
        {/* Logo */}
        <motion.div
          animate={{ rotate: 360 }}
          transition={{ duration: 20, repeat: Infinity, ease: 'linear' }}
          style={{ width: '38px', height: '38px', borderRadius: '11px', background: 'linear-gradient(135deg, #8257e5, #5e33ba)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: '16px', boxShadow: '0 0 18px rgba(130,87,229,0.3)' }}
        >
          <div style={{ width: '12px', height: '12px', background: 'white', borderRadius: '2px', transform: 'rotate(45deg)' }} />
        </motion.div>

        {navItems.map(({ id, Icon, label }) => (
          <NavIcon
            key={id}
            Icon={Icon}
            label={label}
            active={activeNav === id}
            onClick={() => setActiveNav(id)}
          />
        ))}

        {/* Spacer */}
        <div style={{ flex: 1 }} />

        {/* Force refresh */}
        <NavIcon
          Icon={RefreshCw}
          label="Refresh"
          active={false}
          onClick={fetchData}
        />

        {/* Settings */}
        <NavIcon
          Icon={Cpu}
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
              <span style={{ fontSize: '11px', color: '#ff4d4d', fontWeight: 600 }}>API DOWN</span>
            )}
          </div>
        </header>

        {/* Messages */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '28px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <AnimatePresence initial={false}>
            {messages.map((msg, i) => (
              <motion.div
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
              </motion.div>
            ))}

            {messages.length === 0 && !isSending && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                style={{ textAlign: 'center', color: '#333', marginTop: 'auto', marginBottom: 'auto', padding: '48px 32px' }}
              >
                <Zap size={32} color="#2a2a35" style={{ margin: '0 auto 16px' }} />
                <div style={{ fontSize: '15px', fontWeight: 600, color: '#3d3d4d', marginBottom: '6px' }}>Swarm at rest</div>
                <div style={{ fontSize: '13px', color: '#2d2d38' }}>Describe a goal to initialize the swarm</div>
              </motion.div>
            )}

            {isSending && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 0.7 }}
                style={{ alignSelf: 'flex-start', background: '#1c1c22', padding: '12px 18px', borderRadius: '16px 16px 16px 4px', color: '#888', fontSize: '13px', fontStyle: 'italic', border: '1px solid rgba(255,255,255,0.05)' }}
              >
                <span style={{ display: 'inline-flex', gap: '4px', alignItems: 'center' }}>
                  Swarm is formulating
                  {[0,1,2].map(i => (
                    <motion.span
                      key={i}
                      animate={{ opacity: [0.2, 1, 0.2] }}
                      transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.2 }}
                      style={{ fontSize: '18px', lineHeight: 0.6 }}
                    >·</motion.span>
                  ))}
                </span>
              </motion.div>
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
              <motion.span
                animate={{ opacity: [1, 0.5, 1] }}
                transition={{ duration: 2, repeat: Infinity }}
                style={{ fontSize: '11px', color: '#00d9ff', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '4px' }}
              >
                <Activity size={11} /> {runningCount} RUNNING
              </motion.span>
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
const NavIcon = ({ Icon, label, active, onClick }) => (
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
    <Icon size={20} />
  </button>
);

const TelemetryCell = ({ value, label }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
    <div style={{ fontSize: '16px', fontWeight: 800, color: '#edeeef', fontFamily: "'JetBrains Mono', monospace" }}>{value}</div>
    <div style={{ fontSize: '9px', color: '#444', fontWeight: 700, letterSpacing: '0.1em' }}>{label}</div>
  </div>
);

export default App;
