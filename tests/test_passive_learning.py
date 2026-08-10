import json, sqlite3
import pytest
from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.passive_learning import (
    PassiveLearningEngine, _advance_trading_time, _session_state,
    _trading_seconds_between,
)

@pytest.fixture
def passive(tmp_path):
    cache=DiskCache(str(tmp_path/"cache.db"))
    engine=PassiveLearningEngine(str(tmp_path/"trades.db"),
        Settings(demo=True,data_dir=str(tmp_path)),cache)
    yield engine
    engine.close(); cache.close()

def fixture_forecast(ts):
    return {"version":"test","probability_measure":"risk_neutral_Q",
        "reference_volatility_annual":.2,
        "standardized_barriers":{"1.0":{"up":.3,"down":.2,"no_touch":.5}},
        "quantiles_log_return":{"q10":-.02,"q25":-.01,"q50":0,
            "q75":.01,"q90":.02},"forecast_created_ts":ts}

def test_collector_runs_without_active_trade_and_demo_is_excluded(passive):
    result=passive.step(now=1_700_000_000)
    assert result["created"]
    status=passive.status()
    assert status["active_trade_required"] is False
    assert status["raw_n"] == 7
    assert status["evidence_eligible_n"] == 0

def test_no_lookahead_and_t0_immutability(passive):
    ts=1_700_000_000.
    with pytest.raises(ValueError,match="post-capture"):
        passive.capture_observation(instrument="NAS100",captured_ts=ts,
            market_price=100,features={"source_observation_ts":ts+1},
            forecast=fixture_forecast(ts),provenance={},trigger_reason="test")
    ids=passive.capture_observation(instrument="NAS100",captured_ts=ts,
        market_price=100,features={"source_observation_ts":ts},
        forecast=fixture_forecast(ts),provenance={},trigger_reason="test")
    with pytest.raises(sqlite3.IntegrityError,match="immutable"):
        with passive._conn:
            passive._conn.execute(
                "UPDATE passive_market_observations SET forecast_json='{}' "
                "WHERE observation_id=?",(ids[0],))

def test_pending_then_resolved_only_from_recorded_future(passive):
    ts=1_700_000_000.
    ids=passive.capture_observation(instrument="NAS100",captured_ts=ts,
        market_price=100,features={"source_observation_ts":ts},
        forecast=fixture_forecast(ts),provenance={},trigger_reason="test")
    passive.record_market_point("NAS100",ts,100)
    assert passive.resolve_due(now=ts+14*60)=={}
    # 15m path sampled at <=5m gaps, including exact horizon.
    for minute,price in ((5,100.5),(10,101),(15,102)):
        passive.record_market_point("NAS100",ts+minute*60,price)
    report=passive.resolve_due(now=ts+15*60)
    assert report["resolved"] == 1
    row=passive._conn.execute(
        "SELECT * FROM passive_market_observations WHERE observation_id=?",
        (ids[0],)).fetchone()
    outcome=json.loads(row["outcome_json"])
    assert row["resolution_status"]=="resolved"
    assert outcome["resolved_from"]=="recorded_real_market_path"

def test_overlap_effective_n_is_conservative_and_report_has_baselines(passive):
    base=1_700_000_000.
    rows=[]
    for i in range(20):
        rows.append({"instrument":"NAS100","horizon_minutes":1440,
            "captured_ts":base+i*900,"target_ts":base+i*900+86400})
    assert passive._effective_n(rows) < len(rows)
    report=passive.calibration_report()
    assert set(report["baselines"]) >= {
        "zero_return","historical_base_rate","random_walk_no_drift",
        "current_production_forecast","identity_q"}
    assert report["promotion_allowed"] is False

def test_dataset_layers_never_merge(passive):
    edge=passive.edge_report({"observations":3,"resolved_trades":2})
    assert edge["datasets_mixed"] is False
    assert edge["market_forecast_edge"]["dataset"]=="passive_market"
    assert edge["virtual_management_edge"]["dataset"]=="virtual_position"
    assert edge["real_management_edge"]["dataset"]=="real_user_trade"


def test_purged_embargo_split_is_chronological_and_non_overlapping(passive):
    base=1_700_000_000.
    rows=[{"observation_id":f"o-{i}","instrument":"NAS100",
           "horizon_minutes":60,"captured_ts":base+i*3600,
           "target_ts":base+(i+1)*3600} for i in range(30)]
    split=passive.purged_embargo_split(rows)
    assert split["random_shuffle"] is False
    assert split["purge_applied"] is True
    assert split["embargo_applied"] is True
    assert split["embargo_seconds"] == 3600
    by_id={row["observation_id"]:row for row in rows}
    if split["train_ids"] and split["validation_ids"]:
        assert max(by_id[x]["target_ts"] for x in split["train_ids"]) < min(
            by_id[x]["captured_ts"] for x in split["validation_ids"])
    if split["validation_ids"] and split["test_ids"]:
        assert max(by_id[x]["target_ts"] for x in split["validation_ids"]) < min(
            by_id[x]["captured_ts"] for x in split["test_ids"])


def test_exchange_session_and_trading_horizon_skip_weekend():
    # Friday 15:50 New York during standard time.
    import datetime as dt
    start=dt.datetime(2026,1,9,20,50,tzinfo=dt.timezone.utc).timestamp()
    assert _session_state("NAS100",start)["is_open"] is True
    target=_advance_trading_time("NAS100",start,20)
    local=dt.datetime.fromtimestamp(target,dt.timezone.utc)
    assert local.weekday() == 0
    assert _trading_seconds_between("NAS100",start,target) == pytest.approx(1200)


def test_event_trigger_is_versioned_spaced_and_deterministic():
    import json
    last={"captured_ts":1000.,"market_price":100.,
          "forecast_json":json.dumps({"sigma_h_return":.01})}
    assert PassiveLearningEngine._event_trigger_reason(
        now=1100.,last=last,price=102.) is None
    assert PassiveLearningEngine._event_trigger_reason(
        now=1400.,last=last,price=100.2) is None
    assert PassiveLearningEngine._event_trigger_reason(
        now=1400.,last=last,price=101.) == "large_price_displacement"
    assert PassiveLearningEngine._event_trigger_reason(
        now=2000.,last=last,price=100.) == "cadence"


def test_reliability_and_pinball_contracts_use_exact_counts(passive):
    rows=[{"instrument":"NAS100","horizon_minutes":15,
           "captured_ts":i*1000.,"target_ts":i*1000.+900}
          for i in range(4)]
    table=passive._reliability_table(
        [.12,.18,.72,.78],[0.,1.,1.,1.],rows)
    assert table[1]["raw_n"] == 2
    assert table[1]["actual_rate"] == .5
    assert table[7]["raw_n"] == 2
    from seiltanzer.passive_learning import _pinball_score
    assert _pinball_score([0.,1.],[1.,0.],.5)["pinball_loss"] == .5


def test_virtual_positions_are_separate_and_resolve_on_real_path(passive):
    ts=1_700_000_000.
    ids=passive.capture_observation(
        instrument="NAS100",captured_ts=ts,market_price=100,
        features={"source_observation_ts":ts},
        forecast=fixture_forecast(ts),provenance={},trigger_reason="test",
        evidence_eligible=True)
    virtual_n=passive._conn.execute(
        "SELECT COUNT(*) FROM virtual_position_observations").fetchone()[0]
    assert virtual_n == 16
    passive.record_market_point("NAS100",ts,100)
    for minute in range(5,61,5):
        passive.record_market_point(
            "NAS100",ts+minute*60,100*(1+minute/3000))
    report=passive.resolve_due(now=ts+60*60)
    assert report["resolved"] >= 3
    rows=passive._conn.execute(
        "SELECT * FROM virtual_position_observations "
        "WHERE horizon_minutes=60 AND resolution_status='resolved'").fetchall()
    assert len(rows) == 8
    outcome=json.loads(rows[0]["outcome_json"])
    assert set(outcome["policies"]) == {
        "HOLD","CLOSE_10","CLOSE_25","CLOSE_50","EXIT"}
    assert outcome["claims_real_user_improvement"] is False
    edge=passive.edge_report()
    virtual=edge["virtual_management_edge"]
    assert virtual["dataset"] == "virtual_position"
    assert virtual["raw_n"] == 8
    assert virtual["mixed_with_real_trades"] is False
