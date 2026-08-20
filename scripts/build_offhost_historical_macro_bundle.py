#!/usr/bin/env python3
"""Fetch canonical historical BLS/ISM/FOMC pages off production."""
from __future__ import annotations

import argparse
from pathlib import Path

from seiltanzer.macro_historical_offhost_bundle import build_bundle
from seiltanzer.macro_offhost_bundle import current_repository_sha, write_bundle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--acceptance-run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window-days", type=int, default=120)
    args = parser.parse_args()
    if current_repository_sha() != args.expected_sha:
        raise SystemExit("HISTORICAL_OFFHOST_BUILDER_SHA_MISMATCH")
    bundle = build_bundle(
        expected_sha=args.expected_sha,
        acceptance_run_id=args.acceptance_run_id,
        window_days=args.window_days,
    )
    write_bundle(args.output, bundle)
    print(
        "HISTORICAL_OFFHOST_BUNDLE_BUILT "
        f"sha={args.expected_sha} acceptance_run_id={args.acceptance_run_id} "
        f"bls_n={len(bundle['bls_records'])} ism_n={len(bundle['ism_records'])} "
        f"fomc_n={len(bundle['fomc_records'])} "
        f"error_n={len(bundle['errors'])} bundle_sha256={bundle['bundle_sha256']}"
    )


if __name__ == "__main__":
    main()
