#!/usr/bin/env python3
"""Validate and atomically install an exact-SHA historical macro bundle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from seiltanzer.macro_historical_offhost_bundle import validate_bundle
from seiltanzer.macro_offhost_bundle import current_repository_sha, write_bundle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--acceptance-run-id", required=True)
    args = parser.parse_args()
    if current_repository_sha() != args.expected_sha:
        raise SystemExit("HISTORICAL_OFFHOST_INSTALLER_SHA_MISMATCH")
    try:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        bundle = validate_bundle(
            raw, expected_sha=args.expected_sha,
            acceptance_run_id=args.acceptance_run_id,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(
            f"HISTORICAL_OFFHOST_INSTALL_REJECTED:{type(exc).__name__}:{exc}"
        ) from exc
    write_bundle(args.destination, bundle)
    print(
        "HISTORICAL_OFFHOST_BUNDLE_INSTALLED "
        f"sha={args.expected_sha} acceptance_run_id={args.acceptance_run_id} "
        f"bundle_sha256={bundle['bundle_sha256']}"
    )


if __name__ == "__main__":
    main()
