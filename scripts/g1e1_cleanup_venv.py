#!/usr/bin/env python3
"""CLI wrapper for the bounded Seiltanzer venv cleanup contract."""
from __future__ import annotations

import argparse
import json
import site
from pathlib import Path

from seiltanzer.maintenance.venv_cleanup import cleanup


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
