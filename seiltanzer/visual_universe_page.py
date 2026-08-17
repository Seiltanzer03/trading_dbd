"""Standalone page route for the removable Universe Lab experiment."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse


UNIVERSE_PAGE = Path(__file__).resolve().parent / "web" / "universe.html"


def install_visual_universe_page(app: FastAPI) -> None:
    if getattr(app.state, "visual_universe_page_installed", False):
        return

    def universe_page():
        return FileResponse(UNIVERSE_PAGE)

    app.add_api_route("/universe", universe_page, methods=["GET"], name="universe_lab")
    app.state.visual_universe_page_installed = True
