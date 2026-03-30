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

const isDesktopRuntime = () =>
  typeof window !== 'undefined' &&
  Object.prototype.hasOwnProperty.call(window, '__TAURI_INTERNALS__');

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
  const [heavyReflectionMode, setHeavyReflectionMode] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [registryConfig, setRegistryConfig] = useState({ trainer: [], agent: [] });
  const [registryPresets, setRegistryPresets] = useState({ trainer: {}, agent: {} });
  const [savingRegistry, setSavingRegistry] = useState(false);
  const [desktopUpdateStatus, setDesktopUpdateStatus] = useState(null);
  const [checkingDesktopUpdate, setCheckingDesktopUpdate] = useState(false);
  const [installingDesktopUpdate, setInstallingDesktopUpdate] = useState(false);
  const [restartingDesktop, setRestartingDesktop] = useState(false);
  const [desktopUpdateCheckedAt, setDesktopUpdateCheckedAt] = useState(null);

  const loadDesktopUpdateStatus = useCallback(async () => {
    if (!isDesktopRuntime()) {
      setDesktopUpdateStatus(null);
      setDesktopUpdateCheckedAt(null);
      return;
    }
    setCheckingDesktopUpdate(true);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const status = await invoke('desktop_update_status');
      setDesktopUpdateStatus(status);
      setDesktopUpdateCheckedAt(new Date().toISOString());
    } catch (e) {
      console.error('Failed to load desktop updater status', e);
      setDesktopUpdateStatus({
        desktop: true,
        configured: false,
        error: e?.message || 'Failed to load desktop updater status.',
      });
      setDesktopUpdateCheckedAt(new Date().toISOString());
    } finally {
      setCheckingDesktopUpdate(false);
    }
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      const res = await axios.get(`${apiUrl}/admin/settings`);
      if (res.data.status === 'ok') {
        setMaxSyncIters(res.data.settings.max_sync_tool_iterations || 3);
        setAutomaticTaskGeneration(Boolean(res.data.settings.automatic_task_generation));
        setTestingMode(Boolean(res.data.settings.testing_mode));
        setReplayPendingOnStartup(Boolean(res.data.settings.replay_pending_tasks_on_startup));
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
    void loadDesktopUpdateStatus();
  }, [loadDesktopUpdateStatus, loadRegistry, loadRegistryPresets, loadSettings]);

  useEffect(() => {
    if (!isDesktopRuntime()) return undefined;
    const handleFocus = () => {
      void loadDesktopUpdateStatus();
    };
    const intervalId = window.setInterval(() => {
      void loadDesktopUpdateStatus();
    }, 15 * 60 * 1000);
    window.addEventListener('focus', handleFocus);
    document.addEventListener('visibilitychange', handleFocus);
    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener('focus', handleFocus);
      document.removeEventListener('visibilitychange', handleFocus);
    };
  }, [loadDesktopUpdateStatus]);

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

  const handleInstallDesktopUpdate = async () => {
    if (!isDesktopRuntime()) return;
    setInstallingDesktopUpdate(true);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const result = await invoke('desktop_install_update');
      setDesktopUpdateStatus((current) => ({
        ...(current || {}),
        update_available: false,
        installed_version: result?.version || null,
        restart_required: Boolean(result?.installed),
        notes: result?.installed
          ? `Update ${result.version || ''} installed. Restart the desktop app to finish applying it.`.trim()
          : (current?.notes || 'No update was available to install.'),
      }));
    } catch (e) {
      console.error('Failed to install desktop update', e);
      setDesktopUpdateStatus((current) => ({
        ...(current || {}),
        error: e?.message || 'Failed to install desktop update.',
      }));
    } finally {
      setInstallingDesktopUpdate(false);
    }
  };

  const handleRestartDesktop = async () => {
    if (!isDesktopRuntime()) return;
    setRestartingDesktop(true);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('desktop_restart');
    } catch (e) {
      console.error('Failed to restart desktop app', e);
      setDesktopUpdateStatus((current) => ({
        ...(current || {}),
        error: e?.message || 'Failed to restart desktop app.',
      }));
      setRestartingDesktop(false);
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
  const desktopUpdateSummary = desktopUpdateStatus?.restart_required
    ? `Restart ready | update ${desktopUpdateStatus.installed_version || desktopUpdateStatus.latest_version || ''}`.trim()
    : desktopUpdateStatus?.update_available
      ? `Update available: ${desktopUpdateStatus.latest_version || 'new version'}`
      : desktopUpdateStatus?.configured
        ? 'Desktop is current on this channel.'
        : desktopUpdateStatus?.error || 'Desktop updater is not configured yet.';

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

      {isDesktopRuntime() && (
        <DashboardPanel title="DESKTOP UPDATES">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
            <div>
              <div style={sectionLabel}>DESKTOP UPDATES</div>
              <div style={{ fontSize: '13px', color: '#c7c8d6', lineHeight: 1.6 }}>
                {desktopUpdateStatus?.configured
                  ? `Channel ${desktopUpdateStatus.channel || 'alpha'} · current desktop ${desktopUpdateStatus.current_version || 'unknown'}`
                  : 'Desktop updater is present but not configured with a signed channel yet.'}
              </div>
            </div>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              <button
                onClick={() => void loadDesktopUpdateStatus()}
                disabled={checkingDesktopUpdate}
                style={{
                  background: checkingDesktopUpdate ? 'rgba(255,255,255,0.04)' : 'rgba(130,87,229,0.15)',
                  border: '1px solid rgba(130,87,229,0.3)',
                  borderRadius: '8px',
                  padding: '0 14px',
                  height: '36px',
                  color: '#8257e5',
                  fontSize: '12px',
                  fontWeight: 700,
                  cursor: 'pointer',
                }}
              >
                {checkingDesktopUpdate ? 'Checking…' : 'Check for updates'}
              </button>
              <button
                onClick={() => void handleInstallDesktopUpdate()}
                disabled={installingDesktopUpdate || !desktopUpdateStatus?.update_available}
                style={{
                  background: installingDesktopUpdate || !desktopUpdateStatus?.update_available ? 'rgba(255,255,255,0.04)' : 'rgba(0,242,148,0.12)',
                  border: '1px solid rgba(0,242,148,0.25)',
                  borderRadius: '8px',
                  padding: '0 14px',
                  height: '36px',
                  color: desktopUpdateStatus?.update_available ? '#00f294' : '#6d7387',
                  fontSize: '12px',
                  fontWeight: 700,
                  cursor: desktopUpdateStatus?.update_available ? 'pointer' : 'default',
                }}
              >
                {installingDesktopUpdate ? 'Installing…' : 'Install update'}
              </button>
              <button
                onClick={() => void handleRestartDesktop()}
                disabled={restartingDesktop || !desktopUpdateStatus?.restart_required}
                style={{
                  background: restartingDesktop || !desktopUpdateStatus?.restart_required ? 'rgba(255,255,255,0.04)' : 'rgba(255,176,32,0.12)',
                  border: '1px solid rgba(255,176,32,0.25)',
                  borderRadius: '8px',
                  padding: '0 14px',
                  height: '36px',
                  color: desktopUpdateStatus?.restart_required ? '#ffb020' : '#6d7387',
                  fontSize: '12px',
                  fontWeight: 700,
                  cursor: desktopUpdateStatus?.restart_required ? 'pointer' : 'default',
                }}
              >
                {restartingDesktop ? 'Restarting…' : 'Restart app'}
              </button>
            </div>
          </div>
          <div style={{ ...inputGroupStyle, marginTop: '4px' }}>
            <div style={{ fontSize: '12px', color: '#9499ad' }}>
              {desktopUpdateSummary}
            </div>
            {desktopUpdateCheckedAt && (
              <div style={{ fontSize: '11px', color: '#6d7387' }}>
                Last checked: {new Date(desktopUpdateCheckedAt).toLocaleString()}
              </div>
            )}
            {desktopUpdateStatus?.published_at && (
              <div style={{ fontSize: '11px', color: '#6d7387' }}>
                Published: {desktopUpdateStatus.published_at}
              </div>
            )}
            {desktopUpdateStatus?.installed_version && (
              <div style={{ fontSize: '11px', color: '#ffb020' }}>
                Installed and pending restart: {desktopUpdateStatus.installed_version}
              </div>
            )}
            {desktopUpdateStatus?.notes && (
              <div style={{ fontSize: '11px', color: '#c7c8d6', lineHeight: 1.5 }}>
                {desktopUpdateStatus.notes}
              </div>
            )}
            {desktopUpdateStatus?.endpoint && (
              <div style={{ ...infoValue, fontSize: '11px', color: '#8c92a8' }}>
                {desktopUpdateStatus.endpoint}
              </div>
            )}
            {!desktopUpdateStatus?.configured && (
              <div style={{ fontSize: '11px', color: '#6d7387', lineHeight: 1.5 }}>
                Configure `STRATA_DESKTOP_UPDATE_ENDPOINT`, `STRATA_DESKTOP_UPDATE_PUBKEY`, and `TAURI_SIGNING_PRIVATE_KEY`, then run `npm run desktop:build:alpha` to publish a signed alpha build.
              </div>
            )}
          </div>
        </DashboardPanel>
      )}

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
