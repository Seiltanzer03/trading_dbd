"""Transport fixtures do not claim live receipts or measured scheduler cadence."""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import fomc_scheduler_transport as transport
from test_fomc_prospective_capture import collect, SHA, NOW


def envelope():
    payload, _ = collect()
    payload.update(publication_contract_version=transport.PUBLICATION_CONTRACT,
                   published_for_sha=SHA, publication_run_id='spare-123')
    return payload


def receive(tmp_path, payload, **kwargs):
    return transport.receive(
        io.BytesIO(json.dumps(payload).encode()),
        command=f'fomc-publish-v1 {SHA} spare-123', directory=tmp_path,
        identity=lambda: SHA, healthy=lambda: True, clock=lambda: NOW + 10,
        **kwargs)


def test_only_validated_fomc_bytes_are_atomically_published(tmp_path):
    payload = envelope()
    receipt = receive(tmp_path, payload)
    assert json.loads((tmp_path / transport.REMOTE_NAME).read_bytes()) == payload
    assert receipt['received_ts'] == NOW + 10
    assert receipt['capture_ts'] == NOW
    assert receipt['production_authority'] is False
    assert receipt['published_for_sha'] == SHA
    assert {p.name for p in tmp_path.iterdir()} == {transport.REMOTE_NAME, '.fomc-publication.lock'}


@pytest.mark.parametrize('mutation', ['authority', 'sha', 'body', 'stale', 'run', 'contract'])
def test_rejected_capture_preserves_existing_bytes(tmp_path, mutation):
    destination = tmp_path / transport.REMOTE_NAME
    destination.write_bytes(b'old accepted bytes')
    payload = envelope()
    if mutation == 'authority': payload['production_authority'] = True
    if mutation == 'sha': payload['published_for_sha'] = '2' * 40
    if mutation == 'body': payload['records'][0]['html'] += 'tampered'
    if mutation == 'stale': payload['captured_ts'] = NOW - 4000
    if mutation == 'run': payload['publication_run_id'] = 'different'
    if mutation == 'contract': payload['publication_contract_version'] = 'unknown'
    with pytest.raises(ValueError): receive(tmp_path, payload)
    assert destination.read_bytes() == b'old accepted bytes'


@pytest.mark.parametrize('command', ['id', 'fomc-publish-v1 ' + SHA + ' ../other',
                                     'fomc-publish-v1 ' + SHA + ' spare-123; id'])
def test_forced_command_rejects_other_commands_before_reading(tmp_path, command):
    with pytest.raises(ValueError, match='COMMAND'):
        transport.receive(None, command=command, directory=tmp_path,
                          identity=lambda: SHA, healthy=lambda: True)
    assert not list(tmp_path.iterdir())


def test_deploy_generation_change_and_unhealthy_host_refuse_write(tmp_path):
    data = json.dumps(envelope()).encode()
    common = dict(command=f'fomc-publish-v1 {SHA} spare-123', directory=tmp_path,
                  clock=lambda: NOW + 10)
    with pytest.raises(ValueError, match='HEALTH'):
        transport.receive(io.BytesIO(data), identity=lambda: SHA,
                          healthy=lambda: False, **common)
    identities = iter([SHA, '2' * 40])
    with pytest.raises(ValueError, match='SHA'):
        transport.receive(io.BytesIO(data), identity=lambda: next(identities),
                          healthy=lambda: True, **common)
    assert not list(tmp_path.iterdir())


def test_bounded_input_and_explicit_fresh_acquisition_refusal(tmp_path):
    common = dict(command=f'fomc-publish-v1 {SHA} spare-123', directory=tmp_path,
                  identity=lambda: SHA, healthy=lambda: True, clock=lambda: NOW + 10)
    with pytest.raises(ValueError, match='BOUND'):
        transport.receive(io.BytesIO(b'x' * (transport.MAX_CAPTURE_BYTES + 1)), **common)
    payload = envelope()
    payload = {k: payload[k] for k in ('contract_version', 'code_sha', 'captured_ts',
               'production_authority', 'publication_contract_version', 'published_for_sha',
               'publication_run_id')}
    payload.update(status='UNAVAILABLE', reason='OFFICIAL_SOURCE_UNAVAILABLE')
    receipt = receive(tmp_path, payload)
    assert receipt['capture_status'] == 'UNAVAILABLE'
    assert json.loads((tmp_path / transport.REMOTE_NAME).read_bytes())['status'] == 'UNAVAILABLE'


def test_older_capture_cannot_restore_ok_after_newer_failure(tmp_path):
    payload = envelope()
    payload.update(status='UNAVAILABLE', reason='SOURCE_FAILED', captured_ts=NOW + 5)
    receive(tmp_path, payload)
    destination = tmp_path / transport.REMOTE_NAME
    before = destination.read_bytes()
    with pytest.raises(ValueError, match='OUT_OF_ORDER'):
        receive(tmp_path, envelope())
    assert destination.read_bytes() == before


def test_equal_clock_replay_refused_but_new_code_generation_replaces_old(tmp_path):
    receive(tmp_path, envelope())
    with pytest.raises(ValueError, match='OUT_OF_ORDER'):
        receive(tmp_path, envelope())
    destination = tmp_path / transport.REMOTE_NAME
    previous = json.loads(destination.read_bytes())
    previous.update(published_for_sha='2' * 40, captured_ts=NOW + 5)
    destination.write_text(json.dumps(previous))
    assert receive(tmp_path, envelope())['published_for_sha'] == SHA


def test_github_client_uses_same_receiver_and_checks_delivered_bytes(tmp_path):
    report = tmp_path / 'report.json'
    raw = json.dumps(envelope()).encode()
    report.write_bytes(raw)
    class Channel:
        def shutdown_write(self): self.closed = True
        def recv_exit_status(self): return 0
    class Stream(io.BytesIO):
        channel = Channel()
    stdin = Stream()
    stdout = Stream(json.dumps(dict(published_for_sha=SHA, publication_run_id='spare-123',
                                   payload_sha256=transport.hashlib.sha256(raw).hexdigest())).encode())
    class Client:
        def exec_command(self, command, timeout):
            assert 'scripts/fomc_scheduler_transport.py receive' in command
            assert 'SSH_ORIGINAL_COMMAND=' in command
            assert timeout == 30
            return stdin, stdout, Stream()
    transport.deliver_with_client(Client(), report, sha=SHA, run_id='spare-123')
    assert stdin.getvalue() == raw
    assert stdin.channel.closed is True


def test_sender_uses_pinned_host_key_no_shell_and_checks_receipt(tmp_path, monkeypatch):
    report = tmp_path / 'report.json'
    report.write_text(json.dumps(envelope()))
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        receipt = dict(published_for_sha=SHA, publication_run_id='spare-123',
                       payload_sha256=transport.hashlib.sha256(report.read_bytes()).hexdigest())
        return type('Result', (), {'stdout': json.dumps(receipt).encode()})()
    monkeypatch.setattr(transport.subprocess, 'run', run)
    transport.deliver(report, sha=SHA, run_id='spare-123', host='94.241.171.182',
                      user='root', key=tmp_path/'key', known_hosts=tmp_path/'known_hosts')
    argv, options = calls[0]
    assert 'StrictHostKeyChecking=yes' in argv
    assert 'BatchMode=yes' in argv
    assert 'IdentitiesOnly=yes' in argv
    assert options['input'] == report.read_bytes()
    assert options.get('shell', False) is False
    assert options['timeout'] == 90
    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: type('Result', (), {'stdout': b'{}'})())
    with pytest.raises(ValueError, match='RECEIPT'):
        transport.deliver(report, sha=SHA, run_id='spare-123', host='94.241.171.182',
                          user='root', key=tmp_path/'key', known_hosts=tmp_path/'known_hosts')
