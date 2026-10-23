import json

import pytest

from seiltanzer.ai_api import provider_error
from seiltanzer.ai_request_trace import AIRequestTrace


@pytest.mark.parametrize('reason', [
    'combined_provider_missing_shadow', 'combined_provider_missing_policy_scores',
    'combined_provider_invalid_family_preferences', 'combined_provider_hard_integrity_failure',
    'shadow_invalid_policy', 'shadow_invalid_confidence', 'shadow_missing_reason',
    'shadow_invalid_policy_scores',
    'shadow_invalid_payload', 'shadow_invalid_json',
])
def test_rejected_structured_response_is_not_a_provider_outage(reason, caplog):
    error = RuntimeError(reason)
    assert provider_error(error) == {
        'code': 'provider_bad_response', 'retriable': True, 'reason': reason}
    AIRequestTrace().error('provider_fallback', error)
    row = json.loads(caplog.records[-1].message)
    assert row['provider_reason'] == reason


def test_failure_reason_never_logs_arbitrary_provider_text(caplog):
    reason = 'shadow_invalid_policy private-provider-body credential'
    assert provider_error(RuntimeError(reason)) == {'code': 'provider_unavailable', 'retriable': True}
    AIRequestTrace().error('provider_fallback', RuntimeError(reason))
    assert 'provider_reason' not in json.loads(caplog.records[-1].message)
    assert 'private-provider-body' not in caplog.text


def test_api_retains_structured_payload_rejection_reason(tmp_path, monkeypatch):
    from test_ai_verdict_api import _client

    def rejected(_snapshot):
        raise RuntimeError('shadow_invalid_payload')

    app, client = _client(tmp_path, monkeypatch, rejected)
    try:
        response = client.post('/api/ai/verdict')
        assert response.status_code == 200
        assert response.json()['provider_error'] == {
            'code': 'provider_bad_response', 'retriable': True, 'reason': 'shadow_invalid_payload'}
    finally:
        app.state.engine.close()
