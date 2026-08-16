#!/usr/bin/env python3
"""Publish one small off-host active-edge report to the production research dir."""
from __future__ import annotations

import argparse
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
    size = source.stat().st_size
    if size <= 0 or size > MAX_BYTES:
        raise RuntimeError(f"active edge report size outside bounded contract: {size}")

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
        print(f"ACTIVE_EDGE_PUBLISHED={args.remote_name} bytes={size}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
