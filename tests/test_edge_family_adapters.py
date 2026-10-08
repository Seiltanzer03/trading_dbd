from copy import deepcopy
from datetime import datetime, timezone

import pytest

from seiltanzer.edge_family_adapters import (
    FAMILIES, FEATURE_CONTRACT, MODEL_CONTRACT, build_edge_family_evidence,
)


T0 = 1_780_000_000.


def source(**values):
    return {"source_id": "measured:exchange:1", "source_verified": True,
            "instrument": "NAS100", "observed_ts": T0 - 10, "received_ts": T0 - 8,
            "quality": .8, **values}


def model(family, feature):
    return {"contract_version": MODEL_CONTRACT, "feature_contract_version": FEATURE_CONTRACT, "family_id": family,
            "instrument": "NAS100", "component_id": "mathematical_edge",
            "model_version": "measured-" + family, "dataset_sha256": "a" * 64,
            "train_end_ts": T0 - 86400, "validation_start_ts": T0 - 72000,
            "validation_end_ts": T0 - 3600, "trained_at": T0 - 1800,
            "horizon_minutes": 15, "score_scale_r": .2,
            "feature_windows_sec": {feature: 300.} if family == "intermarket" else {},
            "validation": {"status": "OOS_VALIDATED", "point_in_time": True,
                           "purged_split": True, "sample_count": 100, "fold_count": 3,
                           "proper_score_gain": .02, "costs_included": True,
                           "outcomes": "OBSERVED_NET_ACTION_DELTA_VS_HOLD"},
            "action_models": {"CLOSE_25": {"validated": True, "intercept_r": .1,
                                           "coefficients": {feature: .02}}}}


def snapshot(family, data, feature):
    return {"captured_ts": T0, "strategy": {"instrument": "NAS100"},
            "edge_family_sources": {family: data},
            "edge_family_models": {family: model(family, feature)}}


def test_rejected_calendar_cannot_erase_an_admitted_session_context():
    admitted, feature = family_fixture("session")
    rejected = {**admitted, "source_id": "unmapped-calendar", "instrument": "NYSECASH"}
    rows = [build_edge_family_evidence(snapshot("session", packets, feature))["families"]["session"]
            for packets in ([admitted, rejected], [rejected, admitted])]
    assert rows[0]["features"] == rows[1]["features"]
    assert rows[0]["session_context"] == rows[1]["session_context"]
    assert rows[0]["session_context"]["calendar_complete"] is True
    assert rows[0]["session_context"]["calendar_id"] == admitted["calendar_id"]
    assert rows[0]["rejected_sources"] == rows[1]["rejected_sources"]


def test_session_context_tracks_the_freshest_admitted_calendar():
    fresh, feature = family_fixture("session")
    old = {**fresh, "source_id": "older-calendar", "observed_ts": T0-30,
           "received_ts": T0-20, "calendar_id": "older", "session_id": "older-session",
           "session_open_ts": T0-7200, "session_close_ts": T0-15}
    rows = [build_edge_family_evidence(snapshot("session", packets, feature))["families"]["session"]
            for packets in ([fresh, old], [old, fresh])]
    assert rows[0]["features"] == rows[1]["features"]
    assert rows[0]["session_context"] == rows[1]["session_context"]
    assert rows[0]["session_context"]["calendar_id"] == fresh["calendar_id"]
    assert rows[0]["session_context"]["market_open"] is True
    assert rows[0]["feature_provenance"][feature]["source_id"] == fresh["source_id"]


def test_fresh_envelope_cannot_reage_stale_book_children():
    data, feature = family_fixture('order_flow')
    data['previous_top']['ts'] = T0-3601
    data['current_top']['ts'] = T0-3600
    result = build_edge_family_evidence(snapshot('order_flow', data, feature))
    assert result['families']['order_flow']['features'] == {}
    assert result['components'] == []


def test_book_feature_clock_must_match_the_current_observation():
    data, feature = family_fixture('order_flow')
    data['previous_top']['ts'] -= 1
    data['current_top']['ts'] -= 1
    result = build_edge_family_evidence(snapshot('order_flow', data, feature))
    assert result['families']['order_flow']['features'] == {}


def test_family_net_action_forecast_must_match_frozen_comparison_horizon():
    data = source(features={"macro.expected_rate_change": -.2})
    frozen = snapshot("macro", data, "macro.expected_rate_change")
    frozen["policy_manager"] = {"inputs": {"horizon_minutes": 240}}
    result = build_edge_family_evidence(frozen)
    assert not result["families"]["macro"]["forecast_available"]
    assert result["families"]["macro"]["forecast_rejections"][0]["reason"] == "MODEL_FORECAST_HORIZON_MISMATCH"


def family_fixture(family):
    if family == "macro":
        return source(features={"macro.expected_rate_change": -.2}), "macro.expected_rate_change"
    if family == "event":
        release = source(source_id="release:CPI:1", release_id="CPI:1", event_type="CPI",
                         actual=3.1, published_at=T0-10, period="2026-04", unit="percent")
        release["consensus"] = source(source_id="consensus:CPI:1", release_id="CPI:1", value=3.,
                                      period="2026-04", unit="percent", observed_ts=T0-900,
                                      received_ts=T0-600)
        return release, "event.cpi.surprise"
    if family == "order_flow":
        book = source(kind="exchange_order_book", venue="CME",
                      previous_top={"bid_price": 100., "bid_size": 10., "ask_price": 101., "ask_size": 20., "ts": T0-11},
                      current_top={"bid_price": 100., "bid_size": 30., "ask_price": 101., "ask_size": 10., "ts": T0-10})
        return book, "flow.ofi_over_depth"
    if family == "intermarket":
        return source(linked_returns=[{"leader": "SP500", "start_ts": T0-310, "end_ts": T0-10,
                                       "start_price": 100., "end_price": 101.}]), "intermarket.SP500.return"
    if family == "positioning":
        return source(kind="cot_report", report_ts=T0-4*86400, published_at=T0-10, net_position=30.,
                      historical_positions=[{"report_ts": T0-11*86400, "available_at": T0-8*86400, "net_position": 10.},
                                            {"report_ts": T0-18*86400, "available_at": T0-15*86400, "net_position": 40.}]), "positioning.percentile"
    if family == "value_carry":
        return source(kind="valuation", features={"value.earnings_revision": .1}), "value.earnings_revision"
    if family == "option":
        return source(kind="option_chain", features={"option.iv_change": .02}), "option.iv_change"
    if family == "session":
        return source(calendar_complete=True, calendar_id="XNYS-2026-v1", calendar_available_at=T0-86400,
                      session_id="XNYS-2026-05-28", session_open_ts=T0-3600, session_close_ts=T0+18000), "session.minutes_from_open"
    raise AssertionError(family)


@pytest.mark.parametrize("family", FAMILIES)
def test_each_measured_family_produces_real_scores_and_unvalidated_does_not(family):
    data, feature = family_fixture(family)
    frozen = snapshot(family, data, feature)
    before = deepcopy(frozen)
    result = build_edge_family_evidence(frozen)
    assert frozen == before
    assert result["families"][family]["available"]
    assert result["families"][family]["forecast_available"]
    assert result["components"][0]["component_id"] == "mathematical_edge"
    assert result["components"][0]["scores"]["CLOSE_25"] > 0
    assert result["components"][0]["scores"]["HOLD"] == 0
    assert result["components"][0]["family_ids"] == [family]
    assert result["risk_overrides"] is False
    frozen["edge_family_models"][family]["validation"]["status"] = "RESEARCH_ONLY"
    rejected = build_edge_family_evidence(frozen)
    assert rejected["components"] == []
    assert rejected["families"][family]["forecast_available"] is False


def test_empty_snapshot_is_eight_honest_unresolved_families():
    result = build_edge_family_evidence({"captured_ts": T0, "strategy": {"instrument": "XAU"}})
    assert set(result["families"]) == set(FAMILIES)
    assert result["components"] == []
    assert result["forecast_available_count"] == 0
    for row in result["families"].values():
        assert row["readiness"] == "NEEDS_DATA"
        assert row["voting_weight"] == 0
        assert row["needs_data"]
    assert result["families"]["session"]["session_context"]["market_open"] is None


@pytest.mark.parametrize("field", ["observed_ts", "received_ts", "published_at"])
def test_future_source_or_late_receipt_never_votes(field):
    data, feature = family_fixture("macro")
    data[field] = T0 + 1
    result = build_edge_family_evidence(snapshot("macro", data, feature))
    assert result["components"] == []
    assert result["families"]["macro"]["availability"] == "UNAVAILABLE"


def test_previous_month_change_is_not_consensus_surprise():
    data, feature = family_fixture("event")
    del data["consensus"]
    data["previous"] = 2.
    result = build_edge_family_evidence(snapshot("event", data, feature))
    assert result["components"] == []
    assert result["families"]["event"]["features"] == {}


@pytest.mark.parametrize("change", ["after_release", "wrong_unit", "wrong_period", "wrong_release"])
def test_event_consensus_must_precede_same_release(change):
    data, feature = family_fixture("event")
    if change == "after_release":
        data["consensus"]["received_ts"] = T0-5
    elif change == "wrong_unit":
        data["consensus"]["unit"] = "bp"
    elif change == "wrong_period":
        data["consensus"]["period"] = "2026-03"
    else:
        data["consensus"]["release_id"] = "CPI:other"
    assert not build_edge_family_evidence(snapshot("event", data, feature))["components"]


def test_real_event_retains_consensus_and_release_lineage():
    data, feature = family_fixture("event")
    result = build_edge_family_evidence(snapshot("event", data, feature))
    assert result["components"][0]["source_ids"] == ["consensus:CPI:1", "release:CPI:1"]
    assert result["components"][0]["evidence_family_ids"] == ["release:CPI:1"]
    assert result["families"]["event"]["features"][feature] == pytest.approx(.1)


def test_candle_delta_is_not_exchange_flow():
    data, feature = family_fixture("order_flow")
    data["kind"] = "candle_volume_profile"
    result = build_edge_family_evidence(snapshot("order_flow", data, feature))
    assert result["components"] == []
    assert result["families"]["order_flow"]["features"] == {}


def test_measured_ofi_counts_both_sides():
    data, feature = family_fixture("order_flow")
    result = build_edge_family_evidence(snapshot("order_flow", data, feature))
    features = result["families"]["order_flow"]["features"]
    assert features["flow.ofi"] == 30.
    assert features["flow.ofi_over_depth"] == .75


def test_correlation_context_is_not_lead_lag_forecast():
    result = build_edge_family_evidence({"captured_ts": T0, "strategy": {"instrument": "NAS100"},
                                        "policy_manager": {"evidence": {"correlation": {"available": True, "rho": .9}}}})
    assert not result["families"]["intermarket"]["available"]
    assert result["components"] == []


def test_future_cot_values_excluded_from_percentile():
    data, feature = family_fixture("positioning")
    data["historical_positions"].append({"report_ts": T0-86400, "available_at": T0+1, "net_position": -100.})
    result = build_edge_family_evidence(snapshot("positioning", data, feature))
    features = result["families"]["positioning"]["features"]
    assert features["positioning.history_n"] == 2
    assert features["positioning.percentile"] == .5


def test_broker_carry_is_real_outcome_cost_without_extra_vote():
    data = source(kind="broker_carry", charge_currency_per_rollover=4., risk_currency_per_unit=100.,
                  currency="USD", charge_basis="per_unit_of_remaining_position", next_rollover_ts=T0+100)
    result = build_edge_family_evidence(snapshot("value_carry", data, "carry.cost"))
    assert result["components"] == []
    assert result["families"]["value_carry"]["readiness"] == "ECONOMICS_ONLY"
    assert result["economics_adjustments"][0]["cost_r_per_rollover"] == .04
    assert result["economics_adjustments"][0]["included_in_policy_economics"] is False


def test_existing_projected_option_surface_remains_one_q_context_family():
    frozen = {"captured_ts": T0, "strategy": {"instrument": "XAU"},
              "policy_manager": {"evidence": {"iv_surface": {"available": True, "snapshot_age_sec": 30,
                                                            "real_expiries": [{"days": 2, "atm_iv_pct": 22.}],
                                                            "local_24h": [{"hours": 24, "atm_iv_pct": 23.}]},
                                               "data_quality": {"chain": {"source": "GLD chain"}, "proxy_quality": .7}}}}
    result = build_edge_family_evidence(frozen)
    option = result["families"]["option"]
    assert option["available"] and not option["forecast_available"]
    assert option["evidence_family_ids"] == ["XAU:option_distribution"]
    assert option["existing_consumer"] == "quantitative_base"
    assert option["dealer_inventory_observed"] is False
    assert result["components"] == []


def test_invalid_proxy_is_not_promoted_to_trade_instrument():
    data, feature = family_fixture("order_flow")
    data["instrument"] = "NQ"
    frozen = snapshot("order_flow", data, feature)
    assert build_edge_family_evidence(frozen)["components"] == []
    data["proxy_mapping"] = {"validated": True, "mapping_id": "NQ-NAS100-basis-v1",
                             "source_instrument": "NQ", "target_instrument": "NAS100", "validated_at": T0-86400}
    result = build_edge_family_evidence(frozen)
    assert result["components"]
    assert result["families"]["order_flow"]["feature_provenance"][feature]["proxy_mapping"]["mapping_id"]


@pytest.mark.parametrize("change", ["overlap", "wrong_instrument", "regime", "simulated_validation", "missing_costs", "stale_model"])
def test_model_admission_requires_observed_purged_net_evidence(change):
    data, feature = family_fixture("macro")
    frozen = snapshot("macro", data, feature)
    artifact = frozen["edge_family_models"]["macro"]
    if change == "overlap":
        artifact["validation_start_ts"] = artifact["train_end_ts"] + 10
    elif change == "wrong_instrument":
        artifact["instrument"] = "XAU"
    elif change == "regime":
        artifact["regime"] = "TREND"
    elif change == "simulated_validation":
        artifact["validation"]["outcomes"] = "MODEL_SCENARIOS"
    elif change == "missing_costs":
        artifact["validation"]["costs_included"] = False
    else:
        artifact["trained_at"] = T0 - 31*86400
        artifact["validation_end_ts"] = T0 - 32*86400
        artifact["validation_start_ts"] = T0 - 33*86400
        artifact["train_end_ts"] = T0 - 34*86400
    assert build_edge_family_evidence(frozen)["components"] == []


def test_session_conditional_measured_net_outcomes_generate_score():
    data, feature = family_fixture("session")
    frozen = snapshot("session", data, feature)
    frozen["edge_family_models"]["session"]["action_models"]["TIME_STOP"] = {
        "validated": True, "kind": "conditional_net_outcomes",
        "bins": [{"conditions": [{"feature": feature, "lower": 30., "upper": 90.}],
                  "sample_count": 60, "mean_delta_net_r": .08}]}
    result = build_edge_family_evidence(frozen)
    assert result["components"][0]["scores"]["TIME_STOP"] == pytest.approx(.4)


def test_us_session_clock_handles_dst_without_inventing_holiday_calendar():
    winter = datetime(2026, 1, 5, 15, tzinfo=timezone.utc).timestamp()
    summer = datetime(2026, 7, 6, 15, tzinfo=timezone.utc).timestamp()
    contexts = [build_edge_family_evidence({"captured_ts": ts, "strategy": {"instrument": "NAS100"}})
                ["families"]["session"]["session_context"] for ts in (winter, summer)]
    assert [row["utc_offset_seconds"] for row in contexts] == [-18000., -14400.]
    assert all(row["market_open"] is None for row in contexts)


def test_duplicate_artifact_cannot_add_extra_family_weight():
    data, feature = family_fixture("macro")
    frozen = snapshot("macro", data, feature)
    artifact = frozen["edge_family_models"]["macro"]
    single = build_edge_family_evidence(frozen)
    frozen["edge_family_models"]["macro"] = [artifact, deepcopy(artifact)]
    duplicate = build_edge_family_evidence(frozen)
    assert duplicate["components"] == single["components"]


def test_official_numeric_macro_is_real_context_without_fictional_surprise():
    release = {"status": "VALID", "release_id": "CPI-2026-04", "official_source_verified": True,
               "available_at": T0-86400, "fetched_at": T0-86400, "payload": {"core_yoy_pct": 3.}}
    frozen = {"captured_ts": T0, "strategy": {"instrument": "NAS100"},
              "macro_context_v1": {"numeric_macro": {"releases": {"CPI": release},
                                                       "candidate_vector": {"macro.cpi_core_yoy_pct": 3.}}}}
    result = build_edge_family_evidence(frozen)
    assert result["families"]["macro"]["features"]["macro.cpi_core_yoy_pct"] == 3.
    assert result["families"]["macro"]["surprise_computed"] is False
    assert not result["families"]["event"]["available"]
    assert result["components"] == []


def test_byte_budget_exclusion_is_explicit_without_inventing_family_forecasts():
    frozen = {"captured_ts": T0, "strategy": {"instrument": "NAS100"},
              "edge_family_budget_status": {"excluded_roots": ["edge_family_models"],
                                            "reason": "EXCLUDED_BY_SNAPSHOT_BYTE_BUDGET"}}
    result = build_edge_family_evidence(frozen)
    assert result["components"] == []
    for row in result["families"].values():
        assert not row["forecast_available"]
        assert row["budget_excluded_roots"] == ["edge_family_models"]
        assert "EXCLUDED_BY_SNAPSHOT_BYTE_BUDGET" in row["reason"]
    frozen["edge_family_models"] = {}
    result = build_edge_family_evidence(frozen)
    assert all("budget_excluded_roots" not in row for row in result["families"].values())


def _conditional_artifact(frozen, family, feature):
    frozen['edge_family_models'][family]['action_models'] = {'CLOSE_25': {
        'validated': True, 'kind': 'conditional_net_outcomes', 'bins': [{
            'conditions': [{'feature': feature, 'lower': -1e6, 'upper': 1e6}],
            'sample_count': 60, 'mean_delta_net_r': .08}]}}


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('kind', ['linear', 'conditional'])
@pytest.mark.parametrize('declaration,value', [('context_only', True), ('horizon_minutes', 240),
                                              ('horizon_minutes', 'invalid')])
def test_runtime_source_applicability_blocks_votes_but_preserves_context(family, kind, declaration, value):
    data, feature = family_fixture(family)
    data[declaration] = value
    frozen = snapshot(family, data, feature)
    frozen['policy_manager'] = {'inputs': {'horizon_minutes': 15}}
    if kind == 'conditional':
        _conditional_artifact(frozen, family, feature)
    result = build_edge_family_evidence(frozen)
    assert result['families'][family]['features'][feature] is not None
    assert result['families'][family]['feature_provenance'][feature][declaration] == value
    assert result['components'] == []


@pytest.mark.parametrize('kind', ['linear', 'conditional'])
@pytest.mark.parametrize('declaration,value', [('context_only', True), ('horizon_minutes', 240)])
def test_event_consensus_applicability_blocks_votes(kind, declaration, value):
    data, feature = family_fixture('event')
    data['consensus'][declaration] = value
    frozen = snapshot('event', data, feature)
    frozen['policy_manager'] = {'inputs': {'horizon_minutes': 15}}
    if kind == 'conditional':
        _conditional_artifact(frozen, 'event', feature)
    result = build_edge_family_evidence(frozen)
    assert result['families']['event']['feature_provenance'][feature]['consensus_provenance'][declaration] == value
    assert result['components'] == []


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('declared', [False, True])
def test_compatible_source_declarations_and_legacy_documents_still_vote(family, declared):
    data, feature = family_fixture(family)
    if declared:
        data.update(context_only=False, horizon_minutes=15)
    frozen = snapshot(family, data, feature)
    frozen['policy_manager'] = {'inputs': {'horizon_minutes': 15}}
    assert build_edge_family_evidence(frozen)['components']


@pytest.mark.parametrize('root_name', ['macro_context_v1', 'macro_t0_context'])
@pytest.mark.parametrize('path', ['root', 'numeric', 'releases', 'release', 'vector',
                                  'fomc', 'semantic', 'fomc_deterministic', 'payload'])
@pytest.mark.parametrize('declaration,value', [('context_only', True), ('horizon_minutes', 240)])
def test_official_macro_applicability_ancestors_block_only_affected_votes(root_name, path, declaration, value):
    fomc = path in {'fomc', 'semantic', 'fomc_deterministic', 'payload'}
    feature = 'macro.fomc.change' if fomc else 'macro.cpi_value'
    frozen = snapshot('macro', {}, feature)
    frozen.pop('edge_family_sources')
    frozen['policy_manager'] = {'inputs': {'horizon_minutes': 15}}
    release = {'status': 'VALID', 'release_id': 'CPI:1', 'official_source_verified': True,
               'available_at': T0-8, 'published_at': T0-10}
    root = {'numeric_macro': {'releases': {'cpi': release}, 'candidate_vector': {feature: 3.1}}}
    if fomc:
        key = 'fomc_deterministic' if path in {'fomc_deterministic', 'payload'} else 'fomc'
        leaf = 'payload' if key == 'fomc_deterministic' else 'semantic'
        root = {key: {**release, 'available': True, leaf: {'change': .1}}}
        target = root[key][leaf] if path in {'semantic', 'payload'} else root[key]
    else:
        numeric = root['numeric_macro']
        target = {'root': root, 'numeric': numeric, 'releases': numeric['releases'],
                  'release': release, 'vector': numeric['candidate_vector']}[path]
    target[declaration] = value
    frozen[root_name] = root
    result = build_edge_family_evidence(frozen)
    assert feature in result['families']['macro']['features']
    assert result['components'] == []


@pytest.mark.parametrize('receipt', [T0-5, None, 'bad', True, 0.])
def test_declared_available_at_cannot_hide_late_or_invalid_consensus_receipt(receipt):
    data, feature = family_fixture('event')
    data['consensus'].update(available_at=T0-600, received_ts=receipt)
    result = build_edge_family_evidence(snapshot('event', data, feature))
    assert result['families']['event']['features'] == {}
    assert result['components'] == []


def test_dual_consensus_clocks_retain_actual_receipt_and_availability():
    data, feature = family_fixture('event')
    data['consensus'].update(available_at=T0-500)
    row = build_edge_family_evidence(snapshot('event', data, feature))['families']['event']
    meta = row['feature_provenance'][feature]['consensus_provenance']
    assert meta['received_ts'] == T0-600
    assert meta['available_at'] == T0-500


def test_late_consensus_availability_is_rejected_even_with_early_receipt():
    data, feature = family_fixture('event')
    data['consensus']['available_at'] = T0-5
    result = build_edge_family_evidence(snapshot('event', data, feature))
    assert result['families']['event']['features'] == {}


def test_legacy_availability_only_consensus_remains_admitted():
    data, feature = family_fixture('event')
    data['consensus']['available_at'] = data['consensus'].pop('received_ts')
    assert build_edge_family_evidence(snapshot('event', data, feature))['components']
