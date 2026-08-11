import datetime as dt
import json

from seiltanzer.config import INSTRUMENTS, Settings
from seiltanzer.g1_q_collector import Q_COLLECTOR_CONTRACT_VERSION, Q_COLLECTOR_CADENCE_SEC
from seiltanzer.passive_learning import PassiveLearningEngine


class FakeQFeed:
    def __init__(self, instrument: str, now: float, *, direct=True, chain=True):
        self.instrument = INSTRUMENTS[instrument]
        self.instrument_code = instrument
        self._now = now
        self._direct = direct
        self._chain_enabled = chain
        self.price = None
        self.chain = None
        self.market_regime = "NORMAL"

    def refresh_price(self):
        self.price = {
            "value": 2500.0,
            "ts": self._now,
            "source": "Swissquote XAU/USD" if self._direct else "Yahoo fallback",
            "status": "live",
            "fresh": True,
        }

    def refresh_proxy_price(self):
        return None

    def refresh_chain(self):
        if not self._chain_enabled:
            self.chain = None
            return
        expiry = self._now + 2 * 86400.0
        proxy = self.instrument.options_proxy
        self.chain = {
            "ts": self._now,
            "source": f"yfinance {proxy} options 2026-08-13",
            "status": "live",
            "fresh": True,
            "metrics": {
                "proxy": proxy,
                "proxy_spot": 250.0,
                "spot": 250.0,
                "proxy_transform": self.instrument.proxy_transform,
                "expiry": "2026-08-13",
                "expiry_ts_utc": expiry,
                "t_years": 2.0 / 365.0,
                "density": {
                    "strikes": [175.0, 200.0, 225.0, 250.0, 275.0, 300.0, 325.0],
                    "q": [0.05, 0.2, 0.7, 1.0, 0.7, 0.2, 0.05],
                },
                "implied_move": {"move_frac": 0.02},
                "skew": 0.0,
            },
        }


def _tuesday_utc(hour=10):
    return dt.datetime(2026, 8, 11, hour, 0, tzinfo=dt.timezone.utc).timestamp()


def test_independent_q_collector_creates_only_native_expiry_row(tmp_path, monkeypatch):
    import seiltanzer.g1_q_collector as collector

    now = _tuesday_utc(10)
    monkeypatch.setattr(collector.time, "time", lambda: now)
    engine = PassiveLearningEngine(str(tmp_path / "q-only.db"), Settings(), cache=None)
    engine._feeds["XAU"] = FakeQFeed("XAU", now)

    result = engine.g1_q_collect_instrument("XAU", now)
    assert result["attempted"] is True
    assert result["blocker"] is None
    assert result["observation_id"].endswith("-native-expiry")

    rows = [dict(row) for row in engine._conn.execute(
        "SELECT observation_id,horizon_minutes,forecast_json,observation_origin "
        "FROM passive_market_observations ORDER BY observation_id"
    ).fetchall()]
    assert len(rows) == 1
    assert rows[0]["observation_id"].endswith("-native-expiry")
    assert rows[0]["observation_origin"] == "background_collector"
    forecast = json.loads(rows[0]["forecast_json"])
    assert forecast["horizon_kind"] == "option_native_expiry"
    assert forecast["probability_measure"] == "risk_neutral_Q_terminal"
    assert forecast["q_collector_contract_version"] == Q_COLLECTOR_CONTRACT_VERSION

    status = engine.g1_q_status()
    assert status["capture_attempt_n"] == 1
    assert status["successful_q_capture_n"] == 1
    assert status["q_collector_contract_version"] == Q_COLLECTOR_CONTRACT_VERSION
    assert status["q_collector_cadence_sec"] == Q_COLLECTOR_CADENCE_SEC
    assert status["q_collector_refinement_version"] == "q-independent-collector-calendar-v1"
    engine.close()


def test_q_collector_cadence_is_persisted_and_survives_new_engine(tmp_path, monkeypatch):
    import seiltanzer.g1_q_collector as collector

    now = _tuesday_utc(10)
    db = str(tmp_path / "cadence.db")
    monkeypatch.setattr(collector.time, "time", lambda: now)
    engine = PassiveLearningEngine(db, Settings(), cache=None)
    engine._feeds["XAU"] = FakeQFeed("XAU", now)
    first = engine.g1_q_collect_instrument("XAU", now)
    assert first["attempted"] is True
    engine.close()

    engine2 = PassiveLearningEngine(db, Settings(), cache=None)
    engine2._feeds["XAU"] = FakeQFeed("XAU", now + 100)
    second = engine2.g1_q_collect_instrument("XAU", now + 100)
    assert second == {"instrument": "XAU", "attempted": False, "reason": "cadence_not_due"}
    assert engine2.g1_q_status()["capture_attempt_n"] == 1
    engine2.close()


def test_no_q_source_and_closed_market_are_immediately_diagnostic(tmp_path):
    now = _tuesday_utc(10)
    engine = PassiveLearningEngine(str(tmp_path / "blockers.db"), Settings(), cache=None)

    no_source = engine.g1_q_collect_instrument("JPY100", now)
    assert no_source["attempted"] is True
    assert no_source["blocker"] == "NO_Q_SOURCE_CONFIGURED"

    # 10:00 UTC is before the New York cash session used by the evidence contract.
    closed = engine.g1_q_collect_instrument("NAS100", now)
    assert closed["attempted"] is True
    assert closed["blocker"] == "MARKET_CLOSED"

    status = engine.g1_q_status()
    assert status["capture_attempt_n"] == 2
    assert status["successful_q_capture_n"] == 0
    assert status["top_blockers"]["NO_Q_SOURCE_CONFIGURED"] == 1
    assert status["top_blockers"]["MARKET_CLOSED"] == 1
    assert status["runtime_validated"] is False
    engine.close()


def test_non_direct_target_is_attempted_but_never_creates_q_row(tmp_path, monkeypatch):
    import seiltanzer.g1_q_collector as collector

    now = _tuesday_utc(10)
    monkeypatch.setattr(collector.time, "time", lambda: now)
    engine = PassiveLearningEngine(str(tmp_path / "proxy.db"), Settings(), cache=None)
    engine._feeds["XAU"] = FakeQFeed("XAU", now, direct=False)

    result = engine.g1_q_collect_instrument("XAU", now)
    assert result["attempted"] is True
    assert result["blocker"] == "TARGET_PRICE_NON_DIRECT"
    assert engine._conn.execute("SELECT COUNT(*) FROM passive_market_observations").fetchone()[0] == 0
    status = engine.g1_q_status()
    assert status["capture_attempt_n"] == 1
    assert status["successful_q_capture_n"] == 0
    engine.close()


def test_independent_q_row_resolves_through_g1a_and_g1b(tmp_path, monkeypatch):
    import seiltanzer.g1_q_collector as collector

    now = _tuesday_utc(10)
    monkeypatch.setattr(collector.time, "time", lambda: now)
    engine = PassiveLearningEngine(str(tmp_path / "q-resolve.db"), Settings(), cache=None)
    engine._feeds["XAU"] = FakeQFeed("XAU", now)

    result = engine.g1_q_collect_instrument("XAU", now)
    obs_id = result["observation_id"]
    row = dict(engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE observation_id=?", (obs_id,)
    ).fetchone())
    forecast = json.loads(row["forecast_json"])
    assert forecast["calendar_ttm_seconds"] == row["target_ts"] - row["captured_ts"]

    target = float(row["target_ts"])
    engine.record_market_point(
        "XAU", target, 2525.0,
        source="Swissquote XAU/USD", quality=0.99, kind="direct",
    )
    assert engine._resolve_one(row, target) == "resolved"
    engine._g1_sync_membership(limit=5000)
    membership = dict(engine._conn.execute(
        "SELECT * FROM g1_dataset_membership WHERE observation_id=?", (obs_id,)
    ).fetchone())
    assert membership["q_to_p_eligible"] == 1

    outcome = json.loads(engine._conn.execute(
        "SELECT outcome_json FROM passive_market_observations WHERE observation_id=?", (obs_id,)
    ).fetchone()[0])
    assert outcome["terminal"]["terminal_pit_q"] is not None
    status = engine.g1_q_status()
    assert status["resolved_q_observation_n"] == 1
    assert status["q_to_p_eligible_n"] == 1
    assert status["g1b_q_metrics_eligible_n"] == 1
    engine.close()
