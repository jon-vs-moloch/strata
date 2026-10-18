/**
 * Live-mode API auth: attach the API's per-launch token to every write.
 *
 * The API refuses POST/PUT/PATCH/DELETE without an `X-Strata-Token` header
 * (see strata/api/local_guard.py). The token is random per API start, so the
 * workbench fetches it from GET /admin/session-token, which the API answers
 * only to the workbench's own origins. If the API restarts, the old token gets
 * a 401; the request is retried once with a fresh token.
 *
 * Installed only in live mode. Demo mode never talks to the API.
 */
import axios from 'axios';
import { API_BASE } from './runtime';

const TOKEN_HEADER = 'X-Strata-Token';
const SESSION_TOKEN_URL = `${API_BASE}/admin/session-token`;
const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

let tokenPromise = null;

const fetchToken = (realFetch) => {
  if (!tokenPromise) {
    tokenPromise = realFetch(SESSION_TOKEN_URL, { cache: 'no-store' })
      .then((resp) => {
        if (!resp.ok) throw new Error(`session token request failed (${resp.status})`);
        return resp.json();
      })
      .then((body) => String(body.token || ''))
      .catch((err) => {
        tokenPromise = null; // let the next write try again
        throw err;
      });
  }
  return tokenPromise;
};

const forgetToken = () => {
  tokenPromise = null;
};

const isApiUrl = (url) => {
  const value = String(url || '');
  return value === API_BASE || value.startsWith(`${API_BASE}/`);
};

const isStaleToken = (status, body) => status === 401 && body && body.code === 'strata_token_required';

export const installApiAuth = () => {
  const realFetch = window.fetch.bind(window);

  window.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input?.url;
    const method = String(init.method || (typeof input === 'object' && input?.method) || 'GET').toUpperCase();
    if (!isApiUrl(url) || !WRITE_METHODS.has(method)) return realFetch(input, init);

    const send = async () => {
      const headers = new Headers(init.headers || (typeof input === 'object' ? input.headers : undefined));
      headers.set(TOKEN_HEADER, await fetchToken(realFetch));
      return realFetch(input, { ...init, headers });
    };
    const resp = await send();
    if (resp.status !== 401) return resp;
    const body = await resp.clone().json().catch(() => null);
    if (!isStaleToken(resp.status, body)) return resp;
    forgetToken();
    return send();
  };

  axios.interceptors.request.use(async (config) => {
    const method = String(config.method || 'get').toUpperCase();
    if (isApiUrl(config.url) && WRITE_METHODS.has(method)) {
      config.headers = config.headers || {};
      config.headers[TOKEN_HEADER] = await fetchToken(realFetch);
    }
    return config;
  });

  axios.interceptors.response.use(undefined, async (error) => {
    const { config, response } = error || {};
    if (config && !config.__strataTokenRetried && response && isStaleToken(response.status, response.data)) {
      forgetToken();
      config.__strataTokenRetried = true;
      return axios(config);
    }
    throw error;
  });
};
