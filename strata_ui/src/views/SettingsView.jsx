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

const compareSemver = (left, right) => {
  const normalize = (value) => String(value || '')
    .trim()
    .split('-')[0]
    .split('+')[0]
    .split('.')
    .map((part) => Number.parseInt(part, 10) || 0);

  const a = normalize(left);
  const b = normalize(right);
  const length = Math.max(a.length, b.length, 3);
  for (let i = 0; i < length; i += 1) {
    const diff = (a[i] || 0) - (b[i] || 0);
    if (diff !== 0) return diff;
  }
  return 0;
};

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
  const [throttleMode, setThrottleMode] = useState('quiet');
  const [autoSwapLocalMissing, setAutoSwapLocalMissing] = useState(true);
  const [savingSettings, setSavingSettings] = useState(false);
  const [registryConfig, setRegistryConfig] = useState({ trainer: [], agent: [] });
  const [registryPresets, setRegistryPresets] = useState({ trainer: {}, agent: {} });
  const [registryCatalog, setRegistryCatalog] = useState({ trainer: { endpoints: [] }, agent: { endpoints: [] } });
  const [savingRegistry, setSavingRegistry] = useState(false);
  const [desktopUpdateStatus, setDesktopUpdateStatus] = useState(null);
  const [channelManifestStatus, setChannelManifestStatus] = useState(null);
  const [checkingDesktopUpdate, setCheckingDesktopUpdate] = useState(false);
  const [installingDesktopUpdate, setInstallingDesktopUpdate] = useState(false);
  const [restartingDesktop, setRestartingDesktop] = useState(false);
  const [desktopUpdateCheckedAt, setDesktopUpdateCheckedAt] = useState(null);
  const [purgeSelection, setPurgeSelection] = useState({
    clear_queue: true,
    clear_loaded_context: true,
  });
  const [purgingEphemera, setPurgingEphemera] = useState(false);

  const getPoolConfig = useCallback((pool) => {
    const raw = registryConfig?.[pool];
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
      return {
        allow_cloud: raw.allow_cloud ?? true,
        allow_local: raw.allow_local ?? true,
        preferred_transport: raw.preferred_transport ?? null,
        endpoints: Array.isArray(raw.endpoints) ? raw.endpoints : [],
      };
    }
    const legacyEndpoints = Array.isArray(raw) ? raw : [];
    return {
      allow_cloud: pool === 'trainer',
      allow_local: pool === 'agent',
      preferred_transport: pool === 'trainer' ? 'cloud' : 'local',
      endpoints: legacyEndpoints,
    };
  }, [registryConfig]);

  const getPoolEndpoint = useCallback((pool, index = 0) => {
    const endpoints = getPoolConfig(pool).endpoints;
    return endpoints[index] || {};
  }, [getPoolConfig]);

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
      if (status?.endpoint) {
        try {
          const manifestResponse = await axios.get(status.endpoint, { timeout: 5000 });
          setChannelManifestStatus({
            ok: true,
            version: manifestResponse?.data?.version || null,
            published_at: manifestResponse?.data?.pub_date || null,
            notes: manifestResponse?.data?.notes || null,
          });
        } catch (manifestError) {
          setChannelManifestStatus({
            ok: false,
            error: manifestError?.message || 'Failed to load channel manifest.',
          });
        }
      } else {
        setChannelManifestStatus(null);
      }
      setDesktopUpdateCheckedAt(new Date().toISOString());
    } catch (e) {
      console.error('Failed to load desktop updater status', e);
      setDesktopUpdateStatus({
        desktop: true,
        configured: false,
        error: e?.message || 'Failed to load desktop updater status.',
      });
      setChannelManifestStatus(null);
      setDesktopUpdateCheckedAt(new Date().toISOString());
    } finally {
      setCheckingDesktopUpdate(false);
    }
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      const res = await axios.get(`${apiUrl}/admin/settings`);
      if (res.data.status === 'ok') {
        const policy = res?.data?.settings?.inference_throttle_policy || {};
        const comfort = policy.operator_comfort || {};
        setMaxSyncIters(res.data.settings.max_sync_tool_iterations || 3);
        setAutomaticTaskGeneration(Boolean(res.data.settings.automatic_task_generation));
        setTestingMode(Boolean(res.data.settings.testing_mode));
        setReplayPendingOnStartup(Boolean(res.data.settings.replay_pending_tasks_on_startup));
        setHeavyReflectionMode(Boolean(res.data.settings.heavy_reflection_mode));
        setThrottleMode(
          String(policy.throttle_mode || 'hard').trim().toLowerCase() === 'greedy'
            || String(comfort.profile || 'quiet').trim().toLowerCase() === 'aggressive'
            ? 'turbo'
            : 'quiet'
        );
        setAutoSwapLocalMissing(Boolean(res?.data?.settings?.model_catalog_policy?.auto_swap_local_missing ?? true));
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

  const loadRegistryCatalog = useCallback(async () => {
    try {
      const res = await axios.get(`${apiUrl}/admin/registry/catalog`);
      if (res?.data?.status === 'ok') {
        setRegistryCatalog(res.data.catalog || { trainer: { endpoints: [] }, agent: { endpoints: [] } });
      }
    } catch (e) {
      console.error('Failed to load registry catalog', e);
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
    void loadRegistryCatalog();
    void loadRegistryPresets();
    void loadDesktopUpdateStatus();
  }, [loadDesktopUpdateStatus, loadRegistry, loadRegistryCatalog, loadRegistryPresets, loadSettings]);

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
    const poolConfig = getPoolConfig(pool);
    const endpoints = [...poolConfig.endpoints];
    if (!endpoints[index]) endpoints[index] = {};
    endpoints[index] = { ...endpoints[index], [field]: value };
    if (!endpoints[index].transport) endpoints[index].transport = pool === 'trainer' ? 'cloud' : 'local';
    if (!endpoints[index].provider) endpoints[index].provider = pool === 'trainer' ? 'openrouter' : 'lmstudio';
    const next = {
      ...registryConfig,
      [pool]: {
        ...poolConfig,
        endpoints,
      },
    };
    setRegistryConfig({ ...next });
    setSavingRegistry(true);
    try {
      await axios.post(`${apiUrl}/admin/registry`, next);
      void loadRegistryCatalog();
    } catch (e) {
      console.error('Failed to save registry', e);
    } finally {
      setSavingRegistry(false);
    }
  };

  const applyPreset = async (pool, presetKey) => {
    const preset = registryPresets?.[pool]?.[presetKey];
    if (!preset) return;
    const next = {
      ...registryConfig,
      [pool]: {
        ...getPoolConfig(pool),
        endpoints: [{ ...preset }],
      },
    };
    setRegistryConfig(next);
    setSavingRegistry(true);
    try {
      await axios.post(`${apiUrl}/admin/registry`, next);
      void loadRegistryCatalog();
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
      model_catalog_policy: {
        auto_swap_local_missing: overrides.auto_swap_local_missing ?? autoSwapLocalMissing,
      },
      ...(overrides.inference_throttle_policy ? { inference_throttle_policy: overrides.inference_throttle_policy } : {}),
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

  const updatePoolSetting = async (pool, field, value) => {
    const poolConfig = getPoolConfig(pool);
    const next = {
      ...registryConfig,
      [pool]: {
        ...poolConfig,
        [field]: value,
      },
    };
    setRegistryConfig(next);
    setSavingRegistry(true);
    try {
      await axios.post(`${apiUrl}/admin/registry`, next);
      void loadRegistryCatalog();
    } catch (e) {
      console.error('Failed to update pool settings', e);
    } finally {
      setSavingRegistry(false);
    }
  };

  const handlePurgeEphemera = async () => {
    setPurgingEphemera(true);
    try {
      if (purgeSelection.clear_queue) {
        await axios.post(`${apiUrl}/admin/worker/clear_queue`);
      }
      if (purgeSelection.clear_loaded_context) {
        await axios.post(`${apiUrl}/admin/context/clear`);
      }
      await Promise.all([loadRegistryCatalog(), loadSettings()]);
    } catch (e) {
      console.error('Failed to purge ephemeral data', e);
    } finally {
      setPurgingEphemera(false);
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
  const effectiveLatestVersion = channelManifestStatus?.version || desktopUpdateStatus?.latest_version;
  const desktopLatestAhead = effectiveLatestVersion
    && desktopUpdateStatus?.current_version
    && compareSemver(effectiveLatestVersion, desktopUpdateStatus.current_version) > 0;
  const desktopUpdateSummary = desktopUpdateStatus?.restart_required
    ? `Restart ready | update ${desktopUpdateStatus.installed_version || desktopUpdateStatus.latest_version || ''}`.trim()
    : desktopUpdateStatus?.update_available
      ? `Update available: ${desktopUpdateStatus.latest_version || 'new version'}`
      : desktopLatestAhead
        ? `Channel latest is ${effectiveLatestVersion}, but the desktop still reports current at ${desktopUpdateStatus.current_version}.`
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
            {(channelManifestStatus?.published_at || desktopUpdateStatus?.published_at) && (
              <div style={{ fontSize: '11px', color: '#6d7387' }}>
                Published: {channelManifestStatus?.published_at || desktopUpdateStatus?.published_at}
              </div>
            )}
            {effectiveLatestVersion && (
              <div style={{ fontSize: '11px', color: desktopLatestAhead ? '#ffb020' : '#6d7387' }}>
                Channel latest: {effectiveLatestVersion}
              </div>
            )}
            {channelManifestStatus?.error && (
              <div style={{ fontSize: '11px', color: '#ffb020', lineHeight: 1.5 }}>
                Channel manifest check failed in the UI: {channelManifestStatus.error}
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
                For local alpha updates, run `npm run desktop:update:setup:local` once, then `npm run desktop:update:publish:local` whenever you want to publish a new signed desktop build to the local channel.
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
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#8257e5', marginBottom: '4px', letterSpacing: '0.05em' }}>STRONG POOL</div>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#cfc3ff', fontSize: '11px' }}>
                  <input type="checkbox" checked={Boolean(getPoolConfig('trainer').allow_cloud)} onChange={(e) => void updatePoolSetting('trainer', 'allow_cloud', e.target.checked)} />
                  Allow cloud
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#cfc3ff', fontSize: '11px' }}>
                  <input type="checkbox" checked={Boolean(getPoolConfig('trainer').allow_local)} onChange={(e) => void updatePoolSetting('trainer', 'allow_local', e.target.checked)} />
                  Allow local
                </label>
                <select value={getPoolConfig('trainer').preferred_transport || ''} onChange={(e) => void updatePoolSetting('trainer', 'preferred_transport', e.target.value || null)} style={{ ...infoValue, maxWidth: '180px' }}>
                  <option value="">Auto transport</option>
                  <option value="cloud">Prefer cloud</option>
                  <option value="local">Prefer local</option>
                </select>
              </div>
              {getPoolEndpoint('trainer').provider && API_KEY_LINKS[getPoolEndpoint('trainer').provider] && (
                <a href={API_KEY_LINKS[getPoolEndpoint('trainer').provider]} target="_blank" rel="noreferrer" style={{ fontSize: '11px', color: '#bca9ff', textDecoration: 'none' }}>
                  Open {getPoolEndpoint('trainer').provider} API key page
                </a>
              )}
              <input style={infoValue} placeholder="Provider (e.g. google, openrouter, lmstudio)" value={getPoolEndpoint('trainer').provider || ''} onChange={(e) => handleUpdateRegistry('trainer', 'provider', e.target.value)} />
              <select style={infoValue} value={getPoolEndpoint('trainer').transport || 'cloud'} onChange={(e) => handleUpdateRegistry('trainer', 'transport', e.target.value)}>
                <option value="cloud">Cloud</option>
                <option value="local">Local</option>
              </select>
              <input style={infoValue} placeholder="Model (e.g. anthropic/claude-3.5-sonnet)" value={getPoolEndpoint('trainer').model || ''} onChange={(e) => handleUpdateRegistry('trainer', 'model', e.target.value)} />
              <input style={infoValue} placeholder="Endpoint URL (e.g. https://openrouter.ai/api/v1/chat/completions)" value={getPoolEndpoint('trainer').endpoint_url || ''} onChange={(e) => handleUpdateRegistry('trainer', 'endpoint_url', e.target.value)} />
              <input style={infoValue} placeholder="API Key Env (e.g. OPENROUTER_API_KEY)" value={getPoolEndpoint('trainer').api_key_env || ''} onChange={(e) => handleUpdateRegistry('trainer', 'api_key_env', e.target.value)} />
              <input type="number" style={infoValue} placeholder="Requests / minute (optional)" value={getPoolEndpoint('trainer').requests_per_minute || ''} onChange={(e) => handleUpdateRegistry('trainer', 'requests_per_minute', e.target.value ? parseInt(e.target.value, 10) : null)} />
              <input type="number" style={infoValue} placeholder="Max concurrency (optional)" value={getPoolEndpoint('trainer').max_concurrency || ''} onChange={(e) => handleUpdateRegistry('trainer', 'max_concurrency', e.target.value ? parseInt(e.target.value, 10) : null)} />
              <input type="number" style={infoValue} placeholder="Min interval ms (optional)" value={getPoolEndpoint('trainer').min_interval_ms || ''} onChange={(e) => handleUpdateRegistry('trainer', 'min_interval_ms', e.target.value ? parseInt(e.target.value, 10) : null)} />
              {Array.isArray(registryCatalog.trainer?.endpoints) && registryCatalog.trainer.endpoints[0]?.transport === 'local' ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  <div style={{ fontSize: '11px', color: '#8d8ea1' }}>
                    Local catalog: {registryCatalog.trainer.endpoints[0]?.status === 'ok' ? `${(registryCatalog.trainer.endpoints[0]?.models || []).length} models detected` : (registryCatalog.trainer.endpoints[0]?.error || registryCatalog.trainer.endpoints[0]?.status || 'unknown')}
                  </div>
                  {registryCatalog.trainer.endpoints[0]?.status === 'ok' && registryCatalog.trainer.endpoints[0]?.configured_model_present === false && autoSwapLocalMissing ? (
                    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                      {(registryCatalog.trainer.endpoints[0]?.models || []).slice(0, 4).map((modelId) => (
                        <button key={modelId} type="button" onClick={() => handleUpdateRegistry('trainer', 'model', modelId)} style={{ background: 'rgba(214,173,113,0.14)', border: '1px solid rgba(214,173,113,0.24)', borderRadius: '999px', color: '#f3ddbf', padding: '6px 10px', fontSize: '11px', cursor: 'pointer' }}>
                          Switch to {modelId}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          )}
          {visiblePools.includes('agent') && (
            <div style={inputGroupStyle}>
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#00d9ff', marginBottom: '4px', letterSpacing: '0.05em' }}>WEAK POOL</div>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#9fefff', fontSize: '11px' }}>
                  <input type="checkbox" checked={Boolean(getPoolConfig('agent').allow_cloud)} onChange={(e) => void updatePoolSetting('agent', 'allow_cloud', e.target.checked)} />
                  Allow cloud
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#9fefff', fontSize: '11px' }}>
                  <input type="checkbox" checked={Boolean(getPoolConfig('agent').allow_local)} onChange={(e) => void updatePoolSetting('agent', 'allow_local', e.target.checked)} />
                  Allow local
                </label>
                <select value={getPoolConfig('agent').preferred_transport || ''} onChange={(e) => void updatePoolSetting('agent', 'preferred_transport', e.target.value || null)} style={{ ...infoValue, maxWidth: '180px' }}>
                  <option value="">Auto transport</option>
                  <option value="local">Prefer local</option>
                  <option value="cloud">Prefer cloud</option>
                </select>
              </div>
              <input style={infoValue} placeholder="Provider (e.g. lmstudio, ollama, openrouter)" value={getPoolEndpoint('agent').provider || ''} onChange={(e) => handleUpdateRegistry('agent', 'provider', e.target.value)} />
              <select style={infoValue} value={getPoolEndpoint('agent').transport || 'local'} onChange={(e) => handleUpdateRegistry('agent', 'transport', e.target.value)}>
                <option value="local">Local</option>
                <option value="cloud">Cloud</option>
              </select>
              <input style={infoValue} placeholder="Model (e.g. qwen3.5-9b-distilled)" value={getPoolEndpoint('agent').model || ''} onChange={(e) => handleUpdateRegistry('agent', 'model', e.target.value)} />
              <input style={infoValue} placeholder="Endpoint URL (e.g. http://127.0.0.1:1234/v1/chat/completions)" value={getPoolEndpoint('agent').endpoint_url || ''} onChange={(e) => handleUpdateRegistry('agent', 'endpoint_url', e.target.value)} />
              <input type="number" style={infoValue} placeholder="Requests / minute (optional)" value={getPoolEndpoint('agent').requests_per_minute || ''} onChange={(e) => handleUpdateRegistry('agent', 'requests_per_minute', e.target.value ? parseInt(e.target.value, 10) : null)} />
              <input type="number" style={infoValue} placeholder="Max concurrency (optional)" value={getPoolEndpoint('agent').max_concurrency || ''} onChange={(e) => handleUpdateRegistry('agent', 'max_concurrency', e.target.value ? parseInt(e.target.value, 10) : null)} />
              <input type="number" style={infoValue} placeholder="Min interval ms (optional)" value={getPoolEndpoint('agent').min_interval_ms || ''} onChange={(e) => handleUpdateRegistry('agent', 'min_interval_ms', e.target.value ? parseInt(e.target.value, 10) : null)} />
              {Array.isArray(registryCatalog.agent?.endpoints) && registryCatalog.agent.endpoints[0]?.transport === 'local' ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  <div style={{ fontSize: '11px', color: '#8d8ea1' }}>
                    Local catalog: {registryCatalog.agent.endpoints[0]?.status === 'ok' ? `${(registryCatalog.agent.endpoints[0]?.models || []).length} models detected` : (registryCatalog.agent.endpoints[0]?.error || registryCatalog.agent.endpoints[0]?.status || 'unknown')}
                  </div>
                  {registryCatalog.agent.endpoints[0]?.status === 'ok' && registryCatalog.agent.endpoints[0]?.configured_model_present === false && autoSwapLocalMissing ? (
                    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                      {(registryCatalog.agent.endpoints[0]?.models || []).slice(0, 4).map((modelId) => (
                        <button key={modelId} type="button" onClick={() => handleUpdateRegistry('agent', 'model', modelId)} style={{ background: 'rgba(0,217,255,0.12)', border: '1px solid rgba(0,217,255,0.24)', borderRadius: '999px', color: '#b9f8ff', padding: '6px 10px', fontSize: '11px', cursor: 'pointer' }}>
                          Switch to {modelId}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
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
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '16px' }}>
          <div style={{ fontSize: '13px', color: '#ccc' }}>Throttle mode</div>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {[
              {
                id: 'quiet',
                label: 'Quiet',
                helper: 'Conservative local pacing and lower operator annoyance.',
                payload: {
                  inference_throttle_policy: {
                    throttle_mode: 'hard',
                    operator_comfort: {
                      profile: 'quiet',
                      ambiguity_bias: 'prefer_quiet',
                      allow_annoying_if_explicit: false,
                    },
                  },
                },
              },
              {
                id: 'turbo',
                label: 'Turbo',
                helper: 'Greedier throughput and faster local turn-taking.',
                payload: {
                  inference_throttle_policy: {
                    throttle_mode: 'greedy',
                    operator_comfort: {
                      profile: 'aggressive',
                      ambiguity_bias: 'prefer_action',
                      allow_annoying_if_explicit: true,
                    },
                  },
                },
              },
            ].map((mode) => {
              const active = throttleMode === mode.id;
              return (
                <button
                  key={mode.id}
                  type="button"
                  onClick={() => {
                    setThrottleMode(mode.id);
                    void persistSettings(mode.payload);
                  }}
                  style={{
                    background: active ? 'rgba(214,173,113,0.14)' : 'rgba(255,255,255,0.04)',
                    border: active ? '1px solid rgba(214,173,113,0.28)' : '1px solid rgba(255,255,255,0.08)',
                    borderRadius: '12px',
                    padding: '10px 12px',
                    color: active ? '#f1ddbf' : '#d5d7e4',
                    textAlign: 'left',
                    cursor: 'pointer',
                    minWidth: '220px',
                  }}
                >
                  <div style={{ fontSize: '12px', fontWeight: 700, marginBottom: '4px' }}>{mode.label}</div>
                  <div style={{ fontSize: '11px', color: active ? '#d8c7a5' : '#7d8296', lineHeight: 1.5 }}>{mode.helper}</div>
                </button>
              );
            })}
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginTop: '16px' }}>
          {[
            ['Automatically generate tasks', automaticTaskGeneration, setAutomaticTaskGeneration, 'automatic_task_generation', 'Lets the chat model spawn background research and implementation tasks on its own. Default is off for quieter testing.'],
            ['Testing mode', testingMode, setTestingMode, 'testing_mode', 'Suppresses autonomous idle task generation so you can run focused evaluations without extra noise.'],
            ['Replay pending backlog on startup', replayPendingOnStartup, setReplayPendingOnStartup, 'replay_pending_tasks_on_startup', 'Re-enqueues old pending tasks after a reboot. Leave this off unless you intentionally want to resume backlog work.'],
            ['Heavy reflection mode', heavyReflectionMode, setHeavyReflectionMode, 'heavy_reflection_mode', 'Makes the trainer lane seed larger bootstrap supervision batches when idle so overnight runs synthesize telemetry faster.'],
            ['Auto-swap missing local models', autoSwapLocalMissing, setAutoSwapLocalMissing, 'auto_swap_local_missing', 'When a configured local model is missing from the endpoint catalog, surface quick-switch options to available local models.'],
          ].map(([label, checked, setter, field, helper]) => (
            <label key={field} style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', color: '#ccc', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={checked}
                onChange={(e) => {
                  const next = e.target.checked;
                  setter(next);
                  void persistSettings(field === 'auto_swap_local_missing' ? { auto_swap_local_missing: next } : { [field]: next });
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
        <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '12px', padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px', marginBottom: '14px' }}>
          <div>
            <div style={{ fontSize: '14px', fontWeight: 600, color: '#edeeef', marginBottom: '4px' }}>Purge Ephemeral State</div>
            <div style={{ fontSize: '12px', color: '#888' }}>Recommended defaults clear runtime churn while preserving durable knowledge, Procedures, and settings.</div>
          </div>
          <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', color: '#ccc', fontSize: '13px', cursor: 'pointer' }}>
            <input type="checkbox" checked={purgeSelection.clear_queue} onChange={(e) => setPurgeSelection((prev) => ({ ...prev, clear_queue: e.target.checked }))} />
            <span>Clear queued runtime work<div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>Best when the worker is stuck in old recovery churn.</div></span>
          </label>
          <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', color: '#ccc', fontSize: '13px', cursor: 'pointer' }}>
            <input type="checkbox" checked={purgeSelection.clear_loaded_context} onChange={(e) => setPurgeSelection((prev) => ({ ...prev, clear_loaded_context: e.target.checked }))} />
            <span>Clear pinned persistent context<div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>Useful when stale context keeps steering the model into old branches.</div></span>
          </label>
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button
              type="button"
              onClick={handlePurgeEphemera}
              disabled={purgingEphemera || (!purgeSelection.clear_queue && !purgeSelection.clear_loaded_context)}
              style={{ background: 'rgba(214,173,113,0.14)', border: '1px solid rgba(214,173,113,0.28)', borderRadius: '8px', padding: '8px 16px', color: '#f3ddbf', fontSize: '12px', fontWeight: 700, cursor: 'pointer' }}
            >
              {purgingEphemera ? 'Purging…' : 'Purge Selected'}
            </button>
          </div>
        </div>
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
