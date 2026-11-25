// File timeline API client (own fetch helpers; same-origin cookie auth like ../api.js).
import { ApiError } from '../api.js';

async function request(method, path, body) {
  const init = { method, headers: {}, credentials: 'same-origin' };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, { error: 'offline', message: 'Cannot reach the Reframe server.' });
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

const enc = encodeURIComponent;

export const filesApi = {
  status: () => request('GET', '/api/files/status'),
  options: () => request('GET', '/api/files/options'),
  putOptions: (b) => request('PUT', '/api/files/options', b),
  suggestions: () => request('GET', '/api/files/suggested-exclusions'),
  rescan: () => request('POST', '/api/files/rescan', {}),
  cancel: () => request('POST', '/api/files/cancel', {}),
  entries: (mode) => request('GET', `/api/files/entries?mode=${enc(mode)}`),
  entry: (id) => request('GET', `/api/files/entry/${enc(id)}`),
  open: (id) => request('POST', `/api/files/open/${enc(id)}`, {}),
  reveal: (id) => request('POST', `/api/files/reveal/${enc(id)}`, {}),
};

/**
 * One thumbnail: {status: 'ok', blob} | {status: 'placeholder', reason} | {status: 'retry'} (busy / cancelled).
 * Never accepts a path: only an opaque id of the current index (R-154).
 */
export async function fetchThumb(id, gen, signal) {
  const res = await fetch(`/api/files/thumb/${enc(id)}?g=${gen}`, { credentials: 'same-origin', signal });
  const type = res.headers.get('Content-Type') || '';
  if (res.ok && type.startsWith('image/')) return { status: 'ok', blob: await res.blob() };
  let data = null;
  try { data = await res.json(); } catch { /* ignore */ }
  if (res.status === 404) return { status: 'placeholder', reason: 'not available' };
  if (res.ok && data && data.retry) return { status: 'retry' };
  return { status: 'placeholder', reason: (data && data.placeholder) || 'unavailable' };
}

export async function fetchIcon(extension, _gen, signal) {
  let res;
  try {
    res = await fetch(`/api/files/icon?ext=${enc(extension)}`, { credentials: 'same-origin', signal });
  } catch (e) {
    if (e.name !== 'AbortError') console.warn('Could not load a Windows file icon:', e);
    throw e;
  }
  if (res.ok && (res.headers.get('Content-Type') || '').startsWith('image/')) {
    return { status: 'ok', blob: await res.blob() };
  }
  const data = await res.json();
  if (!res.ok) console.warn('Could not load a Windows file icon:', data.message);
  return { status: 'placeholder', reason: data.placeholder || data.message || 'Windows icon unavailable' };
}
