#!/usr/bin/env python3
"""Offload EDE research from the production VPS onto a CI runner.

The production server is used only for:
1. exact-SHA/acceptance-marker validation,
2. transfer of the deploy-created verified immutable SQLite backup,
3. small research-ledger/result transfers,
4. fail-closed API probes.

The CPU-heavy EDE search itself must never run on production.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shlex
import socket
import sqlite3
import time
from typing import Any

HOST = "94.241.171.182"
REMOTE_ROOT = pathlib.PurePosixPath("/opt/seiltanzer")
REMOTE_RESEARCH = REMOTE_ROOT / "data/research"
REMOTE_DATABASE = REMOTE_ROOT / "data/trades.db"
REMOTE_LOCAL_BACKUPS = REMOTE_ROOT / "data/backups/local"
REMOTE_PYTHON = REMOTE_ROOT / ".venv/bin/python"
REMOTE_ORCHESTRATOR = REMOTE_ROOT / "scripts/production_research_acceptance.py"
REMOTE_HISTORICAL_INSTALLER = (
    REMOTE_ROOT / "scripts/install_offhost_historical_macro_bundle.py"
)
REMOTE_HISTORICAL_BUNDLE = (
    REMOTE_RESEARCH / "official_macro_historical_offhost_latest.json"
)
API_PROBE_MAX_TIME_SECONDS = 3
API_PROBE_ATTEMPTS = 3
API_PROBE_RETRY_DELAY_SECONDS = 2.0
POST_TRANSFER_RECOVERY_SECONDS = 30.0
SSH_KEEPALIVE_SECONDS = 30
SSH_OPERATION_ATTEMPTS = 4
MAX_EXACT_BACKUP_AGE_SECONDS = 60 * 60
BACKUP_CONTRACT_VERSION = "seiltanzer-backup-v1"
EXACT_BACKUP_REASONS = frozenset(("prestart", "scheduled"))

LEDGER_NAMES = (
    "ede_frozen_evidence.jsonl",
    "ede_v13_candidate_registry.jsonl",
)
OUTPUT_NAMES = (
    "ede_v13_latest_audit.json",
    *LEDGER_NAMES,
    "ede_transition_latest_audit.json",
)


def _paramiko() -> Any:
    import paramiko  # type: ignore

    return paramiko


def _connect(password: str, *, attempts: int = 4):
    paramiko = _paramiko()
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                HOST,
                username="root",
                password=password,
                timeout=15,
                banner_timeout=30,
                auth_timeout=30,
                look_for_keys=False,
                allow_agent=False,
            )
            transport = client.get_transport()
            if transport is None:
                raise RuntimeError("SSH transport unavailable after connect")
            # The low-priority online copy of the multi-GiB production DB can
            # legitimately run for several minutes. Keep the authenticated
            # transport alive while the remote command is otherwise quiet;
            # this does not extend the command timeout or retry failed work.
            transport.set_keepalive(SSH_KEEPALIVE_SECONDS)
            return client
        except (paramiko.SSHException, socket.timeout, OSError) as exc:
            client.close()
            last_error = exc
            if attempt == attempts:
                raise
            delay = 5 * attempt
            print(
                f"transient SSH connect failure attempt={attempt}: "
                f"{exc}; retrying in {delay}s"
            )
            time.sleep(delay)
    raise RuntimeError(f"SSH connection unavailable: {last_error}")


def _retry_ssh_operation(password: str, operation, *, attempts: int = SSH_OPERATION_ATTEMPTS):
    """Retry a whole idempotent transfer after connect or channel resets."""
    if attempts < 1:
        raise ValueError("SSH operation attempts must be >= 1")
    paramiko = _paramiko()
    transient = (paramiko.SSHException, socket.timeout, TimeoutError, EOFError, OSError)
    for attempt in range(1, attempts + 1):
        client = _connect(password)
        try:
            return operation(client)
        except transient as exc:
            if attempt == attempts:
                raise
            delay = 5 * attempt
            print(
                f"transient SSH operation failure attempt={attempt}/{attempts}: "
                f"{type(exc).__name__}; retrying in {delay}s",
                flush=True,
            )
            time.sleep(delay)
        finally:
            client.close()
    raise RuntimeError("SSH operation retries exhausted")


def _exec(client, command: str, *, timeout: float | None = None) -> str:
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    status = stdout.channel.recv_exit_status()
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if status != 0:
        raise RuntimeError(
            f"remote command failed status={status}: {err or out or command}"
        )
    if err:
        print(err, end="" if err.endswith("\n") else "\n")
    return out


def _verify_sha(client, expected_sha: str) -> None:
    remote_sha = _exec(client, f"git -C {REMOTE_ROOT} rev-parse HEAD").strip()
    if remote_sha != expected_sha:
        raise RuntimeError(
            f"production SHA mismatch remote={remote_sha!r} expected={expected_sha!r}"
        )


def _probe_api(
    client,
    *,
    attempts: int = API_PROBE_ATTEMPTS,
    retry_delay: float = API_PROBE_RETRY_DELAY_SECONDS,
) -> None:
    """Fail closed on sustained API latency while tolerating one short spike.

    Each attempt keeps the exact 3-second HTTP budget. Retries only distinguish a
    transient scheduling/latency spike from a sustained production health defect;
    they never turn a slow response into a passing response.
    """
    if attempts < 1:
        raise ValueError("API probe attempts must be >= 1")
    command = (
        "test \"$(curl -sS -o /dev/null -w '%{http_code}' "
        f"--max-time {API_PROBE_MAX_TIME_SECONDS} "
        "http://127.0.0.1:8790/api/state)\" = 200"
    )
    for attempt in range(1, attempts + 1):
        try:
            _exec(client, command)
            if attempt > 1:
                print(f"API probe recovered attempt={attempt}/{attempts}")
            return
        except RuntimeError as exc:
            if attempt == attempts:
                raise RuntimeError(
                    "API probe failed after "
                    f"{attempts} attempts with per-attempt max-time="
                    f"{API_PROBE_MAX_TIME_SECONDS}s: {exc}"
                ) from exc
            print(
                f"transient API probe failure attempt={attempt}/{attempts}: "
                f"{exc}; retrying in {retry_delay:g}s"
            )
            time.sleep(max(0.0, float(retry_delay)))


def _wait_for_post_transfer_recovery(
    delay: float = POST_TRANSFER_RECOVERY_SECONDS,
) -> None:
    """Let production leave the bounded multi-GiB copy pressure window.

    This wait is outside the HTTP probe.  Every subsequent request retains the
    exact three-second SLA and must still return HTTP 200 to pass.
    """
    seconds = float(delay)
    if seconds < 0.0:
        raise ValueError("post-transfer recovery delay must be >= 0")
    print(f"EDE_POST_TRANSFER_RECOVERY_SECONDS={seconds:g}", flush=True)
    time.sleep(seconds)


def _release_gate(
    password: str, *, acceptance_run_id: str, expected_sha: str
) -> None:
    """Release the exact-run cooperative pause with a fresh retryable SSH session."""
    client = _connect(password)
    try:
        _verify_sha(client, expected_sha)
        _exec(
            client,
            " ".join(
                [
                    shlex.quote(str(REMOTE_PYTHON)),
                    shlex.quote(str(REMOTE_ORCHESTRATOR)),
                    "release-gate",
                    "--acceptance-run-id",
                    shlex.quote(acceptance_run_id),
                    "--expected-sha",
                    shlex.quote(expected_sha),
                ]
            ),
        )
    finally:
        client.close()


def _local_quick_check(path: pathlib.Path) -> None:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=30.0)
    try:
        verdict = conn.execute("PRAGMA quick_check").fetchone()[0]
        if verdict != "ok":
            raise RuntimeError(f"immutable snapshot quick_check failed: {verdict}")
    finally:
        conn.close()


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_payload_sha256(payload: dict[str, Any]) -> str:
    canonical = {
        key: value
        for key, value in payload.items()
        if key != "manifest_payload_sha256"
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify_local_exact_backup(
    database: pathlib.Path,
    manifest_path: pathlib.Path,
    *,
    expected_sha: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"invalid downloaded backup manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("downloaded backup manifest must be an object")
    if payload.get("backup_contract_version") != BACKUP_CONTRACT_VERSION:
        raise RuntimeError("downloaded backup contract mismatch")
    if payload.get("verified") is not True:
        raise RuntimeError("downloaded backup is not verified")
    reason = str(payload.get("reason") or "")
    if reason not in EXACT_BACKUP_REASONS:
        raise RuntimeError("downloaded backup reason is not accepted for EDE")
    if str(payload.get("git_commit") or "") != expected_sha:
        raise RuntimeError("downloaded backup does not belong to the expected SHA")
    if pathlib.PurePosixPath(str(payload.get("source_db") or "")) != REMOTE_DATABASE:
        raise RuntimeError("downloaded backup source DB mismatch")
    database_name = str(payload.get("database_file") or "")
    if not database_name or pathlib.PurePosixPath(database_name).name != database_name:
        raise RuntimeError("downloaded backup database_file is unsafe")
    if int(payload.get("database_size_bytes") or -1) != database.stat().st_size:
        raise RuntimeError("downloaded backup byte count mismatch")
    expected_database_sha = str(payload.get("database_sha256") or "")
    if len(expected_database_sha) != 64 or _sha256(database) != expected_database_sha:
        raise RuntimeError("downloaded backup SHA256 mismatch")
    expected_manifest_sha = str(payload.get("manifest_payload_sha256") or "")
    if expected_manifest_sha and _manifest_payload_sha256(payload) != expected_manifest_sha:
        raise RuntimeError("downloaded backup manifest SHA256 mismatch")
    _local_quick_check(database)
    return payload


def _select_remote_exact_backup(client, *, expected_sha: str) -> dict[str, Any]:
    """Select one recent immutable exact-SHA backup without touching the live DB.

    Prefer the deploy-created prestart recovery point.  On the small production
    disk, two later 15-minute scheduled snapshots can legitimately rotate that
    5.3-GiB file before the EDE handoff reaches this step; a scheduled snapshot
    has the same immutable manifest, hash and SQLite verification contract and
    is therefore the fail-closed fallback.
    """
    selector = r'''
import hashlib
import json
import os
import pathlib
import time

root = pathlib.Path(os.environ["BACKUP_ROOT"]).resolve()
expected_sha = os.environ["EXPECTED_SHA"]
source_db = pathlib.Path(os.environ["SOURCE_DB"])
max_age = float(os.environ["MAX_AGE_SECONDS"])
now = time.time()
candidates = []

def manifest_hash(payload):
    canonical = {
        key: value
        for key, value in payload.items()
        if key != "manifest_payload_sha256"
    }
    encoded = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

for manifest_path in root.glob("*.manifest.json"):
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        created_ts = float(payload.get("created_ts") or 0.0)
        age_sec = now - created_ts
        database_name = str(payload.get("database_file") or "")
        if not database_name or pathlib.Path(database_name).name != database_name:
            continue
        database_path = (root / database_name).resolve()
        if database_path.parent != root or not database_path.is_file():
            continue
        if payload.get("backup_contract_version") != "seiltanzer-backup-v1":
            continue
        reason = str(payload.get("reason") or "")
        if payload.get("verified") is not True or reason not in ("prestart", "scheduled"):
            continue
        if str(payload.get("git_commit") or "") != expected_sha:
            continue
        if pathlib.Path(str(payload.get("source_db") or "")) != source_db:
            continue
        if age_sec < 0 or age_sec > max_age:
            continue
        if int(payload.get("database_size_bytes") or -1) != database_path.stat().st_size:
            continue
        manifest_sha = str(payload.get("manifest_payload_sha256") or "")
        if manifest_sha and manifest_hash(payload) != manifest_sha:
            continue
        database_sha = str(payload.get("database_sha256") or "")
        if len(database_sha) != 64:
            continue
        reason_priority = 1 if reason == "prestart" else 0
        candidates.append(((reason_priority, created_ts), {
            "database_path": str(database_path),
            "manifest_path": str(manifest_path.resolve()),
            "database_file": database_name,
            "database_size_bytes": database_path.stat().st_size,
            "database_sha256": database_sha,
            "created_ts": created_ts,
            "age_sec": age_sec,
            "backup_id": str(payload.get("backup_id") or ""),
            "reason": reason,
            "git_commit": expected_sha,
        }))
    except (OSError, ValueError, TypeError):
        continue

if not candidates:
    raise SystemExit("no recent verified exact-SHA local backup")
selected = max(candidates, key=lambda item: item[0])[1]
print("EDE_VERIFIED_BACKUP_SELECTION=" + json.dumps(selected, sort_keys=True))
'''
    command = (
        "env BACKUP_ROOT="
        + shlex.quote(str(REMOTE_LOCAL_BACKUPS))
        + " SOURCE_DB="
        + shlex.quote(str(REMOTE_DATABASE))
        + " EXPECTED_SHA="
        + shlex.quote(expected_sha)
        + " MAX_AGE_SECONDS="
        + shlex.quote(str(MAX_EXACT_BACKUP_AGE_SECONDS))
        + " python3 - <<'REMOTE'\n"
        + selector
        + "\nREMOTE"
    )
    output = _exec(client, command, timeout=30)
    prefix = "EDE_VERIFIED_BACKUP_SELECTION="
    lines = [line for line in output.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        raise RuntimeError("remote backup selector returned no unique selection")
    selected = json.loads(lines[0][len(prefix):])
    if not isinstance(selected, dict):
        raise RuntimeError("remote backup selection must be an object")
    return selected


def install_historical_bundle(args: argparse.Namespace) -> int:
    """Upload and install one exact-run official bundle with channel retries."""
    if not str(args.run_id).isdigit() or not str(args.acceptance_run_id).isdigit():
        raise ValueError("run IDs must be numeric")
    source = pathlib.Path(args.input)
    if not source.is_file():
        raise FileNotFoundError(str(source))
    remote = pathlib.PurePosixPath(f"/tmp/historical-macro-{args.run_id}.json")
    temporary = pathlib.PurePosixPath(str(remote) + ".upload")

    def operation(client) -> None:
        _verify_sha(client, args.expected_sha)
        sftp = client.open_sftp()
        try:
            sftp.put(str(source), str(temporary))
        finally:
            sftp.close()
        _exec(client, f"mv -f -- {shlex.quote(str(temporary))} {shlex.quote(str(remote))}")
        command = " ".join([
            shlex.quote(str(REMOTE_PYTHON)),
            shlex.quote(str(REMOTE_HISTORICAL_INSTALLER)),
            "--input", shlex.quote(str(remote)),
            "--destination", shlex.quote(str(REMOTE_HISTORICAL_BUNDLE)),
            "--expected-sha", shlex.quote(args.expected_sha),
            "--acceptance-run-id", shlex.quote(args.acceptance_run_id),
        ])
        try:
            _exec(client, command, timeout=180)
        finally:
            _exec(
                client,
                f"rm -f -- {shlex.quote(str(remote))} {shlex.quote(str(temporary))}",
            )

    _retry_ssh_operation(args.password, operation)
    print("EDE_HISTORICAL_BUNDLE_INSTALLED=1")
    return 0


def cleanup_snapshot(args: argparse.Namespace) -> int:
    """Remove only the exact run-scoped remote snapshot, retrying SSH resets."""
    if not str(args.run_id).isdigit():
        raise ValueError("run id must be numeric")
    snapshot = pathlib.PurePosixPath(
        f"/tmp/seiltanzer-ede-source-{args.run_id}.sqlite3"
    )
    paths = [str(snapshot), f"{snapshot}-wal", f"{snapshot}-shm"]

    def operation(client) -> None:
        quoted = " ".join(shlex.quote(path) for path in paths)
        checks = " && ".join(
            f"test ! -e {shlex.quote(path)}" for path in paths
        )
        _exec(client, f"rm -f -- {quoted} && {checks}")

    _retry_ssh_operation(args.password, operation)
    print("EDE_EXACT_REMOTE_SNAPSHOT_CLEAN=1")
    return 0


def snapshot(args: argparse.Namespace) -> int:
    output = pathlib.Path(args.output_db)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output = output.with_name(output.name + ".manifest.json")
    selection_output = output.with_name(output.name + ".selection.json")
    exact_run = bool(args.require_acceptance_marker)
    if exact_run and not str(args.acceptance_run_id or "").strip():
        raise ValueError(
            "--acceptance-run-id is required with --require-acceptance-marker"
        )

    client = _connect(args.password)
    primary_error: BaseException | None = None
    try:
        _verify_sha(client, args.expected_sha)

        if exact_run:
            _exec(
                client,
                " ".join(
                    [
                        shlex.quote(str(REMOTE_PYTHON)),
                        shlex.quote(str(REMOTE_ORCHESTRATOR)),
                        "wait-marker",
                        "--stage",
                        "ede-inventory",
                        "--acceptance-run-id",
                        shlex.quote(args.acceptance_run_id),
                        "--expected-sha",
                        shlex.quote(args.expected_sha),
                        "--timeout-seconds",
                        "2400",
                        "--poll-seconds",
                        "5",
                    ]
                ),
                timeout=2450,
            )
            _verify_sha(client, args.expected_sha)
            _exec(
                client,
                " ".join(
                    [
                        shlex.quote(str(REMOTE_PYTHON)),
                        shlex.quote(str(REMOTE_ORCHESTRATOR)),
                        "validate-gate",
                        "--acceptance-run-id",
                        shlex.quote(args.acceptance_run_id),
                        "--expected-sha",
                        shlex.quote(args.expected_sha),
                    ]
                ),
            )

        selected = _select_remote_exact_backup(client, expected_sha=args.expected_sha)
        output.unlink(missing_ok=True)
        manifest_output.unlink(missing_ok=True)
        progress_state = {"bucket": -1}

        def transfer_progress(transferred: int, total: int) -> None:
            percent = (
                100
                if total <= 0
                else int(max(0, min(100, transferred * 100 // total)))
            )
            bucket = percent // 10
            if bucket > progress_state["bucket"]:
                progress_state["bucket"] = bucket
                print(
                    "EDE_VERIFIED_BACKUP_TRANSFER_PROGRESS "
                    f"percent={percent} transferred_bytes={transferred} "
                    f"total_bytes={total}",
                    flush=True,
                )

        sftp = client.open_sftp()
        try:
            # Pin the tiny verified manifest locally before the multi-GiB copy.
            # Backup retention may remove the remote pair while an already-open
            # SFTP database handle is still transferring for many minutes.
            sftp.get(str(selected["manifest_path"]), str(manifest_output))
            sftp.get(
                str(selected["database_path"]),
                str(output),
                callback=transfer_progress,
            )
        finally:
            sftp.close()
        manifest = _verify_local_exact_backup(
            output, manifest_output, expected_sha=args.expected_sha
        )
        backup_reason = str(manifest["reason"])
        snapshot_source = (
            "DEPLOY_PRESTART_VERIFIED_LOCAL_BACKUP"
            if backup_reason == "prestart"
            else "SCHEDULED_VERIFIED_LOCAL_BACKUP"
        )
        selection_output.write_text(
            json.dumps(
                {
                    "source": snapshot_source,
                    "expected_sha": args.expected_sha,
                    "backup_id": manifest.get("backup_id"),
                    "backup_reason": backup_reason,
                    "cutoff_ts": float(manifest["created_ts"]),
                    "selected_age_sec": float(selected["age_sec"]),
                    "max_age_sec": MAX_EXACT_BACKUP_AGE_SECONDS,
                    "database_sha256": manifest["database_sha256"],
                    "production_authority": False,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        _wait_for_post_transfer_recovery()
        _probe_api(client)
        print(f"EDE_OFFLOAD_SNAPSHOT_SOURCE={snapshot_source}")
        print(f"EDE_OFFLOAD_SNAPSHOT_CUTOFF_TS={float(manifest['created_ts']):.6f}")
        print(f"EDE_OFFLOAD_SNAPSHOT_AGE_SEC={float(selected['age_sec']):.3f}")
        print(f"EDE_OFFLOAD_SNAPSHOT_BYTES={output.stat().st_size}")
    except BaseException as exc:
        primary_error = exc
    finally:
        client.close()

    release_error: BaseException | None = None
    if exact_run:
        try:
            # The gate is released before any CPU-heavy research begins.
            _release_gate(
                args.password,
                acceptance_run_id=args.acceptance_run_id,
                expected_sha=args.expected_sha,
            )
            print("EDE_OFFLOAD_GATE_RELEASED=1")
        except BaseException as exc:
            release_error = exc

    if primary_error is not None:
        if release_error is not None:
            raise RuntimeError(
                f"snapshot failed ({primary_error}); gate release also failed "
                f"({release_error})"
            ) from primary_error
        raise primary_error
    if release_error is not None:
        raise release_error
    return 0


def fetch_ledgers(args: argparse.Namespace) -> int:
    output = pathlib.Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    client = _connect(args.password)
    try:
        _verify_sha(client, args.expected_sha)
        sftp = client.open_sftp()
        try:
            for name in LEDGER_NAMES:
                remote = str(REMOTE_RESEARCH / name)
                local = output / name
                try:
                    sftp.stat(remote)
                except OSError:
                    print(f"serialized ledger absent, starting empty: {name}")
                    continue
                sftp.get(remote, str(local))
                print(f"downloaded serialized ledger {name} bytes={local.stat().st_size}")
        finally:
            sftp.close()
    finally:
        client.close()
    return 0


def publish(args: argparse.Namespace) -> int:
    output = pathlib.Path(args.output_dir)
    for name in OUTPUT_NAMES:
        path = output / name
        if not path.is_file():
            raise RuntimeError(f"missing EDE output {name}")

    client = _connect(args.password)
    try:
        _verify_sha(client, args.expected_sha)
        _probe_api(client)
        _exec(client, f"mkdir -p {REMOTE_RESEARCH}")

        sftp = client.open_sftp()
        try:
            for name in OUTPUT_NAMES:
                local = output / name
                remote = str(REMOTE_RESEARCH / name)
                temporary = f"{remote}.tmp-{args.run_id}"
                sftp.put(str(local), temporary)
                try:
                    sftp.posix_rename(temporary, remote)
                except OSError:
                    # OpenSSH normally supports posix-rename. Retain a bounded
                    # compatibility fallback without executing research remotely.
                    try:
                        sftp.remove(remote)
                    except OSError:
                        pass
                    sftp.rename(temporary, remote)
                print(f"published {name} bytes={local.stat().st_size}")
        finally:
            sftp.close()

        _probe_api(client)
        print("EDE_OFFLOAD_PUBLISH_OK=1")
    finally:
        client.close()
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot")
    snap.add_argument("--password", required=True)
    snap.add_argument("--expected-sha", required=True)
    snap.add_argument("--acceptance-run-id")
    snap.add_argument("--require-acceptance-marker", action="store_true")
    snap.add_argument("--run-id", required=True)
    snap.add_argument("--output-db", required=True)
    snap.set_defaults(func=snapshot)

    install = sub.add_parser("install-historical-bundle")
    install.add_argument("--password", required=True)
    install.add_argument("--expected-sha", required=True)
    install.add_argument("--acceptance-run-id", required=True)
    install.add_argument("--run-id", required=True)
    install.add_argument("--input", required=True)
    install.set_defaults(func=install_historical_bundle)

    cleanup = sub.add_parser("cleanup-snapshot")
    cleanup.add_argument("--password", required=True)
    cleanup.add_argument("--run-id", required=True)
    cleanup.set_defaults(func=cleanup_snapshot)

    ledgers = sub.add_parser("fetch-ledgers")
    ledgers.add_argument("--password", required=True)
    ledgers.add_argument("--expected-sha", required=True)
    ledgers.add_argument("--output-dir", required=True)
    ledgers.set_defaults(func=fetch_ledgers)

    pub = sub.add_parser("publish")
    pub.add_argument("--password", required=True)
    pub.add_argument("--expected-sha", required=True)
    pub.add_argument("--output-dir", required=True)
    pub.add_argument("--run-id", required=True)
    pub.set_defaults(func=publish)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
