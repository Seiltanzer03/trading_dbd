#!/usr/bin/env python3
"""Build actual public edge-family input bundle off-host (no production writes)."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from seiltanzer.edge_family_sources import DEFAULT_INSTRUMENTS, build_bundle


def read_optional(path: Path | None) -> dict:
    if path is None:
        return {}
    if path.stat().st_size > 8_000_000:
        raise ValueError("INPUT_BUNDLE_TOO_LARGE")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("INPUT_BUNDLE_NOT_OBJECT")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instruments", default=",".join(DEFAULT_INSTRUMENTS))
    parser.add_argument("--existing-context", type=Path)
    parser.add_argument("--previous-bundle", type=Path)
    args = parser.parse_args()
    bundle = build_bundle(instruments=args.instruments.split(","),
                          existing=read_optional(args.existing_context),
                          previous=read_optional(args.previous_bundle))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=args.output.parent, encoding="utf-8", delete=False) as handle:
        json.dump(bundle, handle, sort_keys=True, separators=(",", ":"), allow_nan=False)
        temp_path = handle.name
    os.replace(temp_path, args.output)
    print(json.dumps({"contract": bundle["contract_version"], "instruments": len(bundle["instruments"]),
                      "requests": bundle["collection_limits"]["requests"], "errors": bundle["errors"],
                      "production_authority": False}, sort_keys=True))


if __name__ == "__main__":
    main()
