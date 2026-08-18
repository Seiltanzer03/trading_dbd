// Defensive response parsing for endpoints that must remain useful through
// proxy errors, text/plain 5xx pages and malformed upstream JSON.

function compactText(value, limit = 180) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
}

export function structuredErrorMessage(value) {
  if (value == null) return '';
  if (typeof value === 'string') return compactText(value);
  if (Array.isArray(value)) {
    const parts = value.map((item) => structuredErrorMessage(item)).filter(Boolean);
    return compactText(parts.join('; '));
  }
  if (typeof value === 'object') {
    const direct = value.message || value.msg || value.detail || value.error;
    if (direct != null && direct !== value) {
      const parsed = structuredErrorMessage(direct);
      if (parsed) return parsed;
    }
    const loc = Array.isArray(value.loc) ? value.loc.join('.') : '';
    const type = typeof value.type === 'string' ? value.type : '';
    if (loc || type) return compactText([loc, type].filter(Boolean).join(' · '));
    try { return compactText(JSON.stringify(value)); } catch (_) { return 'Некорректный ответ сервера'; }
  }
  return compactText(value);
}

export async function readResponseSafely(response) {
  const status = Number(response?.status) || 0;
  const contentType = String(response?.headers?.get?.('content-type') || '').toLowerCase();
  let text = '';
  try { text = await response.text(); } catch (_) { /* preserve status */ }
  let body = null;
  if (text && (contentType.includes('json') || /^[\s]*[{[]/.test(text))) {
    try { body = JSON.parse(text); } catch (_) { body = null; }
  }
  return { ok: Boolean(response?.ok), status, contentType, body, text: compactText(text) };
}

let aiVerdictInFlight = null;

async function fetchStructuredOnce(url, init = {}) {
  const response = await fetch(url, init);
  const parsed = await readResponseSafely(response);
  if (!parsed.ok) {
    const detail = structuredErrorMessage(parsed.body?.error?.message)
      || structuredErrorMessage(parsed.body?.detail)
      || structuredErrorMessage(parsed.body?.error)
      || parsed.text || `HTTP ${parsed.status}`;
    const error = new Error(detail);
    error.status = parsed.status;
    error.requestId = parsed.body?.error?.request_id || parsed.body?.request_id || null;
    error.code = parsed.body?.error?.code || 'http_error';
    throw error;
  }
  if (!parsed.body || typeof parsed.body !== 'object') {
    const error = new Error(`HTTP ${parsed.status} · некорректный ответ сервера`);
    error.status = parsed.status;
    throw error;
  }
  return parsed.body;
}

export async function fetchStructured(url, init = {}) {
  // /api/ai/verdict is intentionally serialized on the server. Re-clicking the
  // AI button while the first request is still running used to replace the
  // visible modal, send a second POST, and display the server's correct HTTP 429
  // while the successful first response rendered into a detached DOM node.
  // Share the same promise instead: every open/re-open of the modal receives the
  // one authoritative frozen snapshot result and no duplicate provider request
  // is generated.
  const method = String(init?.method || 'GET').toUpperCase();
  if (url === '/api/ai/verdict' && method === 'POST') {
    if (aiVerdictInFlight) return aiVerdictInFlight;
    aiVerdictInFlight = fetchStructuredOnce(url, init);
    try {
      return await aiVerdictInFlight;
    } finally {
      aiVerdictInFlight = null;
    }
  }
  return fetchStructuredOnce(url, init);
}
