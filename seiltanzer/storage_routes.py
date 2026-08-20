"""Storage health and safe local restore-drill APIs for Phase G.1E-0."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request

from .storage_readiness import bounded_storage_integrity
from .storage_restore_drill import (
    RESTORE_DRILL_CONTRACT_VERSION,
    last_restore_drill,
    run_restore_drill,
)


_LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _require_loopback(request: Request) -> None:
    client_host = request.client.host if request.client is not None else ""
    if client_host not in _LOOPBACK_CLIENTS:
        raise HTTPException(status_code=403, detail="restore drill is localhost-only")


def install_storage_routes(app: FastAPI) -> None:
    if getattr(app.state, "storage_routes_installed", False):
        return

    def status():
        body = app.state.storage.status(engine=app.state.engine)
        body["restore_drill_contract_version"] = RESTORE_DRILL_CONTRACT_VERSION
        body["last_restore_drill"] = last_restore_drill(app.state.storage)
        return body

    def backups(limit: int = 50):
        return app.state.storage.backups(limit=limit)

    def integrity(full: bool = False):
        if full:
            return app.state.storage.integrity(full=True)
        return bounded_storage_integrity(app.state.storage)

    def restore_drill(request: Request):
        # This operation verifies a protected snapshot by restoring only into a
        # disposable tempfile. Keep it reachable only from the local production
        # host/CI runner, not through the public terminal surface.
        _require_loopback(request)
        try:
            return run_restore_drill(app.state.storage)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"restore drill failed: {exc}") from exc

    def restore_drill_status(request: Request):
        # Deliberately bounded: no SQLite integrity scan, backup enumeration or
        # research aggregation. This is the post-drill acceptance/readback path.
        _require_loopback(request)
        report = last_restore_drill(app.state.storage)
        if report is None:
            state = "NEVER_RUN"
        else:
            state = "PASS" if report.get("ok") is True else "FAIL"
        return {
            "restore_drill_contract_version": RESTORE_DRILL_CONTRACT_VERSION,
            "status": state,
            "last_restore_drill": report,
        }

    app.add_api_route(
        "/api/system/storage/status", status,
        methods=["GET"], name="storage_status",
    )
    app.add_api_route(
        "/api/system/storage/backups", backups,
        methods=["GET"], name="storage_backups",
    )
    app.add_api_route(
        "/api/system/storage/integrity", integrity,
        methods=["GET"], name="storage_integrity",
    )
    app.add_api_route(
        "/api/system/storage/restore-drill", restore_drill,
        methods=["POST"], name="storage_restore_drill",
    )
    app.add_api_route(
        "/api/system/storage/restore-drill/status", restore_drill_status,
        methods=["GET"], name="storage_restore_drill_status",
    )
    app.state.storage_routes_installed = True
