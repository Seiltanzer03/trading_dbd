#!/usr/bin/env python3
"""Exact-smoke-run coordination helpers for production research acceptance."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seiltanzer.research_acceptance_gate import (  # noqa: E402
    DEFAULT_ACCEPTANCE_GATE_PATH,
    gate_owner_matches,
    release_acceptance_gate,
    write_acceptance_gate,
)


MARKER_DIR = Path("/var/lock")
ALLOWED_MARKER_STAGES = {"post-research", "g1m-local-edge", "ede-inventory"}


def marker_path(stage: str, smoke_run_id: str, *, marker_dir: Path = MARKER_DIR) -> Path:
    stage = str(stage or "").strip()
    run_id = str(smoke_run_id or "").strip()
    if stage not in ALLOWED_MARKER_STAGES:
        raise ValueError(f"unsupported marker stage: {stage}")
    if not run_id:
        raise ValueError("smoke_run_id is required")
    return marker_dir / f"seiltanzer-{stage}-{run_id}.done"


def write_marker(
    stage: str,
    smoke_run_id: str,
    expected_sha: str,
    *,
    marker_dir: Path = MARKER_DIR,
) -> Path:
    sha = str(expected_sha or "").strip()
    if not sha:
        raise ValueError("expected_sha is required")
    marker = marker_path(stage, smoke_run_id, marker_dir=marker_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_name(f"{marker.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            handle.write(sha + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, marker)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return marker


def wait_marker(
    stage: str,
    smoke_run_id: str,
    expected_sha: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
    marker_dir: Path = MARKER_DIR,
) -> Path:
    expected = str(expected_sha or "").strip()
    marker = marker_path(stage, smoke_run_id, marker_dir=marker_dir)
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        if marker.exists():
            actual = marker.read_text(encoding="utf-8").strip()
            if actual != expected:
                raise RuntimeError(
                    f"{stage.upper().replace('-', '_')}_SHA_MISMATCH "
                    f"marker={actual} expected={expected} path={marker}"
                )
            return marker
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"{stage.upper().replace('-', '_')}_COMPLETION_TIMEOUT path={marker}"
            )
        remaining = max(0.0, deadline - time.monotonic())
        print(
            f"Waiting for {stage} completion smoke_run_id={smoke_run_id} "
            f"expected_sha={expected} marker={marker} remaining_sec={remaining:.1f}",
            flush=True,
        )
        time.sleep(min(max(0.1, float(poll_seconds)), remaining or 0.1))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("acquire-gate", "validate-gate", "release-gate", "write-marker", "wait-marker"),
    )
    parser.add_argument("--smoke-run-id", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--stage", choices=sorted(ALLOWED_MARKER_STAGES))
    parser.add_argument("--ttl-seconds", type=float, default=2 * 60 * 60)
    parser.add_argument("--timeout-seconds", type=float, default=15 * 60)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--gate-path", type=Path, default=DEFAULT_ACCEPTANCE_GATE_PATH)
    parser.add_argument("--marker-dir", type=Path, default=MARKER_DIR)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.action == "acquire-gate":
        payload = write_acceptance_gate(
            args.smoke_run_id,
            args.expected_sha,
            ttl_seconds=args.ttl_seconds,
            path=args.gate_path,
        )
        print(
            "RESEARCH_ACCEPTANCE_GATE_ACQUIRED "
            f"smoke_run_id={payload['smoke_run_id']} expected_sha={payload['expected_sha']} "
            f"expires_at={payload['expires_at']:.3f} path={args.gate_path}"
        )
        return
    if args.action == "validate-gate":
        if not gate_owner_matches(
            args.smoke_run_id, args.expected_sha, path=args.gate_path
        ):
            raise SystemExit(
                "RESEARCH_ACCEPTANCE_GATE_MISMATCH_OR_EXPIRED "
                f"smoke_run_id={args.smoke_run_id} expected_sha={args.expected_sha} "
                f"path={args.gate_path}"
            )
        print(
            "RESEARCH_ACCEPTANCE_GATE_VALID "
            f"smoke_run_id={args.smoke_run_id} expected_sha={args.expected_sha}"
        )
        return
    if args.action == "release-gate":
        released = release_acceptance_gate(
            args.smoke_run_id, args.expected_sha, path=args.gate_path
        )
        print(
            f"RESEARCH_ACCEPTANCE_GATE_RELEASED={str(released).lower()} "
            f"smoke_run_id={args.smoke_run_id} expected_sha={args.expected_sha}"
        )
        return
    if args.stage is None:
        raise SystemExit("--stage is required for marker actions")
    if args.action == "write-marker":
        marker = write_marker(
            args.stage,
            args.smoke_run_id,
            args.expected_sha,
            marker_dir=args.marker_dir,
        )
        print(
            f"ACCEPTANCE_MARKER_WRITTEN stage={args.stage} "
            f"smoke_run_id={args.smoke_run_id} expected_sha={args.expected_sha} path={marker}"
        )
        return
    marker = wait_marker(
        args.stage,
        args.smoke_run_id,
        args.expected_sha,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
        marker_dir=args.marker_dir,
    )
    print(
        f"ACCEPTANCE_MARKER_READY stage={args.stage} "
        f"smoke_run_id={args.smoke_run_id} expected_sha={args.expected_sha} path={marker}"
    )


if __name__ == "__main__":
    main()
