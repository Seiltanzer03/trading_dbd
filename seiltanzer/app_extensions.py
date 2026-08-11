"""Small optional API extensions installed by the CLI entrypoint."""
from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .ai_decision_state import record_ack
from .lattice_revaluation import install_lattice_revaluation


class AiDecisionAck(BaseModel):
    """Retired acknowledgement payload retained for isolated legacy apps only."""
    trade_id: int
    decision_id: str = Field(min_length=1, max_length=160)
    status: str
    note: str = Field(default="", max_length=500)


def _has_post_route(app, path: str) -> bool:
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        methods = getattr(route, "methods", set()) or set()
        if "POST" in methods:
            return True
    return False


def install_ai_decision_routes(app) -> None:
    """Install the retired ack route only when no canonical route exists.

    `create_app()` owns the authoritative position-state acknowledgement contract
    (`executed: bool`). Historically this extension registered another POST on
    the same path with a different (`status: str`) body. Starlette then exposed
    two indistinguishable routes, allowing stale browser code to hit a schema it
    did not understand. Never duplicate the canonical route again.
    """
    if getattr(app.state, "ai_decision_routes_installed", False):
        return
    if _has_post_route(app, "/api/ai/decision/ack"):
        app.state.ai_decision_routes_installed = True
        app.state.ai_decision_route_source = "canonical_position_state"
        return

    app.state.ai_decision_routes_installed = True
    app.state.ai_decision_route_source = "legacy_extension_fallback"

    @app.post("/api/ai/decision/ack")
    def api_ai_decision_ack(req: AiDecisionAck):
        engine = app.state.engine
        active = engine.journal.active_trade()
        if active is None:
            raise HTTPException(400, "нет активной сделки")
        if int(active["id"]) != int(req.trade_id):
            raise HTTPException(
                409,
                "активная сделка уже изменилась; откройте последний ИИ-разбор",
            )
        try:
            ack = record_ack(
                engine.journal,
                int(req.trade_id),
                req.decision_id,
                req.status,
                note=req.note,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

        if ack["status"] == "executed":
            message = (
                "исполнение отмечено; следующий ИИ-разбор не повторит "
                "это сокращение как новый приказ"
            )
        else:
            message = (
                "отмечено как не выполненное; решение остаётся действующим "
                "до следующего арбитражного пересчёта"
            )
        return {"ok": True, "ack": ack, "message": message}
