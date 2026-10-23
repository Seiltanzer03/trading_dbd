#!/usr/bin/env python3
"""Publish one small off-host active-edge report to the production research dir."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shlex

from production_ede_offload import (
    REMOTE_RESEARCH,
    _connect,
    _exec,
    _probe_api,
    _verify_sha,
)


ALLOWED_NAMES = {
    "fomc_prospective_latest.json",
    "edge_family_sources_latest.json",
    "mathematical_edge_latest.json",
    "active_structured_15m_latest.json",
    "active_structured_30m_latest.json",
    "active_structured_60m_latest.json",
    "active_structured_120m_latest.json",
    "active_structured_240m_latest.json",
    "active_ml_latest.json",
}
MAX_BYTES = 8_000_000
PUBLICATION_CONTRACT_VERSION = "active-edge-exact-sha-publication-v1"


def _verify_uploaded_report(sftp, temporary, size, expected_digest):
    """Read back bounded staged bytes before the atomic publication boundary."""
    digest = hashlib.sha256()
    received = 0
    with sftp.open(temporary, 'rb') as handle:
        while True:
            block = handle.read(min(65536, size + 1 - received))
            if not block:
                break
            received += len(block)
            if received > size:
                raise RuntimeError('research report upload exceeds expected bytes')
            digest.update(block)
    if received != size or digest.hexdigest() != expected_digest:
        raise RuntimeError('research report upload size or SHA256 mismatch')


def _stamp_report(source: pathlib.Path, *, expected_sha: str, run_id: str) -> int:
    """Bind the published bytes to the exact production code generation."""
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
    limit = (950_000 if payload.get("contract_version") == "fomc-prospective-offhost-capture-v1"
             else 1_000_000 if payload.get("contract_version") == "edge-family-source-bundle-v1" else MAX_BYTES)
    if size <= 0 or size > limit:
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
    if re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.run_id) is None:
        raise RuntimeError('run_id must be a bounded publication identifier')

    source = pathlib.Path(args.input).resolve()
    if not source.is_file():
        raise RuntimeError(f"active edge report missing: {source}")
    size = _stamp_report(
        source,
        expected_sha=args.expected_sha,
        run_id=args.run_id,
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    client = _connect(args.password)
    try:
        _verify_sha(client, args.expected_sha)
        _probe_api(client)
        _exec(client, f"mkdir -p {shlex.quote(str(REMOTE_RESEARCH))}")
        if args.remote_name == 'fomc_prospective_latest.json':
            from fomc_scheduler_transport import deliver_with_client
            receipt = deliver_with_client(client, source, sha=args.expected_sha, run_id=args.run_id)
            _probe_api(client)
            print(json.dumps(receipt, sort_keys=True))
            print(f"ACTIVE_EDGE_PUBLISHED={args.remote_name} bytes={size} "
                  f"published_for_sha={str(args.expected_sha).lower()}")
            return 0
        remote = str(REMOTE_RESEARCH / args.remote_name)
        temporary = f"{remote}.tmp-{args.run_id}"
        sftp = client.open_sftp()
        try:
            sftp.put(str(source), temporary)
            _verify_uploaded_report(sftp, temporary, size, digest)
            _verify_sha(client, args.expected_sha)
            # Never unlink the previous usable publication to emulate replace.
            # Unsupported or failed atomic rename must leave it untouched.
            sftp.posix_rename(temporary, remote)
        finally:
            try:
                sftp.remove(temporary)
            except OSError:
                pass
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
