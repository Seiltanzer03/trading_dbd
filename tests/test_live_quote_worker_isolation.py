from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import time

from seiltanzer.app import create_app
from seiltanzer.config import Settings


def test_price_and_canonical_ticks_progress_while_research_executor_is_full(
    tmp_path, monkeypatch,
):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    engine = app.state.engine
    initial = deepcopy(app.state.live_tick_snapshot['payload'])
    release = threading.Event()
    busy = threading.Event()
    price_calls = []
    tick_calls = []

    def research_work():
        busy.set()
        assert release.wait(10), 'test did not release research worker'

    def refresh_price():
        price_calls.append(time.time())

    def live_tick():
        tick_calls.append(time.time())
        payload = deepcopy(initial)
        payload['ts'] = tick_calls[-1]
        return payload

    monkeypatch.setattr(engine.market, 'refresh_price', refresh_price)
    monkeypatch.setattr(engine, 'tick_payload', live_tick)
    for name in ('refresh_proxy_price', 'refresh_vols', 'refresh_daily',
                 'refresh_chain', 'refresh_iv_surface', 'refresh_correlation',
                 'refresh_bybit'):
        monkeypatch.setattr(engine.market, name, lambda: None)
    monkeypatch.setattr('seiltanzer.app._refresh_intraday_archive', lambda _: None)
    monkeypatch.setattr('seiltanzer.background_disk_admission.passive_step', lambda _: None)

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        worker = loop.run_in_executor(None, research_work)
        try:
            async with asyncio.timeout(1):
                while not busy.is_set():
                    await asyncio.sleep(.01)
            async with app.router.lifespan_context(app):
                try:
                    deadline = loop.time() + 2.5
                    while loop.time() < deadline and (
                        len(price_calls) < 2
                        or app.state.live_tick_snapshot['build_n'] < 2
                    ):
                        await asyncio.sleep(.01)
                    assert len(price_calls) >= 2, (
                        'fresh quote polling queued behind background research')
                    assert app.state.live_tick_snapshot['build_n'] >= 2, (
                        'canonical tick publication queued behind background research')
                    assert tick_calls
                    assert app.state.live_tick_snapshot['payload']['ts'] > initial['ts']
                    assert not worker.done(), 'research was released before live progress'
                finally:
                    release.set()
        finally:
            release.set()
            await worker

    asyncio.run(scenario())
