import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { Database } from 'lucide-react';

const PROVIDER_SETUP_LINKS = [
  { label: 'Cerebras Key', href: 'https://cloud.cerebras.ai/' },
  { label: 'Google AI Studio Key', href: 'https://aistudio.google.com/apikey' },
  { label: 'OpenRouter Keys', href: 'https://openrouter.ai/settings/keys' },
];

const API_KEY_LINKS = {
  cerebras: 'https://cloud.cerebras.ai/',
  google: 'https://aistudio.google.com/apikey',
  openrouter: 'https://openrouter.ai/settings/keys',
};

const DashboardPanel = ({ title, children }) => (
  <div style={{ background: '#141418', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '14px', padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
    <div style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '0.12em' }}>{title}</div>
    {children}
  </div>
);

export default function SettingsView({ onResetDatabase, apiUrl, currentScope = 'home' }) {
  const [resetConfirm, setResetConfirm] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetDone, setResetDone] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [maxSyncIters, setMaxSyncIters] = useState(3);
  const [automaticTaskGeneration, setAutomaticTaskGeneration] = useState(false);
  const [testingMode, setTestingMode] = useState(false);
  const [replayPendingOnStartup, setReplayPendingOnStartup] = useState(false);
  const [allowCloudOnlyBoot, setAllowCloudOnlyBoot] = useState(false);
  const [heavyReflectionMode, setHeavyReflectionMode] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
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
    } catch (e) {
      console.error('Failed to load settings', e);
    }
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
    if (pool === 'trainer') next[pool][index].transport = 'cloud';
    if (pool === 'agent') next[pool][index].transport = 'local';
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
    if (!resetConfirm) {
      setResetConfirm(true);
      return;
    }
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

      <DashboardPanel title="MODEL REGISTRY">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
          <div style={sectionLabel}>MODEL REGISTRY</div>
          {savingRegistry && <span style={{ fontSize: '10px', color: '#8257e5', fontWeight: 700 }}>SAVING…</span>}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '12px' }}>
          {PROVIDER_SETUP_LINKS.map((link) => (
            <a key={link.href} href={link.href} target="_blank" rel="noreferrer" style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '999px', color: '#d8d9e6', padding: '6px 10px', fontSize: '11px', textDecoration: 'none' }}>
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
                  <button key={presetKey} onClick={() => applyPreset('trainer', presetKey)} style={{ background: 'rgba(130,87,229,0.15)', border: '1px solid rgba(130,87,229,0.3)', borderRadius: '999px', color: '#cfc3ff', padding: '6px 10px', fontSize: '11px', cursor: 'pointer' }}>
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
                  <button key={presetKey} onClick={() => applyPreset('agent', presetKey)} style={{ background: 'rgba(0,217,255,0.12)', border: '1px solid rgba(0,217,255,0.25)', borderRadius: '999px', color: '#9fefff', padding: '6px 10px', fontSize: '11px', cursor: 'pointer' }}>
                    {presetKey}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {visiblePools.includes('trainer') && (
            <div style={inputGroupStyle}>
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#8257e5', marginBottom: '4px', letterSpacing: '0.05em' }}>STRONG POOL (CLOUD)</div>
              {registryConfig.trainer?.[0]?.provider && API_KEY_LINKS[registryConfig.trainer[0].provider] && (
                <a href={API_KEY_LINKS[registryConfig.trainer[0].provider]} target="_blank" rel="noreferrer" style={{ fontSize: '11px', color: '#bca9ff', textDecoration: 'none' }}>
                  Open {registryConfig.trainer[0].provider} API key page
                </a>
              )}
              <input style={infoValue} placeholder="Model (e.g. anthropic/claude-3.5-sonnet)" value={registryConfig.trainer?.[0]?.model || ''} onChange={(e) => handleUpdateRegistry('trainer', 'model', e.target.value)} />
              <input style={infoValue} placeholder="Endpoint URL (e.g. https://openrouter.ai/api/v1/chat/completions)" value={registryConfig.trainer?.[0]?.endpoint_url || ''} onChange={(e) => handleUpdateRegistry('trainer', 'endpoint_url', e.target.value)} />
              <input style={infoValue} placeholder="API Key Env (e.g. OPENROUTER_API_KEY)" value={registryConfig.trainer?.[0]?.api_key_env || ''} onChange={(e) => handleUpdateRegistry('trainer', 'api_key_env', e.target.value)} />
              <input type="number" style={infoValue} placeholder="Requests / minute (optional)" value={registryConfig.trainer?.[0]?.requests_per_minute || ''} onChange={(e) => handleUpdateRegistry('trainer', 'requests_per_minute', e.target.value ? parseInt(e.target.value, 10) : null)} />
              <input type="number" style={infoValue} placeholder="Max concurrency (optional)" value={registryConfig.trainer?.[0]?.max_concurrency || ''} onChange={(e) => handleUpdateRegistry('trainer', 'max_concurrency', e.target.value ? parseInt(e.target.value, 10) : null)} />
              <input type="number" style={infoValue} placeholder="Min interval ms (optional)" value={registryConfig.trainer?.[0]?.min_interval_ms || ''} onChange={(e) => handleUpdateRegistry('trainer', 'min_interval_ms', e.target.value ? parseInt(e.target.value, 10) : null)} />
            </div>
          )}
          {visiblePools.includes('agent') && (
            <div style={inputGroupStyle}>
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#00d9ff', marginBottom: '4px', letterSpacing: '0.05em' }}>WEAK POOL (LOCAL)</div>
              <input style={infoValue} placeholder="Model (e.g. qwen3.5-9b-distilled)" value={registryConfig.agent?.[0]?.model || ''} onChange={(e) => handleUpdateRegistry('agent', 'model', e.target.value)} />
              <input style={infoValue} placeholder="Endpoint URL (e.g. http://127.0.0.1:1234/v1/chat/completions)" value={registryConfig.agent?.[0]?.endpoint_url || ''} onChange={(e) => handleUpdateRegistry('agent', 'endpoint_url', e.target.value)} />
              <input type="number" style={infoValue} placeholder="Requests / minute (optional)" value={registryConfig.agent?.[0]?.requests_per_minute || ''} onChange={(e) => handleUpdateRegistry('agent', 'requests_per_minute', e.target.value ? parseInt(e.target.value, 10) : null)} />
              <input type="number" style={infoValue} placeholder="Max concurrency (optional)" value={registryConfig.agent?.[0]?.max_concurrency || ''} onChange={(e) => handleUpdateRegistry('agent', 'max_concurrency', e.target.value ? parseInt(e.target.value, 10) : null)} />
              <input type="number" style={infoValue} placeholder="Min interval ms (optional)" value={registryConfig.agent?.[0]?.min_interval_ms || ''} onChange={(e) => handleUpdateRegistry('agent', 'min_interval_ms', e.target.value ? parseInt(e.target.value, 10) : null)} />
            </div>
          )}
        </div>
      </DashboardPanel>

      <DashboardPanel title="ORCHESTRATOR">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
          <div style={sectionLabel}>ORCHESTRATOR</div>
          {savingSettings && <span style={{ fontSize: '10px', color: '#8257e5', fontWeight: 700 }}>SAVING…</span>}
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span style={{ fontSize: '13px', color: '#888', flex: 1 }}>Max Synchronous Tool Iterations</span>
          <input type="number" value={maxSyncIters} onChange={(e) => setMaxSyncIters(e.target.value)} onBlur={(e) => void persistSettings({ max_sync_tool_iterations: e.target.value })} min="1" max="10" style={{ ...infoValue, width: '60px', textAlign: 'center', opacity: savingSettings ? 0.5 : 1, padding: '10px 0' }} />
        </div>
        <div style={{ fontSize: '11px', color: '#555', marginTop: '6px' }}>
          Limits how many times the model can independently recurse tools on a single message.
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginTop: '16px' }}>
          {[
            ['Automatically generate tasks', automaticTaskGeneration, setAutomaticTaskGeneration, 'automatic_task_generation', 'Lets the chat model spawn background research and implementation tasks on its own. Default is off for quieter testing.'],
            ['Testing mode', testingMode, setTestingMode, 'testing_mode', 'Suppresses autonomous idle task generation so you can run focused evaluations without extra noise.'],
            ['Replay pending backlog on startup', replayPendingOnStartup, setReplayPendingOnStartup, 'replay_pending_tasks_on_startup', 'Re-enqueues old pending tasks after a reboot. Leave this off unless you intentionally want to resume backlog work.'],
            ['Allow cloud-only boot', allowCloudOnlyBoot, setAllowCloudOnlyBoot, 'allow_cloud_only_boot', 'Lets the worker start on the trainer tier when the local agent endpoint is unavailable instead of failing startup.'],
            ['Heavy reflection mode', heavyReflectionMode, setHeavyReflectionMode, 'heavy_reflection_mode', 'Makes the trainer lane seed larger bootstrap supervision batches when idle so overnight runs synthesize telemetry faster.'],
          ].map(([label, checked, setter, field, helper]) => (
            <label key={field} style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', color: '#ccc', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={checked}
                onChange={(e) => {
                  const next = e.target.checked;
                  setter(next);
                  void persistSettings({ [field]: next });
                }}
              />
              <span>
                {label}
                <div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>{helper}</div>
              </span>
            </label>
          ))}
        </div>
      </DashboardPanel>

      <DashboardPanel title="DANGER ZONE">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Database size={14} color="#ff4d4d" />
          <span style={{ fontSize: '11px', fontWeight: 700, color: '#ff4d4d', letterSpacing: '0.08em' }}>DANGER ZONE</span>
        </div>
        <div style={{ background: 'rgba(255,77,77,0.04)', border: '1px solid rgba(255,77,77,0.15)', borderRadius: '12px', padding: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontSize: '14px', fontWeight: 600, color: '#edeeef', marginBottom: '4px' }}>Fresh Start</div>
            <div style={{ fontSize: '12px', color: '#888' }}>Stops active work, clears runtime state, wipes task history, and leaves the worker paused.</div>
          </div>
          <button
            onClick={handleReset}
            disabled={resetting}
            style={{ background: resetConfirm ? 'rgba(255,77,77,0.8)' : 'rgba(255,77,77,0.15)', border: '1px solid rgba(255,77,77,0.4)', borderRadius: '8px', padding: '8px 16px', color: resetConfirm ? '#fff' : '#ff4d4d', fontSize: '12px', fontWeight: 700, cursor: 'pointer', whiteSpace: 'nowrap', transition: 'all 0.2s' }}
          >
            {resetting ? 'Refreshing…' : resetDone ? '✓ Done' : resetConfirm ? 'Confirm Fresh Start' : 'Fresh Start'}
          </button>
        </div>
      </DashboardPanel>
    </div>
  );
}
