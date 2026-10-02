from types import SimpleNamespace

import pytest

from seiltanzer.config import Settings
from seiltanzer.passive_learning import PassiveLearningEngine


def feed(now):
    return SimpleNamespace(instrument_code="BTCUSD", demo=False, intraday_is_offset=False,
        intraday_ohlcv=[(now-60, 100., 102., 99., 101., 1.), (now, 101., 102., 100., 101., 1.)],
        intraday_source_authority={"provider": "Binance", "source_symbol": "BTCUSDT",
            "target_instrument": "BTCUSD", "source_verified": True, "derived": False,
            "observed_ts": now, "available_at": now})


def test_only_exact_completed_configured_bars_are_retained(tmp_path, monkeypatch):
    now = 1_900_000_020.
    monkeypatch.setattr("seiltanzer.passive_learning.time.time", lambda: now)
    learner = PassiveLearningEngine(str(tmp_path / "trades.db"), Settings(data_dir=str(tmp_path)), None)
    try:
        assert learner.record_configured_intraday_archive(feed(now), now) == 1
        assert learner.record_configured_intraday_archive(feed(now), now) == 0
        rows = learner._conn.execute("SELECT source,bar_end_ts,created_ts FROM passive_market_bars").fetchall()
        assert len(rows) == 1
        assert tuple(rows[0]) == ("binance_1m_direct:BTCUSDT", now, now)
    finally:
        learner.close()


@pytest.mark.parametrize("key,value", [("provider", "Coinbase Exchange"), ("source_symbol", "BTCUSD"),
    ("source_verified", False), ("derived", True), ("available_at", 1_900_000_021.)])
def test_proxy_or_future_feed_never_records_exact_crypto_archive(tmp_path, monkeypatch, key, value):
    now = 1_900_000_020.
    monkeypatch.setattr("seiltanzer.passive_learning.time.time", lambda: now)
    learner = PassiveLearningEngine(str(tmp_path / "trades.db"), Settings(data_dir=str(tmp_path)), None)
    try:
        source = feed(now)
        source.intraday_source_authority[key] = value
        assert learner.record_configured_intraday_archive(source, now) == 0
        assert learner._conn.execute("SELECT count(*) FROM passive_market_bars").fetchone()[0] == 0
    finally:
        learner.close()
