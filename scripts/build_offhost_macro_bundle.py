#!/usr/bin/env python3
"""Fetch official numeric macro data off-host with the exact deployed parser."""
from __future__ import annotations

import argparse
from pathlib import Path

from seiltanzer.macro_offhost_bundle import (
    build_bundle,
    current_repository_sha,
    write_bundle,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--acceptance-run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    actual_sha = current_repository_sha()
    if actual_sha != args.expected_sha:
        raise SystemExit(
            f"OFFHOST_MACRO_BUILDER_SHA_MISMATCH:{actual_sha}!={args.expected_sha}"
        )
    bundle = build_bundle(
        expected_sha=args.expected_sha,
        acceptance_run_id=args.acceptance_run_id,
    )
    write_bundle(args.output, bundle)
    periods = ",".join(
        f"{family}={row['period']}" for family, row in bundle["releases"].items()
    )
    print(
        "OFFHOST_MACRO_BUNDLE_BUILT "
        f"sha={args.expected_sha} acceptance_run_id={args.acceptance_run_id} "
        f"bundle_sha256={bundle['bundle_sha256']} {periods}"
    )


if __name__ == "__main__":
    main()
