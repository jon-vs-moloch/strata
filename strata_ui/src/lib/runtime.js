/**
 * Runtime configuration shared by the whole workbench: where the API lives,
 * whether the page is in demo mode, and the colour theme.
 *
 * Demo mode replays recorded sample data instead of talking to the API. It is on
 * when any of these hold:
 *   - the URL has ?demo (or ?demo=1); ?demo=0 turns it off,
 *   - the app was started with `npm run demo` (VITE_STRATA_DEMO=1),
 *   - the viewer chose "View demo data" earlier (remembered per browser).
 */

const env = import.meta.env || {};

export const API_BASE = String(env.VITE_STRATA_API || 'http://127.0.0.1:8000').replace(/\/+$/, '');

const DEMO_KEY = 'strata.demo';
const THEME_KEY = 'strata.theme';

const safeGet = (key) => {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
};

const safeSet = (key, value) => {
  try {
    if (value == null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: preference just isn't remembered */
  }
};

const resolveDemoMode = () => {
  if (typeof window === 'undefined') return false;
  const params = new URLSearchParams(window.location.search);
  if (params.has('demo')) {
    const value = params.get('demo');
    const on = value !== '0' && value !== 'false';
    safeSet(DEMO_KEY, on ? '1' : null);
    return on;
  }
  if (String(env.VITE_STRATA_DEMO || '') === '1') return true;
  return safeGet(DEMO_KEY) === '1';
};

export const DEMO_MODE = resolveDemoMode();

/** Switch between demo and live mode. Reloads so every request goes through one transport. */
export const setDemoMode = (on) => {
  safeSet(DEMO_KEY, on ? '1' : null);
  const url = new URL(window.location.href);
  url.searchParams.set('demo', on ? '1' : '0');
  window.location.assign(url.toString());
};

/* Theme: 'system' | 'light' | 'dark'. index.html applies it before first paint. */

export const getThemePreference = () => {
  const stored = safeGet(THEME_KEY);
  return stored === 'light' || stored === 'dark' ? stored : 'system';
};

const systemTheme = () =>
  window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';

export const applyTheme = (preference = getThemePreference()) => {
  const resolved = preference === 'system' ? systemTheme() : preference;
  document.documentElement.setAttribute('data-theme', resolved);
  return resolved;
};

export const setThemePreference = (preference) => {
  safeSet(THEME_KEY, preference === 'system' ? null : preference);
  return applyTheme(preference);
};

export const watchSystemTheme = (onChange) => {
  if (!window.matchMedia) return () => {};
  const query = window.matchMedia('(prefers-color-scheme: dark)');
  const handler = () => {
    if (getThemePreference() === 'system') onChange(applyTheme('system'));
  };
  query.addEventListener('change', handler);
  return () => query.removeEventListener('change', handler);
};
