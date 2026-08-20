#!/usr/bin/env python3
"""Validate and atomically materialize an exact-SHA official macro bundle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from seiltanzer.macro_offhost_bundle import (
    current_repository_sha,
    validate_bundle,
    write_bundle,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--acceptance-run-id", required=True)
    args = parser.parse_args()

    actual_sha = current_repository_sha()
    if actual_sha != args.expected_sha:
        raise SystemExit(
            f"OFFHOST_MACRO_INSTALLER_SHA_MISMATCH:{actual_sha}!={args.expected_sha}"
        )
    try:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        bundle = validate_bundle(
            raw,
            expected_sha=args.expected_sha,
            acceptance_run_id=args.acceptance_run_id,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"OFFHOST_MACRO_INSTALL_REJECTED:{type(exc).__name__}:{exc}") from exc
    write_bundle(args.destination, bundle)
    print(
        "OFFHOST_MACRO_BUNDLE_INSTALLED "
        f"sha={args.expected_sha} acceptance_run_id={args.acceptance_run_id} "
        f"bundle_sha256={bundle['bundle_sha256']}"
    )


if __name__ == "__main__":
    main()
