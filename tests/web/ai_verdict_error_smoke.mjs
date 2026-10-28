import assert from 'node:assert/strict';
import { fetchStructured, readResponseSafely, structuredErrorMessage, aiProviderWarning } from '../../seiltanzer/web/js/safe_fetch.js';

assert.equal(aiProviderWarning({ degraded: false }), '');
const timeoutWarning = aiProviderWarning({ degraded: true, request_id: 'ai-timeout',
  provider_error: { code: 'provider_timeout' } });
assert.match(timeoutWarning, /LLM НЕДОСТУПЕН/);
assert.match(timeoutWarning, /provider_timeout/);
assert.match(timeoutWarning, /время ожидания/);
assert.match(timeoutWarning, /ai-timeout/);
assert.doesNotMatch(aiProviderWarning({ degraded: true,
  provider_error: { code: '<secret-provider-body>' } }), /secret-provider-body/);
assert.match(aiProviderWarning({ degraded: true, llm_shadow_decision: { status: 'ok' },
  provider_error: { code: 'provider_timeout' } }), /СТРУКТУРИРОВАННЫЙ LLM ДОСТУПЕН/);
assert.doesNotMatch(aiProviderWarning({ degraded: true,
  llm_shadow_decision: { status: 'ok' } }), /LLM НЕДОСТУПЕН/);

function response(status, contentType, body) {
  return {
    status, ok: status >= 200 && status < 300,
    headers: { get: () => contentType },
    text: async () => body,
  };
}

for (const [status, type, text] of [
  [200, 'application/json', '{"ok":true,"verdict":"HOLD"}'],
  [429, 'application/json', '{"ok":false,"error":{"code":"ai_rate_limited"}}'],
  [502, 'application/json', '{"ok":false,"error":{"code":"provider_unavailable"}}'],
  [500, 'application/json', '{"ok":false,"error":{"code":"ai_internal_error"}}'],
  [500, 'text/plain', 'Internal Server Error'],
]) {
  const parsed = await readResponseSafely(response(status, type, text));
  assert.equal(parsed.status, status);
  assert.ok(!String(parsed.text).includes('Unexpected token'));
  assert.ok(!String(parsed.text).includes('is not valid JSON'));
}

const malformed = await readResponseSafely(response(500, 'application/json', 'Internal Server Error'));
assert.equal(malformed.body, null);
assert.equal(malformed.text, 'Internal Server Error');

const pydantic = structuredErrorMessage([
  { type: 'missing', loc: ['body', 'executed'], msg: 'Field required' },
]);
assert.match(pydantic, /Field required/);
assert.doesNotMatch(pydantic, /\[object Object\]/);
const nested = structuredErrorMessage({ detail: { message: 'Состояние позиции устарело' } });
assert.equal(nested, 'Состояние позиции устарело');

// A second AI-button click while the first provider request is running must
// attach to the same HTTP request. Previously it sent another POST, received the
// server's intentional 429 lock response, and replaced the visible modal with
// "ИИ-РАЗБОР НЕДОСТУПЕН" while the first success rendered off-DOM.
const originalFetch = globalThis.fetch;
let fetchCalls = 0;
let release;
const gate = new Promise((resolve) => { release = resolve; });
globalThis.fetch = async (url, init) => {
  fetchCalls += 1;
  assert.equal(url, '/api/ai/verdict');
  assert.equal(init.method, 'POST');
  await gate;
  return response(200, 'application/json', '{"ok":true,"verdict":"HOLD","request_id":"ai-one"}');
};
try {
  const first = fetchStructured('/api/ai/verdict', { method: 'POST' });
  const second = fetchStructured('/api/ai/verdict', { method: 'POST' });
  assert.equal(fetchCalls, 1, 'concurrent AI verdict clicks must share one POST');
  release();
  const [a, b] = await Promise.all([first, second]);
  assert.equal(a.request_id, 'ai-one');
  assert.equal(b.request_id, 'ai-one');
  assert.equal(fetchCalls, 1);

  await fetchStructured('/api/ai/verdict', { method: 'POST' });
  assert.equal(fetchCalls, 2, 'single-flight state must clear after completion');
} finally {
  globalThis.fetch = originalFetch;
}

console.log('ai verdict defensive parsing + single-flight ok');

//202 must poll a single opaque same-origin job, never start another inference.
const requests = [];
globalThis.fetch = async (url, init) => {
  requests.push([url, init.method]);
  if (requests.length === 1) return response(202, 'application/json', JSON.stringify({
    ok: true, status: 'pending', review_job: {
      job_id: 'a'.repeat(32), poll_url: '/api/ai/reviews/' + 'a'.repeat(32), retry_after_sec: 1,
    }}));
  return response(200, 'application/json', '{"ok":true,"verdict":"COMPLETE","mode":"llm"}');
};
try {
  const [a, b] = await Promise.all([
    fetchStructured('/api/ai/verdict', {method: 'POST'}),
    fetchStructured('/api/ai/verdict', {method: 'POST'}),
  ]);
  assert.equal(a.verdict, 'COMPLETE');
  assert.equal(b.verdict, 'COMPLETE');
  assert.deepEqual(requests, [['/api/ai/verdict', 'POST'], ['/api/ai/reviews/' + 'a'.repeat(32), 'GET']]);
  globalThis.fetch = async () => response(202, 'application/json', JSON.stringify({
    ok: true, status: 'pending', review_job: {poll_url: 'https://untrusted.test/private'},
  }));
  await assert.rejects(fetchStructured('/api/ai/verdict', {method: 'POST'}), /Некорректный адрес/);
} finally { globalThis.fetch = originalFetch; }
