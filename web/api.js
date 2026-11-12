// Thin fetch wrapper for the Nest API. JSON in/out; errors throw Error(detail)
// with .code (server error code, e.g. job_locked), .status and .requestId.
import { getLang } from './i18n.js';

const BASE = '/api';

// Turn any error body into one plain sentence.
function detailOf(body, status) {
  if (!body) return `Request failed (${status})`;
  const d = body.detail ?? body.message ?? body;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    // FastAPI validation errors: [{loc, msg}, ...]
    return d.map((e) => (e && e.msg ? `${(e.loc || []).slice(-1)[0] ?? ''}: ${e.msg}` : String(e))).join('; ');
  }
  try { return JSON.stringify(d); } catch { return `Request failed (${status})`; }
}

async function request(method, path, { json, form } = {}) {
  const opts = { method, headers: { Accept: 'application/json', 'X-Lang': getLang() } };   // server answers in this language
  if (json !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(json);
  } else if (form !== undefined) {
    opts.body = form; // FormData: browser sets the multipart boundary
  }
  let res;
  try {
    res = await fetch(BASE + path, opts);
  } catch (e) {
    throw apiError('Cannot reach the Nest server. Check the network.', 'network', 0);
  }
  if (res.status === 204) return null;
  const text = await res.text();
  let body = null;
  if (text) {
    try { body = JSON.parse(text); } catch { body = text; }
  }
  if (!res.ok) {
    const code = body && typeof body === 'object' ? body.code : undefined;
    throw apiError(detailOf(body, res.status), code || (res.status >= 500 ? 'server_error' : undefined), res.status,
      body && typeof body === 'object' ? body.request_id : undefined);
  }
  return body;
}

export function apiError(message, code, status, requestId) {
  const e = new Error(message);
  e.code = code;
  e.status = status;
  e.requestId = requestId;
  return e;
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, json) => request('POST', p, { json: json ?? {} }),
  put: (p, json) => request('PUT', p, { json }),
  patch: (p, json) => request('PATCH', p, { json }),
  del: (p) => request('DELETE', p),
  upload: (p, form) => request('POST', p, { form }),
};

// URL helpers for downloads (plain links, not fetch).
export const url = (p) => BASE + p;
