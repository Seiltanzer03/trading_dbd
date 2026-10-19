import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from seiltanzer import ai_provider_guard as guard
from seiltanzer import ai_request_trace as tracing
from seiltanzer import ai_runtime_report_v20 as report
from seiltanzer.llm_decision_shadow import VALID_POLICIES
from test_ai_provider_explanation import _snapshot


def transport(monkeypatch, *, entered=None, release=None, completed=None):
    calls = []
    content = json.dumps({'shadow_decision': {
        'policy': 'HOLD', 'confidence': .7,
        'policy_scores': {name: float(name == 'HOLD') for name in VALID_POLICIES},
        'reason_ru': 'private model rationale must not enter telemetry',
        'key_evidence': [], 'counter_evidence': [], 'evidence_families': [],
        'invalidation_conditions': [], 'family_assessments': {}}})
    payload = {'model': 'private-model-id',
        'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 321, 'completion_tokens': 123, 'total_tokens': 444}}

    class Response:
        content = json.dumps(payload).encode()

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    class Client:
        def __init__(self, **kwargs):
            assert kwargs['timeout'] == 8

        def __enter__(self):
            return self

        def __exit__(self, *args):
            if completed is not None:
                completed.set()

        def post(self, url, **kwargs):
            calls.append(kwargs['json'])
            if entered is not None:
                entered.set()
            if release is not None:
                assert release.wait(2)
            return Response()

    monkeypatch.setenv('OPENROUTER_API_KEY', 'private-test-credential')
    monkeypatch.setattr(report.httpx, 'Client', Client)
    monkeypatch.setattr(report.ai_verdict, 'render_policy_report', lambda _: 'private deterministic report')
    monkeypatch.setattr(report.ai_verdict, '_validate_model_report', lambda *args: [])
    return calls, len(Response.content), len(content.encode())


def telemetry(caplog, request_id):
    return [json.loads(record.message) for record in caplog.records
        if record.name == tracing.LOGGER.name
        and json.loads(record.message).get('request_id') == request_id]


def test_actual_provider_phases_and_exact_byte_counts_have_no_private_payload(monkeypatch, caplog):
    calls, response_bytes, output_bytes = transport(monkeypatch)
    caplog.set_level(logging.WARNING, logger=tracing.LOGGER.name)
    trace = tracing.AIRequestTrace()
    snapshot = _snapshot()
    with trace.activate():
        result = report.request_explanation_with_shadow(snapshot, authoritative_snapshot=snapshot)
    rows = telemetry(caplog, trace.request_id)
    ended = {row['stage'] for row in rows if row['event'] == 'end'}
    assert {'provider_render', 'provider_prepare', 'provider_http', 'provider_decode',
            'provider_validate'} <= ended
    assert all(row['duration_ms'] >= 0 for row in rows if row['event'] == 'end')
    counts = {key: value for row in rows if row['event'] == 'metrics'
              for key, value in row.items()}
    body = calls[0]
    assert counts['input_bytes'] == len(body['messages'][1]['content'].split('\n', 1)[1].encode())
    assert counts['request_bytes'] == len(json.dumps(body, ensure_ascii=False,
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    assert counts['response_bytes'] == response_bytes
    assert counts['output_bytes'] == output_bytes
    assert counts['max_output_tokens'] == body['max_tokens'] == 2400
    assert (counts['prompt_tokens'], counts['completion_tokens'], counts['total_tokens']) == (321, 123, 444)
    assert counts['finish_reason'] == 'stop'
    assert set(result['llm_shadow_decision']['policy_scores']) == set(VALID_POLICIES)
    logged = json.dumps(rows)
    for private in ('private-test-credential', 'private-model-id', 'private model rationale',
                    'private deterministic report', 'openrouter', 'Authorization'):
        assert private not in logged


def test_timeout_retains_http_start_and_request_size_with_the_original_request_id(monkeypatch, caplog):
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    transport(monkeypatch, entered=entered, release=release, completed=completed)
    caplog.set_level(logging.WARNING, logger=tracing.LOGGER.name)
    trace = tracing.AIRequestTrace()
    executor = ThreadPoolExecutor(max_workers=1)
    snapshot = _snapshot()
    try:
        with trace.activate(), pytest.raises(RuntimeError, match='provider_timeout_after'):
            guard.bounded_provider_call(
                lambda value: report.request_explanation_with_shadow(value, authoritative_snapshot=value),
                snapshot, executor=executor, timeout_sec=.1)
        assert entered.is_set()
        rows = telemetry(caplog, trace.request_id)
        assert any(row['stage'] == 'provider_http' and row['event'] == 'start' for row in rows)
        assert any(row.get('request_bytes', 0) > 0 for row in rows)
        assert not any(row['stage'] == 'provider_http' and row['event'] == 'end' for row in rows)
    finally:
        release.set()
        executor.shutdown(wait=True)
    assert completed.is_set()
    assert any(row['stage'] == 'provider_http' and row['event'] == 'end'
               for row in telemetry(caplog, trace.request_id))


def test_provider_metrics_reject_non_counts_unknown_keys_and_unrecognized_finish_reason(caplog):
    caplog.set_level(logging.WARNING, logger=tracing.LOGGER.name)
    trace = tracing.AIRequestTrace()
    trace.provider_metrics(input_bytes=123, request_bytes=True, response_bytes=-1,
        prompt_tokens='private usage text', completion_tokens=1.5, total_tokens=10**30,
        finish_reason='private provider error', raw_payload='private response')
    row = telemetry(caplog, trace.request_id)[0]
    assert row['input_bytes'] == 123
    assert row['finish_reason'] == 'unknown'
    assert not {'request_bytes', 'response_bytes', 'prompt_tokens', 'completion_tokens',
                'total_tokens', 'raw_payload'} & row.keys()
    assert 'private' not in json.dumps(row)


def test_telemetry_logging_failure_cannot_break_the_provider_result(monkeypatch):
    transport(monkeypatch)
    trace = tracing.AIRequestTrace()
    def failed_logging(*args, **kwargs):
        raise RuntimeError('unavailable diagnostic sink')
    monkeypatch.setattr(tracing.LOGGER, 'warning', failed_logging)
    snapshot = _snapshot()
    with trace.activate():
        result = report.request_explanation_with_shadow(snapshot, authoritative_snapshot=snapshot)
    assert result['llm_shadow_decision']['policy'] == 'HOLD'
