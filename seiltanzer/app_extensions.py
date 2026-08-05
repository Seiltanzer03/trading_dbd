"""Small optional API extensions installed by the CLI entrypoint."""
from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .ai_decision_state import record_ack


class AiDecisionAck(BaseModel):
    trade_id: int
    decision_id: str = Field(min_length=1, max_length=160)
    status: str
    note: str = Field(default="", max_length=500)


def install_ai_decision_routes(app) -> None:
    """Install idempotent acknowledgement routes on an existing FastAPI app."""
    if getattr(app.state, "ai_decision_routes_installed", False):
        return
    app.state.ai_decision_routes_installed = True

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
