from __future__ import annotations

import asyncio
from copy import deepcopy
import threading

import pytest

from seiltanzer.app import create_app
from seiltanzer.config import Settings


def _blocked_app(tmp_path, monkeypatch):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    engine = app.state.engine
    initial = deepcopy(app.state.live_tick_snapshot['payload'])
    started, release, finished = (threading.Event() for _ in range(3))
    price_polled_twice = threading.Event()
    counts = {'price': 0, 'tick': 0}

    def refresh_price():
        counts['price'] += 1
        if counts['price'] >= 2:
            price_polled_twice.set()

    def blocked_tick():
        counts['tick'] += 1
        started.set()
        try:
            if not release.wait(5):
                raise TimeoutError('test did not release tick calculation')
            return deepcopy(initial)
        finally:
            finished.set()

    monkeypatch.setattr(engine.market, 'refresh_price', refresh_price)
    for name in ('refresh_proxy_price', 'refresh_vols', 'refresh_daily',
                 'refresh_chain', 'refresh_iv_surface', 'refresh_correlation',
                 'refresh_bybit'):
        monkeypatch.setattr(engine.market, name, lambda: None)
    monkeypatch.setattr('seiltanzer.app._refresh_intraday_archive', lambda _: None)
    monkeypatch.setattr('seiltanzer.background_disk_admission.passive_step', lambda _: None)
    monkeypatch.setattr(engine, 'tick_payload', blocked_tick)
    return app, initial, started, release, finished, price_polled_twice, counts


def test_slow_tick_does_not_starve_price_polling_or_start_overlapping_ticks(
    tmp_path, monkeypatch,
):
    (app, initial, started, release, finished,
     price_polled_twice, counts) = _blocked_app(tmp_path, monkeypatch)

    async def scenario():
        async with app.router.lifespan_context(app):
            try:
                assert await asyncio.to_thread(started.wait, 1)
                polled_while_calculating = await asyncio.to_thread(
                    price_polled_twice.wait, 2)
                assert counts['tick'] == 1, 'overlapping canonical calculations'
                assert polled_while_calculating, (
                    'price feed scheduling waited for slow tick mathematics')
                assert not finished.is_set()
                # A pending generation must not overwrite the published one.
                assert app.state.live_tick_snapshot['payload'] == initial
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 2)
            async with asyncio.timeout(2):
                while app.state.live_tick_snapshot['build_n'] < 2:
                    await asyncio.sleep(.01)
            published = app.state.live_tick_snapshot['payload']
            assert published['ts'] == initial['ts']
            assert published['feeds']['price'] == initial['feeds']['price']

    asyncio.run(scenario())


@pytest.mark.parametrize('cancel_during_join', [False, True])
def test_shutdown_waits_for_inflight_tick_before_closing_engine(
    tmp_path, monkeypatch, cancel_during_join,
):
    app, _, started, release, finished, _, _ = _blocked_app(tmp_path, monkeypatch)
    original_close = app.state.engine.close
    closed = []

    def close():
        closed.append(finished.is_set())
        original_close()

    monkeypatch.setattr(app.state.engine, 'close', close)

    async def scenario():
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        stopping = None
        try:
            assert await asyncio.to_thread(started.wait, 1)
            poll = next(task for task in asyncio.all_tasks()
                        if task.get_coro().__name__ == 'poll_loop')
            stopping = asyncio.create_task(context.__aexit__(None, None, None))
            await asyncio.sleep(.05)
            assert not stopping.done()
            assert not closed
            if cancel_during_join:
                poll.cancel()
                await asyncio.sleep(.05)
                assert not stopping.done(), 'shutdown orphaned the tick worker'
                assert not closed
        finally:
            release.set()
            if stopping is not None:
                await asyncio.wait_for(stopping, 2)
            else:
                await context.__aexit__(None, None, None)
        assert closed == [True]

    asyncio.run(scenario())
