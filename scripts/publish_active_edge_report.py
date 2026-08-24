#!/usr/bin/env python3
"""Publish one small off-host active-edge report to the production research dir."""
from __future__ import annotations

import argparse
import json
import pathlib
import shlex

from production_ede_offload import (
    REMOTE_RESEARCH,
    _connect,
    _exec,
    _probe_api,
    _verify_sha,
)


ALLOWED_NAMES = {
    "active_structured_15m_latest.json",
    "active_structured_30m_latest.json",
    "active_structured_60m_latest.json",
    "active_structured_120m_latest.json",
    "active_structured_240m_latest.json",
    "active_ml_latest.json",
}
MAX_BYTES = 8_000_000
PUBLICATION_CONTRACT_VERSION = "active-edge-exact-sha-publication-v1"


def _stamp_report(source: pathlib.Path, *, expected_sha: str, run_id: str) -> int:
    """Bind the published bytes to the exact production code generation.

    Research math is already complete before this function runs.  The publisher
    adds provenance only, in place, so the subsequently uploaded Actions artifact
    is byte-identical to the report installed on production.
    """
    sha = str(expected_sha or "").strip().lower()
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise RuntimeError("expected_sha must be a full 40-character git SHA")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid active edge report JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("active edge report must be a JSON object")
    if payload.get("production_authority") is not False:
        raise RuntimeError("active edge report must explicitly disable production authority")
    existing = str(payload.get("published_for_sha") or "").strip().lower()
    if existing and existing != sha:
        raise RuntimeError(
            f"active edge report already bound to different SHA: {existing}")
    payload["publication_contract_version"] = PUBLICATION_CONTRACT_VERSION
    payload["published_for_sha"] = sha
    payload["publication_run_id"] = str(run_id)
    source.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    size = source.stat().st_size
    if size <= 0 or size > MAX_BYTES:
        raise RuntimeError(f"active edge report size outside bounded contract: {size}")
    return size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--password", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--remote-name", required=True, choices=sorted(ALLOWED_NAMES))
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    source = pathlib.Path(args.input).resolve()
    if not source.is_file():
        raise RuntimeError(f"active edge report missing: {source}")
    size = _stamp_report(
        source,
        expected_sha=args.expected_sha,
        run_id=args.run_id,
    )

    client = _connect(args.password)
    try:
        _verify_sha(client, args.expected_sha)
        _probe_api(client)
        _exec(client, f"mkdir -p {shlex.quote(str(REMOTE_RESEARCH))}")
        remote = str(REMOTE_RESEARCH / args.remote_name)
        temporary = f"{remote}.tmp-{args.run_id}"
        sftp = client.open_sftp()
        try:
            sftp.put(str(source), temporary)
            try:
                sftp.posix_rename(temporary, remote)
            except OSError:
                try:
                    sftp.remove(remote)
                except OSError:
                    pass
                sftp.rename(temporary, remote)
        finally:
            sftp.close()
        _probe_api(client)
        print(
            f"ACTIVE_EDGE_PUBLISHED={args.remote_name} bytes={size} "
            f"published_for_sha={str(args.expected_sha).lower()}"
        )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
