"""Бесплатный WebSocket-стрим цены базового актива (Yahoo Finance).

Yahoo отдаёт живые тики через недокументированный сокет
`wss://streamer.finance.yahoo.com` (protobuf-сообщения `yaticker`, без API-ключа).
Мы подписываемся на тикеры инструментов и держим свежую цену в памяти; фид цены
(`MarketData.refresh_price`) берёт её, если она свежая, иначе — обычный REST.

Дизайн — безопасный: любой сбой стрима не ломает приложение, REST-поллинг всегда
остаётся запасным путём. Опционально (флаг --stream). Тестируется в среде с
интернетом; в оффлайне просто не подключится и всё работает на REST.

Только цена базового актива стримится бесплатно — опционные цепочки/плотность
по-прежнему приходят по REST (тиковые опционы требуют платного провайдера).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import struct
import time

log = logging.getLogger("seiltanzer.stream")

WS_URL = "wss://streamer.finance.yahoo.com/"


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, i


def parse_yaticker(buf: bytes) -> dict:
    """Минимальный разбор protobuf `yaticker`: поле 1 = id (строка), поле 2 = price
    (float32). Остальные поля пропускаются по wire-type. Возвращает {id, price}."""
    out: dict = {}
    i = 0
    n = len(buf)
    try:
        while i < n:
            tag, i = _read_varint(buf, i)
            field, wtype = tag >> 3, tag & 7
            if wtype == 0:          # varint
                _, i = _read_varint(buf, i)
            elif wtype == 1:        # 64-bit
                i += 8
            elif wtype == 2:        # length-delimited
                ln, i = _read_varint(buf, i)
                data = buf[i:i + ln]
                i += ln
                if field == 1:
                    out["id"] = data.decode("utf-8", "ignore")
            elif wtype == 5:        # 32-bit
                if field == 2 and i + 4 <= n:
                    out["price"] = struct.unpack("<f", buf[i:i + 4])[0]
                i += 4
            else:
                break
    except Exception:  # noqa: BLE001 — битый кадр не должен ронять стрим
        return out
    return out


class StreamHub:
    """Держит открытый WS к Yahoo и свежие цены по тикерам.

    latest[ticker] = (price, ts). Метод fresh(ticker, max_age) возвращает цену,
    если она не старше max_age секунд.
    """

    def __init__(self, tickers: list[str]):
        self.tickers = tickers
        self.latest: dict[str, tuple[float, float]] = {}
        self._task: asyncio.Task | None = None
        self._stop = False
        self.connected = False

    def fresh(self, ticker: str, max_age: float = 8.0) -> float | None:
        v = self.latest.get(ticker)
        if v and time.time() - v[1] <= max_age:
            return v[0]
        return None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        try:
            import websockets  # noqa: PLC0415
        except ImportError:
            log.warning("пакет websockets не установлен — стрим цены отключён "
                        "(pip install websockets); работаем на REST")
            return
        backoff = 2.0
        while not self._stop:
            try:
                async with websockets.connect(WS_URL, ping_interval=20,
                                              open_timeout=15) as ws:
                    import json
                    await ws.send(json.dumps({"subscribe": self.tickers}))
                    self.connected = True
                    backoff = 2.0
                    log.info("стрим цены подключён: %s", ",".join(self.tickers))
                    async for raw in ws:
                        self._on_message(raw)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                self.connected = False
                log.warning("стрим цены отвалился (%s), переподключение через %.0fс",
                            str(e)[:80], backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    def _on_message(self, raw) -> None:
        try:
            if isinstance(raw, str):
                # Yahoo шлёт либо JSON-обёртку, либо голый base64 protobuf
                payload = raw
                if raw.startswith("{"):
                    import json
                    payload = json.loads(raw).get("message", "")
                buf = base64.b64decode(payload)
            else:
                buf = raw if isinstance(raw, (bytes, bytearray)) else base64.b64decode(raw)
            tick = parse_yaticker(buf)
            tid, price = tick.get("id"), tick.get("price")
            if tid and price and price > 0:
                self.latest[tid] = (float(price), time.time())
        except Exception:  # noqa: BLE001
            pass
