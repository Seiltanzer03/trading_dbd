"""Bounded cleanup for malformed Seiltanzer editable-install remnants."""
from __future__ import annotations

import re
import shutil
from pathlib import Path


CONTRACT_VERSION = "g1e1-venv-cleanup-v1"
_MALFORMED = re.compile(r"^~[^/\\]*ltanzer(?:[-.].*)?$", re.IGNORECASE)


def discover(site_packages: Path) -> list[Path]:
    root = Path(site_packages).resolve()
    if not root.is_dir():
        return []
    out: list[Path] = []
    for child in root.iterdir():
        if not _MALFORMED.match(child.name):
            continue
        if child.is_symlink():
            continue
        try:
            child.resolve().relative_to(root)
        except ValueError:
            continue
        out.append(child)
    return sorted(out, key=lambda p: p.name)


def cleanup(site_packages: Path, *, apply: bool = False) -> dict:
    root = Path(site_packages).resolve()
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
