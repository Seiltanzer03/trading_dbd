"""Crash-safe persistence, verified backups and recovery for Seiltanzer.

Phase G.1E-0 makes the accumulated research/trade history an explicit durable
asset.  RAM remains cache only; ``trades.db`` is the local source of truth and a
verified copy outside the live database is the disaster-recovery source.
"""
from __future__ import annotations

import asyncio
import contextlib
import concurrent.futures
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STORAGE_CONTRACT_VERSION = "seiltanzer-storage-v1"
BACKUP_CONTRACT_VERSION = "seiltanzer-backup-v1"
RECOVERY_CONTRACT_VERSION = "seiltanzer-recovery-v1"
RETENTION_CONTRACT_VERSION = "seiltanzer-backup-retention-v1"
LOCAL_BACKUP_INTERVAL_SEC = 15 * 60
OFFHOST_BACKUP_INTERVAL_SEC = 60 * 60

# These tables contain the economically/research relevant state that must survive
# process restarts. Missing tables are allowed for old/test databases and are
# reported as ``None`` rather than silently treated as zero.
CRITICAL_TABLES = (
    "trades",
    "account",
    "ai_verdicts",
    "decision_snapshots",
    "decision_replays",
    "human_decisions",
    "position_management_events",
    "management_decisions",
    "passive_observations",
    "q_capture_attempts",
    "g1_dataset_membership",
    "g1_dataset_cuts",
    "g1_dataset_cut_members",
    "g1c_fit_runs",
    "g1c_shadow_models",
    "g1c_shadow_predictions",
)


def _now() -> float:
    return time.time()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, sort_keys=True, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _sqlite_integrity(path: Path, *, full: bool = True) -> tuple[bool, str]:
    if not path.exists():
        return False, "database_missing"
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        pragma = "integrity_check" if full else "quick_check"
        rows = [str(row[0]) for row in conn.execute(f"PRAGMA {pragma}").fetchall()]
        ok = rows == ["ok"]
        return ok, "ok" if ok else "; ".join(rows[:20])
    except sqlite3.DatabaseError as exc:
        return False, f"sqlite_error:{exc}"
    finally:
        conn.close()


def _table_counts(path: Path) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    if not path.exists():
        return {name: None for name in CRITICAL_TABLES}
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        existing = {
            str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for name in CRITICAL_TABLES:
            if name not in existing:
                out[name] = None
                continue
            out[name] = int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
    finally:
        conn.close()
    return out


def _verify_backup_snapshot(
    path: Path,
    *,
    startup_source: Path | None = None,
) -> tuple[
    bool,
    str,
    str,
    dict[str, int | None],
    tuple[bool, str] | None,
]:
    """Run independent immutable-snapshot checks in one bounded read window.

    Once SQLite has closed the destination connection, integrity, byte identity
    and critical-table counts are independent read-only validations.  Running
    them together preserves every fail-closed assertion while avoiding three
    sequential whole-snapshot scans during production cold start.  The existing
    live ``quick_check`` may join the same window before Engine construction;
    it is never omitted or moved onto a request path.
    """
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=4 if startup_source is not None else 3,
        thread_name_prefix="backup-verify",
    ) as executor:
        integrity = executor.submit(_sqlite_integrity, path, full=True)
        digest = executor.submit(_sha256, path)
        counts = executor.submit(_table_counts, path)
        source_integrity = (
            executor.submit(_sqlite_integrity, startup_source, full=False)
            if startup_source is not None else None
        )
        ok, detail = integrity.result()
        sha = digest.result()
        table_counts = counts.result()
        source_check = (
            source_integrity.result() if source_integrity is not None else None
        )
    return ok, detail, sha, table_counts, source_check


@dataclass(frozen=True)
class BackupResult:
    backup_id: str
    kind: str
    database_path: str
    manifest_path: str
    verified: bool
    created_ts: float
    sha256: str


class StorageManager:
    """Owns persistence health, backup cadence and restart recovery metadata."""

    def __init__(self, settings, *, git_commit: str | None = None):
        self.settings = settings
        self.data_dir = Path(settings.data_dir).resolve()
        self.db_path = Path(settings.trades_db).resolve()
        self.local_dir = Path(
            os.environ.get(
                "SEILTANZER_LOCAL_BACKUP_DIR",
                str(self.data_dir / "backups" / "local"),
            )
        ).resolve()
        offhost = os.environ.get("SEILTANZER_OFFHOST_BACKUP_DIR", "").strip()
        self.offhost_dir = Path(offhost).resolve() if offhost else None
        self.marker_path = self.data_dir / ".storage_state.json"
        self.local_interval = int(os.environ.get(
            "SEILTANZER_LOCAL_BACKUP_INTERVAL_SEC", LOCAL_BACKUP_INTERVAL_SEC))
        self.offhost_interval = int(os.environ.get(
            "SEILTANZER_OFFHOST_BACKUP_INTERVAL_SEC", OFFHOST_BACKUP_INTERVAL_SEC))
        self.git_commit = git_commit or os.environ.get("GIT_COMMIT") or "unknown"
        self._lock = threading.RLock()
        self._previous_marker = _read_json(self.marker_path) or {}
        self._startup_integrity: dict[str, Any] | None = None
        self._prestart_integrity_ready = False
        self._recovery_actions: list[dict[str, Any]] = []
        self._last_error: str | None = None
        self._background_running = False
        self.local_dir.mkdir(parents=True, exist_ok=True)
        if self.offhost_dir is not None:
            self.offhost_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- setup

    @property
    def previous_shutdown(self) -> str:
        if not self._previous_marker:
            return "UNKNOWN"
        return "CLEAN" if self._previous_marker.get("clean_shutdown") is True else "UNCLEAN"

    def configure_engine_connections(self, engine) -> None:
        """Apply conservative durability pragmas to every live trades.db writer."""
        conns = []
        for owner_name in ("journal", "position", "passive"):
            owner = getattr(engine, owner_name, None)
            conn = getattr(owner, "_conn", None)
            if conn is not None and all(conn is not existing for existing in conns):
                conns.append(conn)
        for conn in conns:
            # journal_mode is database-wide; the remaining pragmas are per-connection.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=FULL")

    def mark_startup(self) -> None:
        if not self._prestart_integrity_ready:
            ok, detail = _sqlite_integrity(self.db_path, full=False)
            self._startup_integrity = {
                "checked_ts": _now(), "ok": ok, "detail": detail,
                "check_kind": "quick_check",
                "verification_scope": "post_engine_source",
                "contract_version": STORAGE_CONTRACT_VERSION,
            }
        self._prestart_integrity_ready = False
        _atomic_json(self.marker_path, {
            "storage_contract_version": STORAGE_CONTRACT_VERSION,
            "started_ts": _now(),
            "clean_shutdown": False,
            "previous_shutdown": self.previous_shutdown,
            "pid": os.getpid(),
        })

    def mark_clean_shutdown(self) -> None:
        _atomic_json(self.marker_path, {
            "storage_contract_version": STORAGE_CONTRACT_VERSION,
            "started_ts": (_read_json(self.marker_path) or {}).get("started_ts"),
            "shutdown_ts": _now(),
            "clean_shutdown": True,
            "pid": os.getpid(),
        })

    # --------------------------------------------------------------- backup

    def _backup_dir(self, kind: str) -> Path:
        if kind == "local":
            return self.local_dir
        if kind == "offhost" and self.offhost_dir is not None:
            return self.offhost_dir
        raise ValueError("off-host backup target is not configured")

    @staticmethod
    def _verified_manifests(directory: Path) -> list[dict[str, Any]]:
        manifests = []
        if not directory.exists():
            return manifests
        for path in directory.glob("*.manifest.json"):
            manifest = _read_json(path)
            if manifest and manifest.get("verified") is True:
                manifest = {**manifest, "manifest_path": str(path)}
                manifests.append(manifest)
        return sorted(manifests, key=lambda x: float(x.get("created_ts") or 0), reverse=True)

    def _last_verified(self, kind: str) -> dict[str, Any] | None:
        if kind == "offhost" and self.offhost_dir is None:
            return None
        items = self._verified_manifests(self._backup_dir(kind))
        return items[0] if items else None

    def create_backup(self, *, kind: str = "local", reason: str = "scheduled") -> BackupResult:
        """Create an online-consistent SQLite snapshot and verify it before success."""
        with self._lock:
            if not self.db_path.exists():
                raise FileNotFoundError(str(self.db_path))
            directory = self._backup_dir(kind)
            directory.mkdir(parents=True, exist_ok=True)
            created = _now()
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(created))
            backup_id = f"{stamp}-{kind}-{int(created * 1000) % 1000000:06d}"
            final_db = directory / f"{backup_id}.sqlite3"
            manifest_path = directory / f"{backup_id}.manifest.json"
            temp_db = directory / f".{backup_id}.tmp.sqlite3"
            with contextlib.suppress(FileNotFoundError):
                temp_db.unlink()

            src = sqlite3.connect(str(self.db_path), timeout=30)
            dst = sqlite3.connect(str(temp_db), timeout=30)
            try:
                src.execute("PRAGMA busy_timeout=30000")
                src.backup(dst, pages=256, sleep=0.01)
                dst.commit()
            except Exception:
                with contextlib.suppress(FileNotFoundError):
                    temp_db.unlink()
                raise
            finally:
                dst.close()
                src.close()

            startup_source = self.db_path if reason == "prestart" else None
            ok, integrity_detail, sha, counts, source_check = _verify_backup_snapshot(
                temp_db,
                startup_source=startup_source,
            )
            if not ok:
                with contextlib.suppress(FileNotFoundError):
                    temp_db.unlink()
                raise RuntimeError(f"backup integrity failed: {integrity_detail}")
            if source_check is not None and not source_check[0]:
                with contextlib.suppress(FileNotFoundError):
                    temp_db.unlink()
                raise RuntimeError(
                    f"source startup integrity failed: {source_check[1]}"
                )
            if source_check is not None:
                self._startup_integrity = {
                    "checked_ts": _now(),
                    "ok": source_check[0],
                    "detail": source_check[1],
                    "check_kind": "quick_check",
                    "verification_scope": "pre_engine_source",
                    "contract_version": STORAGE_CONTRACT_VERSION,
                }
                self._prestart_integrity_ready = True
            size = temp_db.stat().st_size
            os.replace(temp_db, final_db)
            manifest = {
                "backup_contract_version": BACKUP_CONTRACT_VERSION,
                "retention_contract_version": RETENTION_CONTRACT_VERSION,
                "backup_id": backup_id,
                "kind": kind,
                "reason": reason,
                "created_ts": created,
                "source_db": str(self.db_path),
                "database_file": final_db.name,
                "database_sha256": sha,
                "database_size_bytes": size,
                "sqlite_integrity": integrity_detail,
                "critical_table_counts": counts,
                "git_commit": self.git_commit,
                "verified": True,
                "encryption_status": "external_target_managed" if kind == "offhost" else "filesystem_permissions",
            }
            _atomic_json(manifest_path, manifest)
            self._apply_retention(kind)
            return BackupResult(
                backup_id=backup_id, kind=kind,
                database_path=str(final_db), manifest_path=str(manifest_path),
                verified=True, created_ts=created, sha256=sha,
            )

    def _apply_retention(self, kind: str) -> None:
        """Keep dense recent history plus daily/weekly/monthly recovery points."""
        directory = self._backup_dir(kind)
        manifests = self._verified_manifests(directory)
        if len(manifests) <= 24:
            return
        keep: set[str] = set()
        # Always keep the newest 24 snapshots. Older snapshots are sampled into
        # daily/weekly/monthly buckets. This is deterministic and conservative.
        for manifest in manifests[:24]:
            keep.add(str(manifest.get("backup_id")))
        daily: set[str] = set()
        weekly: set[str] = set()
        monthly: set[str] = set()
        now = _now()
        for manifest in manifests[24:]:
            ts = float(manifest.get("created_ts") or 0)
            age_days = max(0.0, (now - ts) / 86400.0)
            tm = time.gmtime(ts)
            bid = str(manifest.get("backup_id"))
            if age_days <= 14:
                key = time.strftime("%Y-%m-%d", tm)
                if key not in daily:
                    daily.add(key); keep.add(bid)
            elif age_days <= 56:
                key = time.strftime("%Y-W%W", tm)
                if key not in weekly:
                    weekly.add(key); keep.add(bid)
            elif age_days <= 365:
                key = time.strftime("%Y-%m", tm)
                if key not in monthly:
                    monthly.add(key); keep.add(bid)
        for manifest in manifests:
            bid = str(manifest.get("backup_id"))
            if bid in keep:
                continue
            mpath = Path(str(manifest["manifest_path"]))
            db_name = manifest.get("database_file")
            if db_name:
                with contextlib.suppress(FileNotFoundError):
                    (directory / str(db_name)).unlink()
            with contextlib.suppress(FileNotFoundError):
                mpath.unlink()

    def backup_if_due(self) -> dict[str, Any]:
        now = _now()
        result: dict[str, Any] = {"local": "not_due", "offhost": "not_configured"}
        local = self._last_verified("local")
        if local is None or now - float(local.get("created_ts") or 0) >= self.local_interval:
            result["local"] = self.create_backup(kind="local", reason="scheduled").backup_id
        if self.offhost_dir is not None:
            offhost = self._last_verified("offhost")
            if offhost is None or now - float(offhost.get("created_ts") or 0) >= self.offhost_interval:
                result["offhost"] = self.create_backup(kind="offhost", reason="scheduled").backup_id
            else:
                result["offhost"] = "not_due"
        return result

    # -------------------------------------------------------------- recovery

    def reconcile_economic_state(self, engine) -> list[dict[str, Any]]:
        """Repair only deterministic crash gaps between trade and position ledgers.

        Opening a trade is self-healing because ``PositionLedger.state`` creates
        the missing TRADE_OPEN event.  Closing used to be two commits; if the
        process died between them, a closed trade could retain a non-zero position
        ledger.  We close that remainder using the already-persisted final result_r.
        """
        actions: list[dict[str, Any]] = []
        for trade in engine.journal.list_trades():
            try:
                if trade.get("status") == "open":
                    before = engine.position.state(trade)
                    if int(before.get("event_count") or 0) >= 1:
                        continue
                elif trade.get("status") == "closed":
                    state = engine.position.state(trade)
                    remaining = float(state.get("remaining_position_fraction") or 0.0)
                    if remaining <= 1e-12:
                        continue
                    engine.position.terminal_exit(
                        trade,
                        event_type="MANUAL_EXIT",
                        execution_price=None,
                        execution_r=trade.get("result_r"),
                    )
                    actions.append({
                        "trade_id": int(trade["id"]),
                        "action": "RECOVER_CLOSED_POSITION_REMAINDER",
                        "remaining_before": remaining,
                        "result_r": trade.get("result_r"),
                        "recovery_contract_version": RECOVERY_CONTRACT_VERSION,
                    })
            except Exception as exc:  # fail visible; do not mutate anything else
                actions.append({
                    "trade_id": trade.get("id"), "action": "RECOVERY_ERROR",
                    "error": str(exc),
                    "recovery_contract_version": RECOVERY_CONTRACT_VERSION,
                })
        self._recovery_actions = actions
        return actions

    @staticmethod
    def restore_verified_backup(*, backup_db: str | Path, manifest_path: str | Path,
                                destination_db: str | Path,
                                preserve_existing: bool = True) -> dict[str, Any]:
        """Restore one verified snapshot. Service orchestration stays external."""
        backup_db = Path(backup_db)
        manifest_path = Path(manifest_path)
        destination_db = Path(destination_db)
        manifest = _read_json(manifest_path)
        if not manifest or manifest.get("verified") is not True:
            raise ValueError("backup manifest is missing or unverified")
        if manifest.get("backup_contract_version") != BACKUP_CONTRACT_VERSION:
            raise ValueError("backup contract mismatch")
        expected = str(manifest.get("database_sha256") or "")
        if not expected or _sha256(backup_db) != expected:
            raise ValueError("backup SHA256 mismatch")
        ok, detail = _sqlite_integrity(backup_db, full=True)
        if not ok:
            raise ValueError(f"backup integrity failed: {detail}")
        destination_db.parent.mkdir(parents=True, exist_ok=True)
        preserved = None
        if destination_db.exists() and preserve_existing:
            preserved = destination_db.with_name(
                destination_db.name + f".damaged-{int(_now())}")
            os.replace(destination_db, preserved)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(destination_db) + suffix)
                if sidecar.exists():
                    os.replace(sidecar, Path(str(preserved) + suffix))
        tmp = destination_db.with_name(destination_db.name + ".restore.tmp")
        shutil.copy2(backup_db, tmp)
        os.replace(tmp, destination_db)
        restored_ok, restored_detail = _sqlite_integrity(destination_db, full=True)
        if not restored_ok:
            raise RuntimeError(f"restored database failed integrity: {restored_detail}")
        return {
            "ok": True,
            "recovery_contract_version": RECOVERY_CONTRACT_VERSION,
            "restored_sha256": _sha256(destination_db),
            "preserved_previous_db": str(preserved) if preserved else None,
            "integrity": restored_detail,
        }

    # --------------------------------------------------------------- status

    def integrity(self, *, full: bool = False) -> dict[str, Any]:
        ok, detail = _sqlite_integrity(self.db_path, full=full)
        return {
            "storage_contract_version": STORAGE_CONTRACT_VERSION,
            "checked_ts": _now(), "ok": ok, "detail": detail,
            "check_kind": "integrity_check" if full else "quick_check",
        }

    def backups(self, *, limit: int = 50) -> dict[str, Any]:
        local = self._verified_manifests(self.local_dir)[:max(1, min(limit, 500))]
        offhost = ([] if self.offhost_dir is None else
                   self._verified_manifests(self.offhost_dir)[:max(1, min(limit, 500))])
        return {
            "backup_contract_version": BACKUP_CONTRACT_VERSION,
            "local": local,
            "offhost": offhost,
            "offhost_configured": self.offhost_dir is not None,
        }

    def status(self, *, engine=None) -> dict[str, Any]:
        now = _now()
        local = self._last_verified("local")
        offhost = self._last_verified("offhost") if self.offhost_dir is not None else None
        integrity = self.integrity(full=False)
        local_age = None if local is None else max(0.0, now - float(local.get("created_ts") or 0))
        offhost_age = None if offhost is None else max(0.0, now - float(offhost.get("created_ts") or 0))
        if not integrity["ok"]:
            health = "INTEGRITY_WARNING"
        elif local is None or (local_age is not None and local_age > self.local_interval * 2):
            health = "BACKUP_STALE"
        elif self.offhost_dir is None:
            health = "LOCAL_BACKUP_ONLY"
        elif offhost is None or (offhost_age is not None and offhost_age > self.offhost_interval * 2):
            health = "DISASTER_RECOVERY_DEGRADED"
        else:
            health = "HEALTHY"
        unresolved_q = None
        if engine is not None:
            with contextlib.suppress(Exception):
                unresolved_q = int(engine.passive.g1_q_status().get("unresolved_q_capture_n") or 0)
        return {
            "storage_contract_version": STORAGE_CONTRACT_VERSION,
            "backup_contract_version": BACKUP_CONTRACT_VERSION,
            "recovery_contract_version": RECOVERY_CONTRACT_VERSION,
            "health": health,
            "database_path": str(self.db_path),
            "database_exists": self.db_path.exists(),
            "database_size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "sqlite_integrity": integrity,
            "previous_shutdown": self.previous_shutdown,
            "startup_integrity": self._startup_integrity,
            "last_local_backup": local,
            "last_local_backup_age_sec": local_age,
            "last_offhost_backup": offhost,
            "last_offhost_backup_age_sec": offhost_age,
            "offhost_configured": self.offhost_dir is not None,
            "rpo_target_sec": self.local_interval,
            "unresolved_q_observations": unresolved_q,
            "recovery_actions": list(self._recovery_actions[-50:]),
            "last_error": self._last_error,
            "background_backup_running": self._background_running,
            "ram_authority": False,
            "persistent_db_authority": True,
        }

    async def background_loop(self) -> None:
        self._background_running = True
        try:
            while True:
                try:
                    await asyncio.to_thread(self.backup_if_due)
                    self._last_error = None
                except Exception as exc:  # service remains alive; health exposes failure
                    self._last_error = str(exc)
                await asyncio.sleep(60.0)
        finally:
            self._background_running = False


def prepare_storage(settings, *, git_commit: str | None = None) -> StorageManager:
    """Run before Engine construction so old DB is snapshotted before migrations."""
    manager = StorageManager(settings, git_commit=git_commit)
    if manager.db_path.exists():
        # Pre-start backup is intentionally synchronous. A service that cannot
        # snapshot its existing source-of-truth should not silently migrate it.
        manager.create_backup(kind="local", reason="prestart")
    return manager


def install_storage_runtime(app, manager: StorageManager | None = None) -> StorageManager:
    """Attach storage authority and wrap the existing FastAPI lifespan."""
    if getattr(app.state, "storage_runtime_installed", False):
        return app.state.storage
    manager = manager or StorageManager(app.state.settings)
    engine = app.state.engine
    manager.configure_engine_connections(engine)
    manager.mark_startup()
    manager.reconcile_economic_state(engine)
    app.state.storage = manager
    app.state.storage_runtime_installed = True

    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def durable_lifespan(inner_app):
        backup_task: asyncio.Task | None = None
        clean = False
        try:
            async with original_lifespan(inner_app):
                backup_task = asyncio.create_task(manager.background_loop())
                try:
                    yield
                    clean = True
                finally:
                    if backup_task is not None:
                        backup_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await backup_task
        finally:
            # The original lifespan has now stopped collectors and closed engine
            # connections, so this snapshot cannot race an in-flight writer.
            if clean:
                try:
                    await asyncio.to_thread(
                        manager.create_backup, kind="local", reason="clean_shutdown")
                    if manager.offhost_dir is not None:
                        await asyncio.to_thread(
                            manager.create_backup, kind="offhost", reason="clean_shutdown")
                    manager.mark_clean_shutdown()
                except Exception as exc:
                    manager._last_error = str(exc)

    app.router.lifespan_context = durable_lifespan
    return manager
