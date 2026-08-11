"""Operator CLI for verified Seiltanzer backup/restore drills.

Examples:
  python -m seiltanzer.storage_cli --data-dir /opt/seiltanzer/data status
  python -m seiltanzer.storage_cli --data-dir /opt/seiltanzer/data backup
  python -m seiltanzer.storage_cli verify BACKUP.sqlite3 BACKUP.manifest.json
  python -m seiltanzer.storage_cli --data-dir /opt/seiltanzer/data restore BACKUP.sqlite3 BACKUP.manifest.json

The restore command intentionally does not stop/start systemd. Operators must stop
``seiltanzer`` first; the previous database is preserved by default.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Settings
from .storage_refinement import install_storage_refinement
from .storage_runtime import StorageManager, _read_json, _sha256, _sqlite_integrity


def main() -> None:
    install_storage_refinement()
    parser = argparse.ArgumentParser(prog="seiltanzer-storage")
    parser.add_argument("--data-dir", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    backup = sub.add_parser("backup")
    backup.add_argument("--offhost", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("backup_db")
    verify.add_argument("manifest")
    restore = sub.add_parser("restore")
    restore.add_argument("backup_db")
    restore.add_argument("manifest")
    restore.add_argument("--no-preserve", action="store_true")
    args = parser.parse_args()

    settings = Settings(data_dir=args.data_dir)
    manager = StorageManager(settings)

    if args.command == "status":
        print(json.dumps(manager.status(), ensure_ascii=False, indent=2))
        return
    if args.command == "backup":
        kind = "offhost" if args.offhost else "local"
        result = manager.create_backup(kind=kind, reason="manual_cli")
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
        return
    if args.command == "verify":
        db = Path(args.backup_db)
        manifest = _read_json(Path(args.manifest))
        ok, detail = _sqlite_integrity(db, full=True)
        expected = (manifest or {}).get("database_sha256")
        actual = _sha256(db) if db.exists() else None
        result = {
            "ok": bool(ok and expected and actual == expected),
            "sqlite_integrity": detail,
            "expected_sha256": expected,
            "actual_sha256": actual,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["ok"] else 2)
    if args.command == "restore":
        result = StorageManager.restore_verified_backup(
            backup_db=args.backup_db,
            manifest_path=args.manifest,
            destination_db=settings.trades_db,
            preserve_existing=not args.no_preserve,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
