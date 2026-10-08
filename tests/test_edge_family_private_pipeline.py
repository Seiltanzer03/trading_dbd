import base64
import gzip
import io
import json
from types import SimpleNamespace

import pytest

from test_edge_family_private_archive import MemoryStore


SHA = 'a' * 40


def exporter_module():
    import scripts.export_unified_edge_reviews as module
    return module


def private_pipeline():
    import scripts.run_private_edge_family_pipeline as module
    return module


def test_exact_sha_mismatch_prevents_remote_query_and_output(tmp_path, monkeypatch):
    module = exporter_module()
    calls = []
    client = SimpleNamespace(close=lambda: calls.append('closed'))
    monkeypatch.setattr(module, '_connect', lambda password: client)
    def reject(client, sha):
        raise ValueError('sha mismatch')
    monkeypatch.setattr(module, '_verify_sha', reject, raising=False)
    with pytest.raises(ValueError):
        module.export_actual(tmp_path/'reviews.json', maximum=32,
                             password='test-only', expected_sha=SHA)
    assert calls == ['closed']
    assert not (tmp_path/'reviews.json').exists()


@pytest.mark.parametrize('fail_second', [False, True])
def test_export_checks_sha_before_and_after_read_only_snapshot(tmp_path, monkeypatch, fail_second):
    module = exporter_module()
    events = []
    raw = json.dumps({'read_only': True, 'reviews': [], 'exported_ts': 100.}).encode()
    output = io.BytesIO(base64.b64encode(gzip.compress(raw)))
    output.channel = SimpleNamespace(recv_exit_status=lambda: 0)
    def query(command, timeout):
        events.append('query')
        return None, output, io.BytesIO(b'')
    client = SimpleNamespace(exec_command=query, close=lambda: events.append('closed'))
    monkeypatch.setattr(module, '_connect', lambda password: client)
    def verify(client, sha):
        assert sha == SHA
        if fail_second and 'query' in events:
            raise ValueError('changed deployment')
        events.append('verified')
    monkeypatch.setattr(module, '_verify_sha', verify, raising=False)
    if fail_second:
        with pytest.raises(ValueError):
            module.export_actual(tmp_path/'reviews.json', maximum=32,
                                 password='test-only', expected_sha=SHA)
        assert not (tmp_path/'reviews.json').exists()
    else:
        assert module.export_actual(tmp_path/'reviews.json', maximum=32,
                                    password='test-only', expected_sha=SHA)['reviews'] == []
        assert events == ['verified', 'query', 'verified', 'closed']


def envelope():
    return {'read_only': True, 'reviews': [], 'exported_ts': 100.}


def test_private_generations_restore_history_and_publish_only_whitelisted_counts(tmp_path):
    module = private_pipeline()
    client = MemoryStore()
    first = module.run_job(client, expected_sha=SHA, generation=1,
                           workspace=tmp_path/'first', exporter=envelope, clock=lambda: 200.)
    second = module.run_job(client, expected_sha=SHA, generation=2,
                            workspace=tmp_path/'second', exporter=envelope, clock=lambda: 300.)
    assert first['archive_state'] == 'INITIAL'
    assert second['archive_state'] == 'RESTORED'
    assert second['archive_committed'] is True
    assert second['active_model_count'] == 0
    assert second['production_activation_performed'] is False
    assert second == json.loads((tmp_path/'second'/'public_summary.json').read_bytes())
    assert set(second) == module.PUBLIC_FIELDS
    assert len(client.objects) == 3


def test_sequential_actual_generations_accumulate_immutable_native_snapshots(tmp_path):
    from test_edge_family_archive import episode, export
    from seiltanzer.edge_family_private_archive import restore
    module = private_pipeline()
    client = MemoryStore()
    old, new = episode('old-private', captured=100.), episode('new-private', captured=200.)
    module.run_job(client, expected_sha=SHA, generation=1, workspace=tmp_path/'first',
                   exporter=lambda: export(old, exported=150.), clock=lambda: 180.)
    result = module.run_job(client, expected_sha=SHA, generation=2, workspace=tmp_path/'second',
                            exporter=lambda: export(new, exported=250.), clock=lambda: 300.)
    raw, receipt = restore(client)
    records = json.loads(raw)['episodes']
    assert records == [old, new]
    assert records[0]['snapshot_json'] == old['snapshot_json']
    assert records[0]['captured_ts'] == 100.
    assert result['archive_episode_count'] == 2
    assert result['active_model_count'] == 0
    assert 'old-private' not in json.dumps(result)


def test_public_cli_does_not_leak_private_provider_exception(tmp_path, monkeypatch):
    import sys
    module = private_pipeline()
    def denied(**kwargs):
        raise RuntimeError('secret-provider-body-account-id')
    client = MemoryStore()
    client.get_object = denied
    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace(client=lambda *a, **kw: client))
    monkeypatch.setitem(sys.modules, 'botocore.config', SimpleNamespace(Config=lambda **kw: None))
    with pytest.raises(SystemExit) as caught:
        module.main(['--expected-sha', SHA, '--generation', '1',
                     '--workspace', str(tmp_path/'private')])
    assert str(caught.value) == 'PRIVATE_FAMILY_PIPELINE_FAILED'


@pytest.mark.parametrize('diagnostic', ['successful_sha_stderr', 'transient_connect_retry'])
def test_public_cli_silences_legacy_ssh_helpers(tmp_path, monkeypatch, capsys, diagnostic):
    import sys
    module = private_pipeline()
    export_module = exporter_module()
    marker = 'private-ssh-diagnostic-account-id'
    def stream(raw):
        result = io.BytesIO(raw)
        result.channel = SimpleNamespace(recv_exit_status=lambda: 0)
        return result
    def command(value, timeout):
        if value.startswith('git '):
            return None, stream((SHA + '\n').encode()), stream(
                marker.encode() if diagnostic == 'successful_sha_stderr' else b'')
        payload = base64.b64encode(gzip.compress(json.dumps(envelope()).encode()))
        return None, stream(payload), stream(b'')
    ssh = SimpleNamespace(exec_command=command, close=lambda: None)
    def connect(password):
        if diagnostic == 'transient_connect_retry':
            print('SSH attempt1 failed: ' + marker)
        return ssh
    monkeypatch.setattr(export_module, '_connect', connect)
    monkeypatch.setenv('SSH_PASSWORD', 'test-only')
    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace(client=lambda *a, **kw: MemoryStore()))
    monkeypatch.setitem(sys.modules, 'botocore.config', SimpleNamespace(Config=lambda **kw: None))
    assert module.main(['--expected-sha', SHA, '--generation', '1',
                        '--workspace', str(tmp_path/'private')]) == 0
    output = capsys.readouterr()
    assert marker not in output.out + output.err
    assert set(json.loads(output.out)) == module.PUBLIC_FIELDS


def test_packaged_models_do_not_become_active_production_models(tmp_path, monkeypatch):
    module = private_pipeline()
    pipeline = module.run_pipeline
    def packaged(*args, **kwargs):
        result = pipeline(*args, **kwargs)
        result['diagnostics']['active_model_count'] = 1
        return result
    monkeypatch.setattr(module, 'run_pipeline', packaged)
    summary = module.run_job(MemoryStore(), expected_sha=SHA, generation=1,
                             workspace=tmp_path, exporter=envelope, clock=lambda: 200.)
    assert summary['active_model_count'] == 0
    assert summary['packaged_model_count'] == 1
    assert summary['production_activation_performed'] is False


def test_corrupt_latest_stops_before_export(tmp_path):
    module = private_pipeline()
    from seiltanzer.edge_family_private_archive import LATEST_KEY
    client = MemoryStore()
    client.objects[LATEST_KEY] = b'bad'
    def must_not_export():
        pytest.fail('export ran before verified history restore')
    with pytest.raises(ValueError):
        module.run_job(client, expected_sha=SHA, generation=2,
                       workspace=tmp_path, exporter=must_not_export, clock=lambda: 200.)


@pytest.mark.parametrize('change', [{'synthetic': True}, {'read_only': False}, {'reviews': [{}] * 33}])
def test_invalid_actual_export_never_commits_archive(tmp_path, change):
    module = private_pipeline()
    client = MemoryStore()
    with pytest.raises(ValueError):
        module.run_job(client, expected_sha=SHA, generation=1, workspace=tmp_path,
                       exporter=lambda: dict(envelope(), **change), clock=lambda: 200.)
    assert not client.objects


def test_private_diagnostics_and_review_ids_never_enter_public_summary(tmp_path, monkeypatch):
    module = private_pipeline()
    pipeline = module.run_pipeline
    def marked(*args, **kwargs):
        result = pipeline(*args, **kwargs)
        result['diagnostics']['reason'] = 'private account secret-review-id'
        result['diagnostics']['archive_exclusions'] = [{'review_id': 'secret-review-id'}]
        return result
    monkeypatch.setattr(module, 'run_pipeline', marked)
    summary = module.run_job(MemoryStore(), expected_sha=SHA, generation=1,
                             workspace=tmp_path, exporter=envelope, clock=lambda: 200.)
    assert 'secret-review-id' not in json.dumps(summary)
    assert summary['reason'] == 'NO_VALIDATED_MODEL'


def test_workflow_serializes_trusted_main_private_writer_without_public_raw_artifacts():
    import yaml
    from pathlib import Path
    workflow = yaml.safe_load(Path('.github/workflows/edge-family-training.yml').read_text())
    job = workflow['jobs']['actual-private-object-pipeline']
    assert job['concurrency'] == {'group': 'edge-family-private-object-storage-v1',
                                  'cancel-in-progress': False}
    assert "github.event_name == 'workflow_dispatch'" in job['if']
    assert "github.ref == 'refs/heads/main'" in job['if']
    assert "github.run_id" in workflow['concurrency']['group']
    assert workflow['concurrency']['cancel-in-progress'] != True
    assert not any('upload-artifact' in step.get('uses', '') for step in job['steps'])
    commands = '\n'.join(str(step.get('run', '')) for step in job['steps'])
    assert 'GITHUB_SHA' in commands and 'run_private_edge_family_pipeline' in commands
