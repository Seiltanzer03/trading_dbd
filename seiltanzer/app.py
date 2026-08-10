"""FastAPI-приложение: API, WebSocket-пуш тиков, раздача фронтенда."""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import time
import base64
import secrets

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import INSTRUMENTS, SETUPS, Settings, settings_from_env
from .decision_research import canonical_snapshot
from .position_state import StaleDecisionError
from .engine import Engine
from .ai_verdict import build_snapshot, render_policy_report, request_verdict
from .ai_api import (
    deterministic_result,
    error_body as ai_error_body,
    log_event as log_ai_event,
    provider_error as normalize_provider_error,
    request_id as new_ai_request_id,
    success_body as ai_success_body,
)

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")


class TradeOpen(BaseModel):
    setup: int
    direction: str
    entry: float
    stop: float
    take: float
    notes: str = ""
    zones: list[dict] = Field(default_factory=list)
    # Текущая цена у брокера/на торгуемом CFD в момент открытия формы.
    # Если отличается от бесплатного фьючерса Yahoo, сохраняем постоянный basis
    # и продолжаем вести сделку живыми изменениями бесплатного ряда.
    reference_price: float | None = None


class TradeClose(BaseModel):
    trade_id: int
    result_r: float
    notes: str | None = None


class ZonesUpdate(BaseModel):
    trade_id: int
    zones: list[dict] = Field(default_factory=list)


class TradeEdit(BaseModel):
    trade_id: int
    setup: int | None = None
    direction: str | None = None
    entry: float | None = None
    stop: float | None = None
    take: float | None = None
    result_r: float | None = None
    notes: str | None = None


class TradeDelete(BaseModel):
    trade_id: int


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


class HumanDecisionRecord(BaseModel):
    review_id: str
    policy: str
    reason_category: str
    note: str = ""


class ManagementExecution(BaseModel):
    decision_id: str
    trade_id: int
    executed: bool


class ExperimentRegister(BaseModel):
    experiment_id: str
    hypothesis: str
    features: list[str] = Field(default_factory=list)
    formula: str
    thresholds: dict = Field(default_factory=dict)
    train_period: tuple[float, float]
    validation_period: tuple[float, float]
    test_period: tuple[float, float]


class ExperimentResult(BaseModel):
    experiment_id: str
    result: dict = Field(default_factory=dict)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or settings_from_env()
    engine = Engine(settings)
    clients: set[WebSocket] = set()
    ai_last_call = 0.0
    ai_lock = asyncio.Lock()

    app = FastAPI(title="Seiltanzer Terminal", version="0.1.0")
    app.state.engine = engine
    app.state.settings = settings

    @app.middleware("http")
    async def auth_and_no_cache(request, call_next):
        # Basic Auth check
        auth_user = os.environ.get("TERMINAL_USER")
        auth_pass = os.environ.get("TERMINAL_PASS")
        if auth_user and auth_pass:
            auth_header = request.headers.get("Authorization")
            authorized = False
            if auth_header and auth_header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                    username, _, password = decoded.partition(":")
                    if (secrets.compare_digest(username.encode("utf8"), auth_user.encode("utf8")) and
                        secrets.compare_digest(password.encode("utf8"), auth_pass.encode("utf8"))):
                        authorized = True
                except Exception:
                    pass
            if not authorized:
                return Response("Unauthorized", status_code=401, headers={"WWW-Authenticate": 'Basic realm="Seiltanzer Terminal"'})

        # запрет кэша на фронт: гарантирует, что браузер получит свежий JS/CSS
        # (иначе после git pull старый app.js мог остаться в кэше)
        resp = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static"):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
        return resp

    # ------------------------------------------------------------ background

    async def poll_loop():
        last = {"price": 0.0, "proxy_price": 0.0, "intraday": 0.0, "vols": 0.0,
                "daily": 0.0, "chain": 0.0, "iv_surface": 0.0, "correlation": 0.0}
        running: dict[str, asyncio.Task] = {}
        # при живом стриме цену «опрашиваем» часто (берём свежий тик из памяти)
        price_period = 1.0 if (settings.demo or settings.stream) else settings.price_poll_sec
        periods = {
            "price": price_period,
            "proxy_price": 1.0 if (settings.demo or settings.stream)
                           else settings.proxy_poll_sec,
            "intraday": 60.0,
            "vols": 5.0 if settings.demo else settings.vol_poll_sec,
            "daily": 30.0 if settings.demo else 300.0,
            "chain": 1.0 if settings.demo else settings.chain_poll_sec,
            "iv_surface": 1.0 if settings.demo else settings.chain_poll_sec,
            "correlation": 30.0 if settings.demo else 300.0,
        }
        jobs = {
            "price": engine.market.refresh_price,
            "proxy_price": engine.market.refresh_proxy_price,
            "intraday": engine.market.refresh_intraday,
            "vols": engine.market.refresh_vols,
            "daily": engine.market.refresh_daily,
            "chain": engine.market.refresh_chain,
            "iv_surface": engine.market.refresh_iv_surface,
            "correlation": engine.market.refresh_correlation,
        }
        try:
            while True:
                now = time.time()
                # Долгий option_chain/IV запрос больше не останавливает тиковый
                # WebSocket: каждый фид работает максимум в одном background task.
                for name, task in list(running.items()):
                    if task.done():
                        with contextlib.suppress(Exception):
                            task.result()
                        del running[name]
                for name, fn in jobs.items():
                    if name not in running and now - last[name] >= periods[name]:
                        last[name] = now
                        running[name] = asyncio.create_task(asyncio.to_thread(fn))

                payload = engine.tick_payload()
                dead = []
                for ws in clients:
                    try:
                        await ws.send_json(payload)
                    except Exception:  # noqa: BLE001
                        dead.append(ws)
                for ws in dead:
                    clients.discard(ws)
                await asyncio.sleep(
                    1.0 if (settings.demo or settings.stream) else 2.0)
        finally:
            # Не закрываем sqlite, пока уже запущенный фид ещё может писать кэш.
            if running:
                await asyncio.gather(*running.values(), return_exceptions=True)

    async def passive_loop():
        while True:
            await asyncio.to_thread(engine.passive.step)
            await asyncio.sleep(2.0 if settings.demo else 10.0)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        if engine.stream_hub is not None:
            engine.stream_hub.start()      # живой WS-стрим цены (нужен event loop)
        task = asyncio.create_task(poll_loop())
        passive_task = asyncio.create_task(passive_loop())
        yield
        task.cancel()
        passive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        with contextlib.suppress(asyncio.CancelledError):
            await passive_task
        if engine.stream_hub is not None:
            await engine.stream_hub.stop()
        engine.close()

    app.router.lifespan_context = lifespan

    # ------------------------------------------------------------------- api

    @app.get("/api/state")
    def api_state():
        active = engine.journal.active_trade()
        return {
            "tick": engine.tick_payload(),
            "ridge": engine.ridge_payload(),
            "journal": engine.journal.list_trades(),
            "edge_track": engine.journal.edge_track(),
            "validation": engine.journal.validation_report(),
            "ai_history": (engine.journal.recent_ai_verdicts(active["id"], limit=10)
                           if active else []),
            "setups": _setups_payload(),
            "instruments": {c: {"yahoo": i.yahoo,
                                "quote_pair": i.swissquote_pair,
                                "broker_symbol": i.tradingview_symbol,
                                "options_proxy": i.options_proxy}
                            for c, i in INSTRUMENTS.items()},
        }

    @app.get("/api/ai/history")
    def api_ai_history():
        active = engine.journal.active_trade()
        return {
            "trade_id": active["id"] if active else None,
            "items": (engine.journal.recent_ai_verdicts(active["id"], limit=10)
                      if active else []),
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

    @app.get("/api/diagnostics")
    def api_diagnostics():
        return engine.diagnostics_payload()

    @app.get("/api/validation")
    def api_validation():
        report = engine.journal.validation_report()
        report["counterfactual_replay"] = engine.journal.counterfactual_report()
        report["q_calibration"] = engine.journal.q_calibration_report()
        return report

    @app.get("/api/research/passive/status")
    def api_passive_status():
        return engine.passive.status()

    @app.get("/api/research/passive/calibration")
    def api_passive_calibration():
        return engine.passive.calibration_report()

    @app.get("/api/research/passive/observations")
    def api_passive_observations(limit: int = 100,
                                 instrument: str | None = None):
        return engine.passive.observations(limit=limit, instrument=instrument)

    @app.get("/api/research/passive/edge")
    def api_passive_edge():
        return engine.passive.edge_report(engine.journal.counterfactual_report())

    @app.get("/api/research/counterfactual")
    def api_counterfactual_research(trade_id: int | None = None,
                                    limit: int = 100):
        return engine.journal.counterfactual_report(trade_id, limit=limit)

    @app.post("/api/research/human-decision")
    def api_human_decision(req: HumanDecisionRecord):
        try:
            return engine.journal.record_human_decision(
                req.review_id, req.policy, req.reason_category, req.note)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/research/experiments")
    def api_experiment_report():
        return engine.journal.experiment_report()

    @app.post("/api/research/experiments")
    def api_experiment_register(req: ExperimentRegister):
        try:
            return engine.journal.register_experiment(
                experiment_id=req.experiment_id, hypothesis=req.hypothesis,
                features=req.features, formula=req.formula,
                thresholds=req.thresholds, train_period=req.train_period,
                validation_period=req.validation_period,
                test_period=req.test_period,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/research/experiments/result")
    def api_experiment_result(req: ExperimentResult):
        try:
            return engine.journal.record_experiment_result(
                req.experiment_id, req.result)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/chain")
    def api_chain(ticker: str | None = None):
        # тикер сейчас определяется активным инструментом; параметр — для явности
        ridge = engine.ridge_payload()
        if ticker and ridge.get("proxy") not in (None, ticker):
            raise HTTPException(400, f"активный прокси: {ridge.get('proxy')}, "
                                     f"запрошен {ticker}")
        return ridge

    @app.get("/api/analytics/gex-migration")
    def api_analytics_gex_migration():
        return engine.gex_migration_payload()

    @app.get("/api/analytics/regime-phase")
    def api_analytics_regime_phase():
        return engine.macro_regime_payload()

    @app.get("/api/analytics/wavelet")
    def api_analytics_wavelet():
        return engine.wavelet_payload()

    @app.get("/api/analytics/correlation-graph")
    def api_analytics_correlation_graph():
        return engine.cross_asset_payload()

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
            # Basis имеет смысл только против правильного бесплатного ряда.
            # Без активной сделки движок мог всё ещё стоять, например, на NAS100,
            # пока пользователь открывает XAU.
            if engine.market.instrument_code != setup.instrument:
                engine.market.set_instrument(setup.instrument)
                engine.market.refresh_price()
            raw_price = engine.market.price.get("value")
            reference = req.reference_price
            if reference is not None and (not math.isfinite(reference) or reference <= 0):
                raise ValueError("текущая цена брокера должна быть положительным числом")
            if reference is not None and raw_price is None:
                raise ValueError(
                    "не удалось получить бесплатную котировку выбранного "
                    "инструмента — basis сейчас зафиксировать нельзя")
            quote_offset = ((reference - raw_price)
                            if reference is not None and raw_price is not None else 0.0)
            trade = engine.journal.open_trade(
                setup=req.setup, instrument=setup.instrument,
                direction=req.direction, entry=req.entry, stop=req.stop,
                take=req.take, notes=req.notes, zones=req.zones,
                quote_offset=quote_offset, raw_price_at_open=raw_price,
                quote_source=engine.market.price.get("source"))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        engine.position.open_trade(trade)
        engine.on_trade_opened(trade)
        return {**trade, "position_state": engine.position.state(trade)}

    @app.post("/api/trade/close")
    def api_trade_close(req: TradeClose):
        try:
            live_trade = engine.journal.get_trade(req.trade_id)
            closed = engine.journal.close_trade(req.trade_id, req.result_r, req.notes)
            engine.position.terminal_exit(
                live_trade, event_type="MANUAL_EXIT",
                execution_price=engine._current_instrument_price(live_trade),
                execution_r=req.result_r)
            return {**closed, "position_state": engine.position.state(live_trade)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/trade/zones")
    def api_trade_zones(req: ZonesUpdate):
        try:
            return engine.journal.update_zones(req.trade_id, req.zones)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/trade/edit")
    def api_trade_edit(req: TradeEdit):
        try:
            previous = engine.journal.get_trade(req.trade_id)
            trade = engine.journal.edit_trade(
                req.trade_id, setup=req.setup, direction=req.direction,
                entry=req.entry, stop=req.stop, take=req.take,
                result_r=req.result_r, notes=req.notes)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        geometry_changed = any(
            previous.get(key) != trade.get(key)
            for key in ("setup", "instrument", "direction", "entry", "stop", "take"))
        if geometry_changed:
            engine.position.supersede_trade(req.trade_id, "trade_geometry_changed")
        if trade["status"] == "open":
            engine.on_trade_edited(trade)
            return {**trade, "position_state": engine.position.state(trade)}
        return trade

    @app.post("/api/trade/delete")
    def api_trade_delete(req: TradeDelete):
        try:
            engine.journal.delete_trade(req.trade_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.post("/api/account")
    def api_account(req: AccountUpdate):
        try:
            return engine.journal.update_account(**req.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/ai/verdict")
    async def api_ai_verdict():
        nonlocal ai_last_call
        req_id = new_ai_request_id()
        started = time.monotonic()
        if ai_lock.locked():
            return JSONResponse(
                status_code=429,
                content=ai_error_body(
                    "ai_request_in_progress", "ИИ уже анализирует предыдущий снимок",
                    req_id, retriable=True),
            )
        if time.monotonic() - ai_last_call < 15:
            return JSONResponse(
                status_code=429,
                content=ai_error_body(
                    "ai_rate_limited", "Повторный анализ доступен через 15 секунд",
                    req_id, retriable=True),
            )
        async with ai_lock:
            try:
                snapshot = build_snapshot(engine)
            except Exception as exc:
                log_ai_event(req_id=req_id, stage="snapshot_error", started=started, exc=exc)
                return JSONResponse(
                    status_code=500,
                    content=ai_error_body(
                        "snapshot_error", "Не удалось зафиксировать снимок сделки",
                        req_id, retriable=False),
                )
            if snapshot.get("trade_id") is None:
                return JSONResponse(
                    status_code=400,
                    content=ai_error_body(
                        "no_active_trade", "Нет активной сделки для ИИ-разбора",
                        req_id, retriable=False),
                )
            ai_last_call = time.monotonic()
            trade_id = int(snapshot["trade_id"])
            try:
                review_id = canonical_snapshot(snapshot)["review_id"]
            except Exception as exc:
                log_ai_event(
                    req_id=req_id, trade_id=trade_id, stage="snapshot_error",
                    started=started, exc=exc)
                return JSONResponse(
                    status_code=500,
                    content=ai_error_body(
                        "snapshot_error", "Снимок сделки не прошёл проверку целостности",
                        req_id, retriable=False),
                )
            decision = ((snapshot.get("policy_manager") or {})
                        .get("management_decision"))
            # Persist the deterministic instruction before waiting on the LLM.
            # Provider latency must not turn a valid working decision stale.
            active_trade = engine.journal.active_trade()
            try:
                if (decision and active_trade
                        and int(active_trade["id"]) == trade_id):
                    engine.position.register_decision(
                        snapshot, review_id, active_trade)
            except StaleDecisionError:
                # A deterministic tick may arm BE while the snapshot is being
                # calculated. Rebuild exactly once from the new economic state;
                # repeated movement remains a real 409 stale-decision conflict.
                snapshot = await asyncio.to_thread(build_snapshot, engine)
                trade_id = int(snapshot["trade_id"])
                review_id = canonical_snapshot(snapshot)["review_id"]
                decision = ((snapshot.get("policy_manager") or {})
                            .get("management_decision"))
                active_trade = engine.journal.active_trade()
                try:
                    if (decision and active_trade
                            and int(active_trade["id"]) == trade_id):
                        engine.position.register_decision(
                            snapshot, review_id, active_trade)
                except StaleDecisionError as exc:
                    log_ai_event(
                        req_id=req_id, trade_id=trade_id, stage="stale_decision",
                        review_id=review_id, started=started, exc=exc)
                    return JSONResponse(
                        status_code=409,
                        content=ai_error_body(
                            "stale_decision",
                            "Состояние позиции изменилось во время расчёта; запросите новый разбор",
                            req_id, retriable=True),
                    )
            degraded = False
            provider_failure = None
            try:
                result = await asyncio.to_thread(request_verdict, snapshot)
                if not isinstance(result, dict) or not isinstance(result.get("verdict"), str):
                    raise RuntimeError("provider_invalid_payload")
            except RuntimeError as exc:
                provider_failure = normalize_provider_error(exc)
                if "invalid_payload" in str(exc).lower():
                    provider_failure = {"code": "provider_invalid_payload", "retriable": True}
                result = deterministic_result(snapshot, render_policy_report)
                degraded = True
                log_ai_event(
                    req_id=req_id, trade_id=trade_id, stage="provider_fallback",
                    review_id=review_id,
                    started=started, provider="openrouter", mode="deterministic_fallback",
                    exc=exc,
                )
            except Exception as exc:
                # ValueError/TypeError and other unexpected application failures
                # must remain visible as programming errors, not provider outages.
                log_ai_event(
                    req_id=req_id, trade_id=trade_id, stage="internal_error",
                    review_id=review_id,
                    started=started, provider="openrouter", exc=exc,
                )
                return JSONResponse(
                    status_code=500,
                    content=ai_error_body(
                        "ai_internal_error", "Не удалось сформировать ИИ-разбор",
                        req_id, retriable=False),
                )
            try:
                if decision:
                    result["management_decision"] = decision
                engine.journal.record_ai_verdict(
                    trade_id, snapshot,
                    result["verdict"], result.get("model"))
            except Exception as exc:
                log_ai_event(
                    req_id=req_id, trade_id=trade_id, stage="journal_error",
                    review_id=review_id,
                    started=started, mode=("deterministic_fallback" if degraded else "llm"),
                    exc=exc,
                )
                return JSONResponse(
                    status_code=500,
                    content=ai_error_body(
                        "journal_error", "Разбор рассчитан, но не удалось сохранить снимок",
                        req_id, retriable=False),
                )
            body = ai_success_body(
                result, req_id, degraded=degraded,
                provider_failure=provider_failure,
            )
            body["context_reviews"] = len(snapshot.get("previous_reviews") or [])
            log_ai_event(
                req_id=req_id, trade_id=trade_id, stage="complete", started=started,
                review_id=review_id,
                provider="openrouter", mode=body["mode"],
            )
            return JSONResponse(content=body)

    @app.post("/api/ai/decision/ack")
    def api_ai_decision_ack(req: ManagementExecution):
        try:
            trade = engine.journal.active_trade()
            if trade is None or int(trade["id"]) != int(req.trade_id):
                raise StaleDecisionError("active trade changed")
            tick = engine.tick_payload()
            return engine.position.acknowledge(
                decision_id=req.decision_id, trade=trade, executed=req.executed,
                execution_price=((tick.get("feeds") or {}).get("price") or {}).get("value"),
                execution_r=((tick.get("prob") or {}).get("r")))
        except StaleDecisionError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/position")
    def api_position_state():
        trade = engine.journal.active_trade()
        return {
            "trade_id": trade["id"] if trade else None,
            "position_state": engine.position.state(trade) if trade else None,
            "events": engine.position.events(trade["id"]) if trade else [],
        }

    # -------------------------------------------------------------------- ws

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        
        # WebSocket Auth (если включено)
        auth_user = os.environ.get("TERMINAL_USER")
        auth_pass = os.environ.get("TERMINAL_PASS")
        if auth_user and auth_pass:
            auth_header = ws.headers.get("Authorization")
            authorized = False
            if auth_header and auth_header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                    username, _, password = decoded.partition(":")
                    if (secrets.compare_digest(username.encode("utf8"), auth_user.encode("utf8")) and
                        secrets.compare_digest(password.encode("utf8"), auth_pass.encode("utf8"))):
                        authorized = True
                except Exception:
                    pass
            if not authorized:
                await ws.close(code=1008)
                return

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
