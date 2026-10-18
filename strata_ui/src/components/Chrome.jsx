/**
 * App chrome shared across views: the Strata mark, the live/demo mode switch,
 * the theme toggle, the demo-notice toast and the "API offline" banner.
 */
import React, { useEffect, useState } from 'react';
import { Monitor, Moon, Sun, X } from 'lucide-react';
import { API_BASE, getThemePreference, setDemoMode, setThemePreference, watchSystemTheme } from '../lib/runtime';
import { DEMO_NOTICE_EVENT } from '../demo/events';

/** Three offset strata bands: the system (indigo) over the agent (teal) over the trainer (copper). */
export const StrataMark = ({ size = 28 }) => (
  <svg width={size} height={size} viewBox="0 0 28 28" role="img" aria-label="Strata">
    <rect x="0.5" y="0.5" width="27" height="27" rx="8" fill="var(--surface-2)" stroke="var(--line-strong)" />
    <rect x="6" y="7.5" width="16" height="3.2" rx="1.6" fill="var(--accent)" />
    <rect x="4.5" y="12.4" width="16" height="3.2" rx="1.6" fill="var(--agent)" />
    <rect x="7.5" y="17.3" width="14" height="3.2" rx="1.6" fill="var(--trainer)" />
  </svg>
);

export const ModeSwitch = ({ demoMode, apiStatus }) => {
  const live = !demoMode;
  const healthy = apiStatus === 'ok';
  const label = demoMode ? 'Demo data' : healthy ? 'Live API' : apiStatus === 'connecting' ? 'Connecting' : 'API offline';
  return (
    <div className={`chrome-mode ${demoMode ? 'is-demo' : healthy ? 'is-live' : 'is-down'}`}>
      <span className="chrome-mode-dot" aria-hidden="true" />
      <span className="chrome-mode-label" title={demoMode ? 'Recorded sample data. Nothing is sent to a backend.' : `Talking to ${API_BASE}`}>{label}</span>
      <button
        type="button"
        className="chrome-mode-switch"
        onClick={() => setDemoMode(live)}
        title={live ? 'Explore recorded sample data without a backend' : 'Connect to the Strata API'}
      >
        {live ? 'View demo' : 'Go live'}
      </button>
    </div>
  );
};

const THEME_CYCLE = ['system', 'light', 'dark'];
const THEME_ICON = { system: Monitor, light: Sun, dark: Moon };

export const ThemeToggle = () => {
  const [preference, setPreference] = useState(getThemePreference);
  useEffect(() => watchSystemTheme(() => {}), []);
  const Icon = THEME_ICON[preference];
  const next = THEME_CYCLE[(THEME_CYCLE.indexOf(preference) + 1) % THEME_CYCLE.length];
  return (
    <button
      type="button"
      className="chrome-icon-btn"
      onClick={() => {
        setThemePreference(next);
        setPreference(next);
      }}
      title={`Theme: ${preference}. Switch to ${next}.`}
      aria-label={`Theme: ${preference}. Switch to ${next}.`}
    >
      <Icon size={15} />
    </button>
  );
};

export const DemoNoticeToast = () => {
  const [notice, setNotice] = useState('');
  useEffect(() => {
    let timer = null;
    const onNotice = (event) => {
      setNotice(String(event.detail || ''));
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setNotice(''), 4200);
    };
    window.addEventListener(DEMO_NOTICE_EVENT, onNotice);
    return () => {
      window.removeEventListener(DEMO_NOTICE_EVENT, onNotice);
      window.clearTimeout(timer);
    };
  }, []);
  if (!notice) return null;
  return (
    <div className="chrome-toast" role="status">
      <span>{notice}</span>
      <button type="button" className="chrome-icon-btn" onClick={() => setNotice('')} aria-label="Dismiss">
        <X size={13} />
      </button>
    </div>
  );
};

export const ApiOfflineBanner = ({ apiStatus, demoMode }) => {
  const [dismissed, setDismissed] = useState(false);
  if (demoMode || apiStatus !== 'error' || dismissed) return null;
  return (
    <div className="chrome-banner" role="status">
      <div>
        <strong>Can't reach the Strata API at <code>{API_BASE}</code>.</strong>{' '}
        Start the backend (see the README), or explore the workbench with recorded sample data.
      </div>
      <div className="chrome-banner-actions">
        <button type="button" className="chrome-btn is-primary" onClick={() => setDemoMode(true)}>View demo data</button>
        <button type="button" className="chrome-btn" onClick={() => setDismissed(true)}>Dismiss</button>
      </div>
    </div>
  );
};
