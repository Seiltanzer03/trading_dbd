"""Bounded cooperative gate for production research acceptance.

The production service keeps collecting market data and serving decisions while
research maintenance is temporarily deferred. A post-smoke acceptance run owns
one short-lived JSON lease. The worker is allowed to finish (or perform) the
required first cycle of the current service process, then it defers *new* heavy
research cycles until the acceptance chain releases the lease.

This module carries no model, edge, promotion or production-decision authority.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any, Iterator


ACCEPTANCE_GATE_VERSION = "production-research-acceptance-gate-v1"
DEFAULT_ACCEPTANCE_GATE_PATH = Path(
    os.environ.get(
        "SEILTANZER_RESEARCH_ACCEPTANCE_GATE",
        "/var/lock/seiltanzer-research-acceptance.json",
    )
)
# Heavy EDE compute is offloaded from production after the immutable snapshot.
# The lease therefore only needs to cover the bounded post-research -> G1M ->
# inventory synchronization path. Two hours is a conservative crash-safety
# ceiling; normal workflows release it explicitly much earlier.
MAX_ACCEPTANCE_GATE_TTL_SEC = 2 * 60 * 60


def _clean_owner(smoke_run_id: str, expected_sha: str) -> tuple[str, str]:
    run_id = str(smoke_run_id or "").strip()
    sha = str(expected_sha or "").strip()
    if not run_id:
        raise ValueError("smoke_run_id is required")
    if not sha:
        raise ValueError("expected_sha is required")
    return run_id, sha


@contextlib.contextmanager
def _exclusive_gate_update(path: Path) -> Iterator[None]:
    """Serialize cross-process lease replacement/release without blocking reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def read_acceptance_gate(
    path: Path | str = DEFAULT_ACCEPTANCE_GATE_PATH,
) -> dict[str, Any] | None:
    gate_path = Path(path)
    try:
        payload = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        created_at = float(payload["created_at"])
        expires_at = float(payload["expires_at"])
        smoke_run_id, expected_sha = _clean_owner(
            str(payload["smoke_run_id"]), str(payload["expected_sha"])
        )
    except (KeyError, TypeError, ValueError):
        return None
    if payload.get("contract_version") != ACCEPTANCE_GATE_VERSION:
        return None
    if expires_at <= created_at:
        return None
    return {
        **payload,
        "created_at": created_at,
        "expires_at": expires_at,
        "smoke_run_id": smoke_run_id,
        "expected_sha": expected_sha,
    }


def write_acceptance_gate(
    smoke_run_id: str,
    expected_sha: str,
    *,
    ttl_seconds: float = MAX_ACCEPTANCE_GATE_TTL_SEC,
    path: Path | str = DEFAULT_ACCEPTANCE_GATE_PATH,
    now: float | None = None,
) -> dict[str, Any]:
    run_id, sha = _clean_owner(smoke_run_id, expected_sha)
    ttl = float(ttl_seconds)
    if not (0.0 < ttl <= MAX_ACCEPTANCE_GATE_TTL_SEC):
        raise ValueError(
            f"ttl_seconds must be in (0,{MAX_ACCEPTANCE_GATE_TTL_SEC}]"
        )
    created_at = float(time.time() if now is None else now)
    payload = {
        "contract_version": ACCEPTANCE_GATE_VERSION,
        "smoke_run_id": run_id,
        "expected_sha": sha,
        "created_at": created_at,
        "expires_at": created_at + ttl,
    }
    gate_path = Path(path)
    with _exclusive_gate_update(gate_path):
        tmp = gate_path.with_name(
            f"{gate_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        try:
            with tmp.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            # Atomic replace deliberately lets a newer exact-smoke run supersede
            # an older lease. The ownership lock makes release-vs-replace atomic,
            # so an older cleanup cannot unlink a newer run's lease.
            os.replace(tmp, gate_path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
    return payload


def gate_owner_matches(
    smoke_run_id: str,
    expected_sha: str,
    *,
    path: Path | str = DEFAULT_ACCEPTANCE_GATE_PATH,
    now: float | None = None,
) -> bool:
    run_id, sha = _clean_owner(smoke_run_id, expected_sha)
    gate_path = Path(path)
    with _exclusive_gate_update(gate_path):
        payload = read_acceptance_gate(gate_path)
        if payload is None:
            return False
        current = float(time.time() if now is None else now)
        return (
            payload["smoke_run_id"] == run_id
            and payload["expected_sha"] == sha
            and payload["expires_at"] > current
        )


def release_acceptance_gate(
    smoke_run_id: str,
    expected_sha: str,
    *,
    path: Path | str = DEFAULT_ACCEPTANCE_GATE_PATH,
) -> bool:
    run_id, sha = _clean_owner(smoke_run_id, expected_sha)
    gate_path = Path(path)
    with _exclusive_gate_update(gate_path):
        payload = read_acceptance_gate(gate_path)
        if payload is None:
            return False
        if payload["smoke_run_id"] != run_id or payload["expected_sha"] != sha:
            return False
        try:
            gate_path.unlink()
        except FileNotFoundError:
            return False
        return True


def worker_acceptance_gate_state(
    *,
    process_started_ts: float,
    last_finished_ts: float | None,
    path: Path | str = DEFAULT_ACCEPTANCE_GATE_PATH,
    now: float | None = None,
) -> dict[str, Any]:
    """Return whether a current-process worker must defer its next heavy cycle.

    A lease created before this service process is stale for this generation and
    is ignored. A current lease never aborts a cycle already in progress. If the
    process has not completed any research cycle yet, one required cycle remains
    allowed; after that completion all new cycles pause until release/expiry.
    """
    current = float(time.time() if now is None else now)
    payload = read_acceptance_gate(path)
    base = {
        "active": False,
        "pause": False,
        "reason": "NO_ACTIVE_ACCEPTANCE_GATE",
        "smoke_run_id": None,
        "expected_sha": None,
        "expires_at": None,
    }
    if payload is None:
        return base
    if payload["expires_at"] <= current:
        return {**base, "reason": "ACCEPTANCE_GATE_EXPIRED"}
    # A deployment restarts the service. A lease from the previous service
    # generation must never suppress the new generation's required first cycle.
    if payload["created_at"] < float(process_started_ts) - 1.0:
        return {**base, "reason": "STALE_SERVICE_GENERATION_GATE"}

    common = {
        "active": True,
        "smoke_run_id": payload["smoke_run_id"],
        "expected_sha": payload["expected_sha"],
        "expires_at": payload["expires_at"],
    }
    if last_finished_ts is None:
        return {
            **common,
            "pause": False,
            "reason": "REQUIRED_WORKER_CYCLE_PENDING",
        }
    return {
        **common,
        "pause": True,
        "reason": "PRODUCTION_ACCEPTANCE_ACTIVE",
    }
