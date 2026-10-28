"""Publish slow instrument feeds only to the generation that requested them."""
from __future__ import annotations

from copy import copy, deepcopy
from functools import wraps
import threading
import time


VERSION = 'instrument-feed-publication-v1'
CHANNEL_FIELDS = {
    'refresh_proxy_price': ('proxy_price', '_last_proxy_rest_attempt'),
    'refresh_intraday': ('intraday', 'intraday_ohlcv', 'intraday_source_authority',
        'intraday_is_offset', 'binance_trade_flow', 'family_reference_series',
        'family_reference_error'),
    'refresh_daily': ('daily',),
    'refresh_chain': ('chain', 'proxy_price', '_chain_error_detail', '_deribit_fetcher'),
    'refresh_iv_surface': ('iv_surface', '_deribit_fetcher'),
}
_INPUT_FIELDS = tuple(dict.fromkeys(name for fields in CHANNEL_FIELDS.values()
                                  for name in fields if name != '_deribit_fetcher'))


class _StagedCache:
    """Defer the two adapter writes, preserving their original receipt clocks."""
    def __init__(self, cache):
        self.cache = cache
        self.writes = []

    def get(self, *args, **kwargs):
        return self.cache.get(*args, **kwargs)

    def chain_snapshots(self, *args, **kwargs):
        return self.cache.chain_snapshots(*args, **kwargs)

    def put(self, key, value, ts=None):
        self.writes.append(('put', (key, deepcopy(value)), {'ts': ts or time.time()}))

    def add_chain_snapshot(self, ticker, snapshot, ts=None, keep=60):
        self.writes.append(('add_chain_snapshot', (ticker, deepcopy(snapshot)),
                            {'ts': ts or time.time(), 'keep': keep}))

    def publish(self):
        for method, args, kwargs in self.writes:
            getattr(self.cache, method)(*args, **kwargs)


def _isolated_refresh(method, channel):
    @wraps(method)
    def refresh(self, *args, **kwargs):
        # Resilience may delegate to an already wrapped adapter. Both run on
        # the same private view, without nesting publication transactions.
        if getattr(self, '_instrument_refresh_view', False):
            return method(self, *args, **kwargs)
        with self._intraday_lock:
            code = self.instrument_code
            revision = self._price_revision
            generations = getattr(self, '_instrument_refresh_generations', None)
            if generations is None:
                generations = self._instrument_refresh_generations = {}
            generation = generations[channel] = generations.get(channel, 0) + 1
            locks = getattr(self, '_instrument_refresh_publication_locks', None)
            if locks is None:
                locks = self._instrument_refresh_publication_locks = {}
            publication_lock = locks.setdefault(channel, threading.RLock())
            view = copy(self)
            view._instrument_refresh_view = True
            view._intraday_lock = threading.RLock()
            view._price_refresh_state = threading.local()
            view._published_price = deepcopy(self.price)
            view._primary_price = deepcopy(getattr(self, '_primary_price', self.price))
            view.cache = _StagedCache(self.cache)
            for field in _INPUT_FIELDS:
                if hasattr(self, field):
                    setattr(view, field, deepcopy(getattr(self, field)))
            original_proxy = deepcopy(getattr(self, 'proxy_price', None))

        result = method(view, *args, **kwargs)

        # Cache serialization has its own lock: a busy SQLite cache must never
        # hold the live quote lock. Newer publications cannot overtake an older
        # cache commit, and a generation rejected before publication writes none.
        with publication_lock:
            with self._intraday_lock:
                if (self.instrument_code != code or self._price_revision != revision
                        or generations[channel] != generation):
                    return result
            view.cache.publish()
            with self._intraday_lock:
                if (self.instrument_code != code or self._price_revision != revision
                        or generations[channel] != generation):
                    return result
                for field in CHANNEL_FIELDS[channel]:
                    if not hasattr(view, field):
                        continue
                    # A chain fetch can provide its indicative proxy spot. It
                    # cannot replace a newer independently published stream.
                    if (field == 'proxy_price' and channel != 'refresh_proxy_price'
                            and getattr(self, field, None) != original_proxy):
                        continue
                    setattr(self, field, getattr(view, field))
        return result

    refresh.instrument_refresh_isolation_version = VERSION
    return refresh


def install_instrument_refresh_isolation(market_class):
    """Idempotently wrap final adapters, including later resilience replacements."""
    for channel in CHANNEL_FIELDS:
        method = getattr(market_class, channel)
        if getattr(method, 'instrument_refresh_isolation_version', None) != VERSION:
            setattr(market_class, channel, _isolated_refresh(method, channel))
