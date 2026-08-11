import assert from 'node:assert/strict';
import { readResponseSafely, structuredErrorMessage } from '../../seiltanzer/web/js/safe_fetch.js';

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

console.log('ai verdict defensive parsing ok');
