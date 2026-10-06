import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from seiltanzer.macro_data_factory_routes import _install_prospective_fomc_lifespan


@pytest.mark.parametrize('fail', [False, True])
def test_worker_lifetime_is_inside_existing_app_lifespan(fail):
    app = FastAPI()
    events = []

    @asynccontextmanager
    async def original(inner_app):
        assert inner_app is app
        events.append('app_start')
        try:
            yield {'existing_state': True}
        finally:
            events.append('app_close')

    app.router.lifespan_context = original
    runtime = SimpleNamespace(start=lambda: events.append('worker_start'),
                              stop=lambda: events.append('worker_stop'))
    _install_prospective_fomc_lifespan(app, runtime)
    assert events == []

    async def exercise():
        async with app.router.lifespan_context(app) as state:
            assert state == {'existing_state': True}
            events.append('request')
            if fail:
                raise RuntimeError('request failed')

    if fail:
        with pytest.raises(RuntimeError, match='request failed'):
            asyncio.run(exercise())
    else:
        asyncio.run(exercise())
    assert events == ['app_start', 'worker_start', 'request', 'worker_stop', 'app_close']
