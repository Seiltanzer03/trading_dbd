from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/retire_local_backup_after_offhost_restore.py"
)
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location(
    "retire_local_backup_after_offhost_restore", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Client:
    def close(self):
        pass


def test_retirement_requires_restore_proof_and_confines_exact_pair(tmp_path, monkeypatch):
    result = tmp_path / "result.json"
    result.write_text(json.dumps({
        "restore_contract": "trading-dbd-offhost-full-restore-v1",
        "full_restore_verified": True,
        "sqlite_quick_check": "ok",
        "immutable_sqlite_read": True,
        "bucket": "trading-dbd-backups-2026",
        "backup_id": "20260905T180524Z-local-524722",
        "database_sha256": "a" * 64,
        "database_size_bytes": 6729957376,
    }))
    commands = []
    probes = []
    monkeypatch.setattr(MODULE, "_connect", lambda _password: Client())
    monkeypatch.setattr(MODULE, "_probe_api", lambda _client: probes.append(True))

    def fake_exec(_client, command, timeout):
        commands.append((command, timeout))
        if "python3 -c" in command:
            return "LOCAL_BACKUP_RETIRED=" + json.dumps({
                "backup_id": "20260905T180524Z-local-524722",
                "database_sha256": "a" * 64,
                "database_size_bytes": 6729957376,
            })
        return ""

    monkeypatch.setattr(MODULE, "_exec", fake_exec)
    proof = MODULE.retire(password="secret", restore_result=result)
    assert proof["backup_id"] == "20260905T180524Z-local-524722"
    assert len(probes) == 1
    assert commands[0][1] == 900
    assert "/opt/seiltanzer/data/backups/local" in commands[0][0]
    assert "database.unlink()" in commands[0][0]
    assert "trades.db" in commands[0][0]
    assert any("systemctl start seiltanzer" in command for command, _ in commands)
