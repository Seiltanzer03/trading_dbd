#!/usr/bin/env python3
"""Safely remove only known malformed Seiltanzer pip remnants.

Old interrupted/root-owned editable installs left names such as ``~1iltanzer``
and ``~=2ltanzer-0.1.0.dist-info``.  They cause pip to emit hundreds of
``Ignoring invalid distribution`` warnings.  This utility deliberately refuses
anything outside the selected site-packages directory and anything that does not
match the narrow malformed-Seiltanzer pattern.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import site
from pathlib import Path


CONTRACT_VERSION = "g1e1-venv-cleanup-v1"
# pip's interrupted rename normally replaces the first character(s) of
# ``seiltanzer`` with '~' plus punctuation/digits. Never match normal packages.
_MALFORMED = re.compile(r"^~[^/\\]*ltanzer(?:[-.].*)?$", re.IGNORECASE)


def discover(site_packages: Path) -> list[Path]:
    root = site_packages.resolve()
    if not root.is_dir():
        return []
    out = []
    for child in root.iterdir():
        if not _MALFORMED.match(child.name):
            continue
        # Symlinks are not followed or removed by this cleanup contract.
        if child.is_symlink():
            continue
        try:
            child.resolve().relative_to(root)
        except ValueError:
            continue
        out.append(child)
    return sorted(out, key=lambda p: p.name)


def cleanup(site_packages: Path, *, apply: bool = False) -> dict:
    root = site_packages.resolve()
    candidates = discover(root)
    removed: list[str] = []
    failed: list[dict] = []
    if apply:
        for path in candidates:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed.append(path.name)
            except OSError as exc:
                failed.append({"name": path.name, "error": str(exc)})
    remaining = [p.name for p in discover(root)]
    return {
        "contract_version": CONTRACT_VERSION,
        "site_packages": str(root),
        "apply": bool(apply),
        "candidate_n": len(candidates),
        "candidates": [p.name for p in candidates],
        "removed_n": len(removed),
        "removed": removed,
        "failed": failed,
        "remaining_n": len(remaining),
        "remaining": remaining,
        "clean": len(remaining) == 0,
    }


def _default_site_packages() -> Path:
    candidates = [Path(p) for p in site.getsitepackages()]
    if not candidates:
        raise RuntimeError("site-packages directory not found")
    return candidates[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site-packages", type=Path, default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--require-clean", action="store_true")
    args = ap.parse_args()
    result = cleanup(args.site_packages or _default_site_packages(), apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.require_clean and not result["clean"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
