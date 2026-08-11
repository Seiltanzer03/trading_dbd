"""Read-only storage health APIs for Phase G.1E-0."""
from __future__ import annotations

from fastapi import FastAPI


def install_storage_routes(app: FastAPI) -> None:
    if getattr(app.state, "storage_routes_installed", False):
        return

    def status():
        return app.state.storage.status(engine=app.state.engine)

    def backups(limit: int = 50):
        return app.state.storage.backups(limit=limit)

    def integrity(full: bool = False):
        return app.state.storage.integrity(full=full)

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
    app.state.storage_routes_installed = True
