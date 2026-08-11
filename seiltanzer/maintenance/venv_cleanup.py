"""Bounded cleanup for malformed Seiltanzer editable-install remnants."""
from __future__ import annotations

import re
import shutil
import site
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


def remediate_current_environment() -> dict:
    """Try the same narrow cleanup under the service process identity.

    Production's legacy root-owned deployment created the malformed entries, so
    the unprivileged Actions runner can enumerate but not remove them. The service
    process is the correct existing ownership boundary to perform this one-time
    remediation. Failure is reported but never broadens the deletion contract or
    prevents the trading service from starting.
    """
    roots = [Path(p) for p in site.getsitepackages()]
    if not roots:
        return {
            "contract_version": CONTRACT_VERSION,
            "apply": True,
            "clean": True,
            "candidate_n": 0,
            "removed_n": 0,
            "remaining_n": 0,
            "failed": [],
            "site_packages": None,
        }
    return cleanup(roots[0], apply=True)
