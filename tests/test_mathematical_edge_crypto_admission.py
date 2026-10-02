"""Synthetic contracts check quote/venue admission, not crypto edge evidence."""
from copy import deepcopy
import json
import math
import time
from types import SimpleNamespace

import pytest

from seiltanzer import mathematical_edge as edge
from seiltanzer.config import CRYPTO_INSTRUMENTS


def fixture(tmp_path, monkeypatch, code='BTCUSD'):
    now = time.time()
    end = math.floor(now/300)*300
    n = len(edge.FEATURES)
    head = {'mean': [0.]*n, 'scale': [1.]*n, 'beta': [math.log(.2/.8)]+[0.]*n, 'baseline': .5}
    diagnostics = {'gain_mbit': 20., 'positive_blocks': 3, 'blocks': 3, 'test_n': 120, 'working_supported': True}
    model = {'instrument': code, 'horizon_minutes': 15, 'feature_contract': edge.FEATURE_CONTRACT,
             'features': list(edge.FEATURES), 'target_contract': deepcopy(edge.TARGET_CONTRACT),
             'source_sha256': 'b'*64, 'training_cutoff': now-3600,
             'heads': {'direction': deepcopy(head), 'movement': deepcopy(head)},
             'diagnostics': {'direction': deepcopy(diagnostics), 'movement': deepcopy(diagnostics)},
             'path_target_contract': deepcopy(edge.PATH_TARGET_CONTRACT),
             'path_heads': {key: {**diagnostics, 'head': deepcopy(head), 'target_semantics': value,
                                 'horizon_minutes': 15, 'training_cutoff': now-3600}
                            for key, value in edge.PATH_TARGET_CONTRACT.items()}}
    model['model_sha256'] = edge.fingerprint(model)
    source = {'instrument': code, 'provider': 'Binance', 'ticker': CRYPTO_INSTRUMENTS[code].binance_symbol,
              'interval': '5m', 'source_kind': 'SINGLE_PROVIDER_COMPLETED_5M',
              'validated_bars_sha256': 'b'*64,
              'source_semantics': {'provider_quote_currency': 'USDT', 'configured_quote_currency': 'USDT',
                                   'currency_basis_mismatch': False, 'synthetic_price_history': False}}
    report = {'contract_version': edge.CONTRACT, 'created_ts': now-1,
              'published_for_sha': 'a'*40, 'automatic_execution': False, 'production_authority': False,
              'instruments': {code: model}, 'sources': [source]}
    destination = tmp_path/'research'/'mathematical_edge_latest.json'
    destination.parent.mkdir()
    raw = [[end-(13-i)*300+k*60, 100., 101., 99., 100.]
           for i in range(13) for k in range(5)]
    authority = {'provider': 'Binance', 'source_symbol': CRYPTO_INSTRUMENTS[code].binance_symbol,
                 'target_instrument': code, 'source_verified': True, 'derived': False,
                 'observed_ts': end, 'available_at': now-1}
    engine = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path),
                             market=SimpleNamespace(instrument_code=code, intraday_ohlcv=raw,
                                                    intraday_source_authority=authority))
    from seiltanzer import runtime_git_identity
    monkeypatch.setattr(runtime_git_identity, 'runtime_git_sha', lambda: 'a'*40)
    return engine, {'ts': now, 'instrument': code}, {'instrument': code, 'direction': 'long'}, report, destination


@pytest.mark.parametrize('code', CRYPTO_INSTRUMENTS)
@pytest.mark.parametrize('case', ['legacy_no_source', 'coinbase_usd', 'kraken_usd', 'bare_validated_flag',
                                 'other_usdt_venue', 'unbound_training_hash', 'unverified_live_series'])
def test_unvalidated_crypto_transfer_blocks_every_prediction_and_contribution(tmp_path, monkeypatch, code, case):
    engine, tick, trade, report, destination = fixture(tmp_path, monkeypatch, code)
    source = report['sources'][0]
    reason = 'CRYPTO_USD_USDT_PRICE_MAPPING_UNVALIDATED'
    if case == 'legacy_no_source':
        report.pop('sources')
        reason = 'CRYPTO_TRAINING_SOURCE_PROVENANCE_UNAVAILABLE'
    elif case in ('coinbase_usd', 'kraken_usd', 'bare_validated_flag'):
        source['provider'] = 'Kraken' if case == 'kraken_usd' else 'Coinbase Exchange'
        source['source_semantics'].update(provider_quote_currency='USD', currency_basis_mismatch=True)
        if case == 'bare_validated_flag':
            source['proxy_mapping'] = {'validated': True, 'mapping_id': 'assertion-only'}
    elif case == 'other_usdt_venue':
        source['provider'] = 'Other venue'
        reason = 'CRYPTO_PRICE_VENUE_OR_SERIES_MAPPING_UNVALIDATED'
    elif case == 'unbound_training_hash':
        source['validated_bars_sha256'] = 'c'*64
        reason = 'CRYPTO_MODEL_TRAINING_SOURCE_HASH_UNBOUND'
    else:
        engine.market.intraday_source_authority['source_verified'] = False
        reason = 'CRYPTO_LIVE_CONFIGURED_SERIES_AUTHORITY_UNAVAILABLE'
    destination.write_text(json.dumps(report))
    def forbidden(*args):
        raise AssertionError('No crypto prediction may run before price-series admission')
    monkeypatch.setattr(edge, 'predict', forbidden)
    monkeypatch.setattr(edge, 'runtime_path_predictions', forbidden)
    profile = edge.runtime_profile(engine, tick, trade)
    assert not profile['available'] and profile['weight_fraction'] == 0
    assert profile['reason'] == reason
    assert not profile.get('path_predictions') and not profile.get('probabilities')
    combined = edge.combine_math_profile({'available': False, 'weight_fraction': 0.}, profile)
    assert combined['mathematical_component_weight'] == combined['mathematical_extended_component_weight'] == 0


@pytest.mark.parametrize('code', CRYPTO_INSTRUMENTS)
def test_exact_configured_usdt_series_can_pass_only_bound_provenance(tmp_path, monkeypatch, code):
    engine, tick, trade, report, destination = fixture(tmp_path, monkeypatch, code)
    destination.write_text(json.dumps(report))
    monkeypatch.setattr(edge, 'runtime_gex_diagnostics', lambda *a: {'available': False})
    profile = edge.runtime_profile(engine, tick, trade)
    assert profile['available'] and profile['weight_fraction'] > 0
    assert profile['path_predictions']
    assert profile['training_price_source_admission']['reason'] == 'EXACT_CONFIGURED_BINANCE_USDT_TRAINING_SERIES'
    assert 'Binance USDT' in profile['runtime_price_feature_source']


def test_report_crypto_source_supported_search_is_diagnostic_without_transfer_validation(monkeypatch):
    from scripts import run_mathematical_edge as runner
    model = {'instrument': 'BTCUSD', 'status': 'WORKING_SUPPORTED', 'search_completed': True,
             'source_sha256': 'b'*64, 'diagnostics': {'direction': {'working_supported': True}},
             'training_cutoff': 600., 'path_heads': {}}
    monkeypatch.setattr(runner, 'train_instrument', lambda *a, **kw: deepcopy(model))
    source = {'instrument': 'BTCUSD', 'provider': 'Coinbase Exchange', 'ticker': 'BTC-USD',
              'bars': [{'bar_end_ts': 600., 'open': 100., 'high': 101., 'low': 99., 'close': 100.}],
              'source_semantics': {'provider_quote_currency': 'USD', 'configured_quote_currency': 'USDT',
                                   'currency_basis_mismatch': True}}
    report = runner.build_report([source], {}, 600.)
    assert report['instruments']['BTCUSD']['status'] == 'WORKING_SUPPORTED'
    matrix = report['instrument_matrix']['BTCUSD']
    assert matrix['supported_heads'] == ['direction']
    assert matrix['management_role'] == 'DIAGNOSTIC_ONLY_PRICE_SERIES_MAPPING_UNVALIDATED'
    assert not matrix['training_price_source_admission']['available']
