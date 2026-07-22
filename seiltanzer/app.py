"""FastAPI-приложение: API, WebSocket-пуш тиков, раздача фронтенда."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import INSTRUMENTS, SETUPS, Settings, settings_from_env
from .engine import Engine

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")


class TradeOpen(BaseModel):
    setup: int
    direction: str
    entry: float
    stop: float
    take: float
    notes: str = ""
    zones: list[dict] = []


class TradeClose(BaseModel):
    trade_id: int
    result_r: float
    notes: str | None = None


class ZonesUpdate(BaseModel):
    trade_id: int
    zones: list[dict]


class AccountUpdate(BaseModel):
    name: str | None = None
    phase: str | None = None
    acc_size: float | None = None
    balance: float | None = None


class JournalAdd(BaseModel):
    """Ручное добавление закрытой сделки (бэкфилл истории)."""
    setup: int
    direction: str
    entry: float
    stop: float
    take: float
    result_r: float
    notes: str = ""
    opened_at: float | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or settings_from_env()
    engine = Engine(settings)
    clients: set[WebSocket] = set()

    app = FastAPI(title="Seiltanzer Terminal", version="0.1.0")
    app.state.engine = engine
    app.state.settings = settings

    # ------------------------------------------------------------ background

    async def poll_loop():
        last = {"price": 0.0, "intraday": 0.0, "vols": 0.0,
                "daily": 0.0, "chain": 0.0}
        periods = {
            "price": 1.0 if settings.demo else settings.price_poll_sec,
            "intraday": 60.0,
            "vols": 5.0 if settings.demo else settings.vol_poll_sec,
            "daily": 1800.0,
            "chain": 30.0 if settings.demo else settings.chain_poll_sec,
        }
        jobs = {
            "price": engine.market.refresh_price,
            "intraday": engine.market.refresh_intraday,
            "vols": engine.market.refresh_vols,
            "daily": engine.market.refresh_daily,
            "chain": engine.market.refresh_chain,
        }
        while True:
            now = time.time()
            for name, fn in jobs.items():
                if now - last[name] >= periods[name]:
                    last[name] = now
                    try:
                        await asyncio.to_thread(fn)
                    except Exception:  # noqa: BLE001 — фид сам ведёт статус
                        pass
            payload = engine.tick_payload()
            dead = []
            for ws in clients:
                try:
                    await ws.send_json(payload)
                except Exception:  # noqa: BLE001
                    dead.append(ws)
            for ws in dead:
                clients.discard(ws)
            await asyncio.sleep(1.0 if settings.demo else 2.0)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(poll_loop())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        engine.close()

    app.router.lifespan_context = lifespan

    # ------------------------------------------------------------------- api

    @app.get("/api/state")
    def api_state():
        return {
            "tick": engine.tick_payload(),
            "ridge": engine.ridge_payload(),
            "journal": engine.journal.list_trades(),
            "edge_track": engine.journal.edge_track(),
            "setups": _setups_payload(),
            "instruments": {c: {"yahoo": i.yahoo, "options_proxy": i.options_proxy}
                            for c, i in INSTRUMENTS.items()},
        }

    def _setups_payload():
        out = []
        for num, s in SETUPS.items():
            stats = engine.journal.setup_stats(num, settings.journal_min_trades)
            jn, jw = engine.journal.journal_counts(num)
            out.append({
                "num": num, "name": s.name, "instrument": s.instrument,
                "rr": s.rr, "builtin_n": s.n, "builtin_wins": s.wins,
                "winrate": stats.winrate, "n": stats.n, "wins": stats.wins,
                "calibration": stats.source, "journal_n": jn, "journal_wins": jw,
                "filters": list(s.filters),
                "efficiency": stats.efficiency,
            })
        return out

    @app.get("/api/setups")
    def api_setups():
        return _setups_payload()

    @app.get("/api/chain")
    def api_chain(ticker: str | None = None):
        # тикер сейчас определяется активным инструментом; параметр — для явности
        ridge = engine.ridge_payload()
        if ticker and ridge.get("proxy") not in (None, ticker):
            raise HTTPException(400, f"активный прокси: {ridge.get('proxy')}, "
                                     f"запрошен {ticker}")
        return ridge

    @app.get("/api/journal")
    def api_journal():
        return engine.journal.list_trades()

    @app.post("/api/journal")
    def api_journal_add(req: JournalAdd):
        try:
            t = engine.journal.add_closed(req.setup, req.direction, req.entry,
                                          req.stop, req.take, req.result_r,
                                          req.notes, req.opened_at)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return t

    @app.get("/api/journal.csv", response_class=PlainTextResponse)
    def api_journal_csv():
        return PlainTextResponse(engine.journal.export_csv(),
                                 media_type="text/csv; charset=utf-8")

    @app.post("/api/trade")
    def api_trade_open(req: TradeOpen):
        setup = SETUPS.get(req.setup)
        if setup is None:
            raise HTTPException(400, f"неизвестный сетап: {req.setup}")
        try:
            trade = engine.journal.open_trade(
                setup=req.setup, instrument=setup.instrument,
                direction=req.direction, entry=req.entry, stop=req.stop,
                take=req.take, notes=req.notes, zones=req.zones)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        engine.on_trade_opened(trade)
        return trade

    @app.post("/api/trade/close")
    def api_trade_close(req: TradeClose):
        try:
            return engine.journal.close_trade(req.trade_id, req.result_r, req.notes)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/trade/zones")
    def api_trade_zones(req: ZonesUpdate):
        try:
            return engine.journal.update_zones(req.trade_id, req.zones)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/account")
    def api_account(req: AccountUpdate):
        try:
            return engine.journal.update_account(**req.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    # -------------------------------------------------------------------- ws

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        clients.add(ws)
        try:
            await ws.send_json(engine.tick_payload())
            while True:
                await ws.receive_text()  # клиент ничего не шлёт; держим сокет
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(ws)

    # ---------------------------------------------------------------- static

    @app.get("/")
    def index():
        return FileResponse(os.path.join(WEB_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
