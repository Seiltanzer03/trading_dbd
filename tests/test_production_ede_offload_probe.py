from __future__ import annotations

import importlib.util
import hashlib
import json
import pathlib
import sqlite3

import pytest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "production_ede_offload.py"
SPEC = importlib.util.spec_from_file_location("production_ede_offload_probe_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_probe_retries_transient_timeout_without_relaxing_three_second_budget(monkeypatch):
    calls: list[str] = []
    sleeps: list[float] = []

    def flaky_exec(_client, command: str, *, timeout=None):
        calls.append(command)
        if len(calls) < 3:
            raise RuntimeError("curl: (28) Operation timed out after 3002 milliseconds")
        return ""

    monkeypatch.setattr(MODULE, "_exec", flaky_exec)
    monkeypatch.setattr(MODULE.time, "sleep", lambda seconds: sleeps.append(float(seconds)))

    MODULE._probe_api(object(), attempts=3, retry_delay=0.25)

    assert MODULE.API_PROBE_MAX_TIME_SECONDS == 3
    assert len(calls) == 3
    assert all("--max-time 3" in command for command in calls)
    assert sleeps == [0.25, 0.25]


def test_probe_still_fails_closed_after_bounded_retries(monkeypatch):
    calls: list[str] = []

    def always_slow(_client, command: str, *, timeout=None):
        calls.append(command)
        raise RuntimeError("curl timed out")

    monkeypatch.setattr(MODULE, "_exec", always_slow)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        MODULE._probe_api(object(), attempts=2, retry_delay=0)

    assert len(calls) == 2
    assert all("--max-time 3" in command for command in calls)


def test_probe_rejects_zero_attempts():
    with pytest.raises(ValueError, match="attempts must be >= 1"):
        MODULE._probe_api(object(), attempts=0)


def test_post_transfer_recovery_is_bounded_and_does_not_change_probe_sla(
    monkeypatch, capsys,
):
    sleeps: list[float] = []
    monkeypatch.setattr(MODULE.time, "sleep", lambda seconds: sleeps.append(seconds))

    MODULE._wait_for_post_transfer_recovery()

    assert MODULE.POST_TRANSFER_RECOVERY_SECONDS == 30.0
    assert MODULE.API_PROBE_MAX_TIME_SECONDS == 3
    assert sleeps == [30.0]
    assert "EDE_POST_TRANSFER_RECOVERY_SECONDS=30" in capsys.readouterr().out


def test_post_transfer_recovery_rejects_negative_delay(monkeypatch):
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)
    with pytest.raises(ValueError, match="delay must be >= 0"):
        MODULE._wait_for_post_transfer_recovery(-1)


def test_ssh_connection_enables_transport_keepalive(monkeypatch):
    class Transport:
        keepalive = None

        def set_keepalive(self, seconds):
            self.keepalive = seconds

    class Client:
        def __init__(self):
            self.transport = Transport()

        def set_missing_host_key_policy(self, _policy):
            pass

        def connect(self, *_args, **_kwargs):
            pass

        def get_transport(self):
            return self.transport

        def close(self):
            pass

    client = Client()

    class Paramiko:
        SSHException = RuntimeError

        @staticmethod
        def SSHClient():
            return client

        @staticmethod
        def AutoAddPolicy():
            return object()

    monkeypatch.setattr(MODULE, "_paramiko", lambda: Paramiko)

    assert MODULE._connect("secret", attempts=1) is client
    assert client.transport.keepalive == MODULE.SSH_KEEPALIVE_SECONDS == 30


def test_whole_ssh_operation_retries_channel_reset(monkeypatch):
    class TransientSSHError(Exception):
        pass

    class Paramiko:
        SSHException = TransientSSHError

    class Client:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    clients = [Client(), Client(), Client()]
    attempts = []
    sleeps = []
    monkeypatch.setattr(MODULE, "_paramiko", lambda: Paramiko)
    monkeypatch.setattr(MODULE, "_connect", lambda _password: clients[len(attempts)])
    monkeypatch.setattr(MODULE.time, "sleep", lambda seconds: sleeps.append(seconds))

    def operation(_client):
        attempts.append(1)
        if len(attempts) < 3:
            raise TransientSSHError("connection reset")
        return "ok"

    assert MODULE._retry_ssh_operation("secret", operation, attempts=3) == "ok"
    assert len(attempts) == 3
    assert sleeps == [5, 10]
    assert all(client.closed for client in clients)


def test_whole_ssh_operation_does_not_retry_remote_contract_failure(monkeypatch):
    class Paramiko:
        SSHException = OSError

    class Client:
        def close(self):
            pass

    calls = []
    monkeypatch.setattr(MODULE, "_paramiko", lambda: Paramiko)
    monkeypatch.setattr(MODULE, "_connect", lambda _password: Client())

    def operation(_client):
        calls.append(1)
        raise RuntimeError("exact SHA mismatch")

    with pytest.raises(RuntimeError, match="exact SHA mismatch"):
        MODULE._retry_ssh_operation("secret", operation, attempts=4)
    assert calls == [1]


def test_downloaded_exact_sha_prestart_backup_is_verified(tmp_path):
    database = tmp_path / "backup.sqlite3"
    conn = sqlite3.connect(database)
    conn.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO evidence(value) VALUES('causal')")
    conn.commit()
    conn.close()

    expected_sha = "a" * 40
    payload = {
        "backup_contract_version": MODULE.BACKUP_CONTRACT_VERSION,
        "backup_id": "exact-prestart",
        "reason": "prestart",
        "created_ts": 123.0,
        "source_db": str(MODULE.REMOTE_DATABASE),
        "database_file": database.name,
        "database_size_bytes": database.stat().st_size,
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "git_commit": expected_sha,
        "verified": True,
    }
    payload["manifest_payload_sha256"] = MODULE._manifest_payload_sha256(payload)
    manifest = tmp_path / "backup.manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    verified = MODULE._verify_local_exact_backup(
        database, manifest, expected_sha=expected_sha
    )
    assert verified["backup_id"] == "exact-prestart"


def test_downloaded_exact_sha_scheduled_backup_is_verified(tmp_path):
    database = tmp_path / "scheduled.sqlite3"
    conn = sqlite3.connect(database)
    conn.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO evidence(value) VALUES('causal')")
    conn.commit()
    conn.close()

    expected_sha = "a" * 40
    payload = {
        "backup_contract_version": MODULE.BACKUP_CONTRACT_VERSION,
        "backup_id": "exact-scheduled",
        "reason": "scheduled",
        "created_ts": 123.0,
        "source_db": str(MODULE.REMOTE_DATABASE),
        "database_file": database.name,
        "database_size_bytes": database.stat().st_size,
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "git_commit": expected_sha,
        "verified": True,
    }
    payload["manifest_payload_sha256"] = MODULE._manifest_payload_sha256(payload)
    manifest = tmp_path / "scheduled.manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    verified = MODULE._verify_local_exact_backup(
        database, manifest, expected_sha=expected_sha
    )
    assert verified["backup_id"] == "exact-scheduled"
    assert verified["reason"] == "scheduled"


def test_downloaded_backup_from_another_sha_is_rejected(tmp_path):
    database = tmp_path / "backup.sqlite3"
    conn = sqlite3.connect(database)
    conn.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    payload = {
        "backup_contract_version": MODULE.BACKUP_CONTRACT_VERSION,
        "backup_id": "wrong-sha",
        "reason": "prestart",
        "created_ts": 123.0,
        "source_db": str(MODULE.REMOTE_DATABASE),
        "database_file": database.name,
        "database_size_bytes": database.stat().st_size,
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "git_commit": "b" * 40,
        "verified": True,
    }
    manifest = tmp_path / "backup.manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="expected SHA"):
        MODULE._verify_local_exact_backup(
            database, manifest, expected_sha="a" * 40
        )
