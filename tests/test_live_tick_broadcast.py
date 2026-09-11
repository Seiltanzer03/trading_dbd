import asyncio

from seiltanzer.app import broadcast_live_tick


def test_disconnect_during_send_does_not_stop_other_clients():
    async def scenario():
        clients = set()
        received = []

        class Client:
            async def send_json(self, payload):
                clients.discard(self)
                await asyncio.sleep(0)
                received.append(payload)

        clients.update(Client() for _ in range(3))
        await broadcast_live_tick(clients, {"ts": 123})
        assert received == [{"ts": 123}] * 3
        assert not clients

    asyncio.run(scenario())


def test_slow_client_cannot_block_healthy_client_or_next_tick():
    async def scenario():
        delivered = asyncio.Event()
        closed = []
        received = []

        class Slow:
            async def send_json(self, payload):
                await asyncio.Event().wait()

            async def close(self, code):
                closed.append(code)
                await asyncio.Event().wait()

        class Healthy:
            async def send_json(self, payload):
                received.append(payload)
                delivered.set()

        slow, healthy = Slow(), Healthy()
        clients = {slow, healthy}
        task = asyncio.create_task(broadcast_live_tick(clients, 1, timeout=0.1))
        await asyncio.wait_for(delivered.wait(), timeout=0.05)
        assert not task.done()
        await asyncio.wait_for(task, timeout=1)
        assert clients == {healthy}
        assert closed == [1013]
        await broadcast_live_tick(clients, 2)
        assert received == [1, 2]

    asyncio.run(scenario())


def test_cancellation_propagates_to_inflight_send():
    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        class Client:
            async def send_json(self, payload):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        task = asyncio.create_task(broadcast_live_tick({Client()}, {}))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError('broadcast swallowed shutdown cancellation')
        assert cancelled.is_set()

    asyncio.run(scenario())
