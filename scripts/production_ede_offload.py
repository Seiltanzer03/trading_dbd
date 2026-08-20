#!/usr/bin/env python3
"""Offload EDE research from the production VPS onto a CI runner.

The production server is used only for:
1. exact-SHA/acceptance-marker validation,
2. a low-priority immutable SQLite snapshot,
3. small research-ledger/result transfers,
4. fail-closed API probes.

The CPU-heavy EDE search itself must never run on production.
"""
from __future__ import annotations

import argparse
import pathlib
import shlex
import socket
import sqlite3
import textwrap
import time
from typing import Any

HOST = "94.241.171.182"
REMOTE_ROOT = pathlib.PurePosixPath("/opt/seiltanzer")
REMOTE_RESEARCH = REMOTE_ROOT / "data/research"
REMOTE_DATABASE = REMOTE_ROOT / "data/trades.db"
REMOTE_PYTHON = REMOTE_ROOT / ".venv/bin/python"
REMOTE_ORCHESTRATOR = REMOTE_ROOT / "scripts/production_research_acceptance.py"
API_PROBE_MAX_TIME_SECONDS = 3
API_PROBE_ATTEMPTS = 3
API_PROBE_RETRY_DELAY_SECONDS = 2.0
SSH_KEEPALIVE_SECONDS = 30

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


def snapshot(args: argparse.Namespace) -> int:
    output = pathlib.Path(args.output_db)
    output.parent.mkdir(parents=True, exist_ok=True)
    remote_snapshot = f"/tmp/seiltanzer-ede-source-{args.run_id}.sqlite3"
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

        # Keep the one remaining DB operation gentle: the runner copies pages from
        # a low-priority online SQLite backup instead of executing EDE on the VPS.
        snapshot_script = textwrap.dedent(
            r"""
            import os
            import pathlib
            import sqlite3

            source = pathlib.Path("/opt/seiltanzer/data/trades.db")
            destination = pathlib.Path(os.environ["REMOTE_SNAPSHOT"])
            destination.unlink(missing_ok=True)

            src = sqlite3.connect(
                f"file:{source.resolve()}?mode=ro", uri=True, timeout=30.0
            )
            src.execute("PRAGMA query_only=ON")
            src.execute("PRAGMA busy_timeout=30000")
            dst = sqlite3.connect(destination)
            progress_state = {"bucket": -1}

            def report_backup_progress(status, remaining, total):
                percent = 100 if total <= 0 else int(
                    max(0, min(100, ((total - remaining) * 100) // total))
                )
                bucket = percent // 10
                if bucket > progress_state["bucket"]:
                    progress_state["bucket"] = bucket
                    print(
                        "EDE_REMOTE_SNAPSHOT_PROGRESS "
                        f"percent={percent} remaining_pages={remaining} "
                        f"total_pages={total} sqlite_status={status}",
                        flush=True,
                    )

            try:
                src.backup(
                    dst,
                    pages=256,
                    progress=report_backup_progress,
                    sleep=0.05,
                )
                dst.commit()
            finally:
                dst.close()
                src.close()

            check = sqlite3.connect(
                f"file:{destination.resolve()}?mode=ro", uri=True, timeout=30.0
            )
            try:
                assert check.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            finally:
                check.close()
            """
        )
        command = (
            "env REMOTE_SNAPSHOT="
            + shlex.quote(remote_snapshot)
            + " ionice -c2 -n7 nice -n 15 python3 - <<'REMOTE'\n"
            + snapshot_script
            + "\nREMOTE"
        )
        _exec(client, command, timeout=900)
        _probe_api(client)

        sftp = client.open_sftp()
        try:
            sftp.get(remote_snapshot, str(output))
        finally:
            sftp.close()
        _local_quick_check(output)
        print(f"EDE_OFFLOAD_SNAPSHOT_BYTES={output.stat().st_size}")
    except BaseException as exc:
        primary_error = exc
    finally:
        try:
            _exec(client, "rm -f " + shlex.quote(remote_snapshot))
        except Exception as exc:
            print(f"snapshot cleanup warning: {exc}")
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
