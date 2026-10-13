import hashlib
import importlib
import importlib.util
import json

import pytest

from seiltanzer import edge_family_sources as sources


RECEIVED = 1791898000.


def body():
    return json.dumps([{
        ':id': 'row-gold-20261006', ':created_at': '2026-10-09T19:30:09.098Z',
        ':updated_at': '2026-10-09T19:30:09.098Z',
        'report_date_as_yyyy_mm_dd': '2026-10-06T00:00:00.000',
        'cftc_contract_market_code': '088691', 'market_and_exchange_names': 'GOLD',
        'noncomm_positions_long_all': '245403',
        'noncomm_positions_short_all': '35068', 'open_interest_all': '396109'}]).encode()


def module():
    name = 'seiltanzer.cftc_revision_history'
    assert importlib.util.find_spec(name) is not None, 'missing CFTC revision validator'
    return importlib.import_module(name)


@pytest.mark.parametrize('field,value', [
    (':created_at', None), (':updated_at', '2026-10-09T19:30:09'),
    (':updated_at', '2026-10-14T19:30:09Z'),
    (':updated_at', '2026-10-08T19:30:09Z'),
    ('cftc_contract_market_code', '084691'),
    ('noncomm_positions_long_all', '1.5'),
])
def test_invalid_archive_metadata_is_explicit_and_legacy_net_survives(field, value):
    rows = json.loads(body()); rows[0][field] = value
    raw = json.dumps(rows).encode()
    if field != 'cftc_contract_market_code':
        assert sources.parse_cot(raw, contract='088691', receipt=RECEIVED,
                                 source_id='actual-body')['kind'] == 'cot_report'
    with pytest.raises(ValueError):
        module().capture_versions(raw, contract='088691', received_ts=RECEIVED,
                                  source_id='actual-body')


@pytest.mark.parametrize('mutation', ['raw', 'version', 'receipt', 'publication'])
def test_packet_reparse_rejects_mutated_values_or_authority(mutation):
    m = module()
    packet = m.capture_versions(body(), contract='088691', received_ts=RECEIVED,
                                source_id='actual-body')
    assert m.validate_versions(packet) == packet
    if mutation == 'raw': packet['raw_body_utf8'] += ' '
    elif mutation == 'version': packet['versions'][0]['net_position'] += 1
    elif mutation == 'receipt': packet['received_ts'] = 1
    else: packet['publication_verified'] = True
    with pytest.raises(ValueError): m.validate_versions(packet)


def test_missing_oi_remains_null_and_duplicate_identity_is_rejected():
    m = module(); rows = json.loads(body()); rows[0].pop('open_interest_all')
    packet = m.capture_versions(json.dumps(rows).encode(), contract='088691',
        received_ts=RECEIVED, source_id='actual-body')
    assert packet['versions'][0]['open_interest'] is None
    with pytest.raises(ValueError):
        m.capture_versions(json.dumps(rows+rows).encode(), contract='088691',
            received_ts=RECEIVED, source_id='actual-body')


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), 10**400])
def test_malformed_receipt_is_normalized_to_validation_error(value):
    with pytest.raises(ValueError):
        module().capture_versions(body(), contract='088691', received_ts=value,
                                  source_id='actual-body')


@pytest.mark.parametrize('corrupt', [False, True])
def test_existing_yandex_transport_retains_verified_public_versions(tmp_path, corrupt):
    from test_ede_ledger_archive import ObjectStore, load_script, restore
    # The standalone script is loaded by path under both pytest entrypoints.
    path = __import__('pathlib').Path(__file__).resolve().parents[1] / 'scripts/archive_cftc_revisions.py'
    assert path.exists(), 'missing bounded Yandex CFTC version archive command'
    archive = load_script('archive_cftc_revisions')
    store = ObjectStore(); store.corrupt = corrupt
    packet = module().capture_versions(body(), contract='088691', received_ts=RECEIVED,
                                      source_id='actual-body')
    if corrupt:
        with pytest.raises((ValueError, OSError, EOFError)):
            archive.archive_packet(store, packet, tmp_path, expected_sha='a'*40)
    else:
        receipt = archive.archive_packet(store, packet, tmp_path, expected_sha='a'*40)
        assert receipt['verified_full_get'] is True
        restored = tmp_path/'restored.json'
        restore(store, receipt, restored)
        assert module().validate_versions(json.loads(restored.read_text())) == packet


def test_collector_retains_exact_revision_packet_without_backdating_receipt():
    calls = []
    def fetch(url):
        calls.append(url)
        if url.startswith(sources.CFTC):
            return body()
        raise ValueError('unrelated fixture source unavailable')
    bundle = sources.build_bundle(instruments=['XAU'], fetch=fetch,
        clock=lambda: RECEIVED, budget=sources.SourceBudget(min_interval=0))
    records = bundle['instruments']['XAU']['edge_family_sources']['positioning']
    packet = bundle.get('cftc_revision_archives', {}).get('XAU')
    assert isinstance(packet, dict), 'CFTC source revision history was discarded'
    assert packet['raw_body_sha256'] == hashlib.sha256(body()).hexdigest()
    assert packet['raw_body_utf8'].encode() == body()
    assert packet['received_ts'] == records[0]['received_ts'] == RECEIVED
    assert packet['versions'][0]['net_position'] == 210335
    assert packet['versions'][0]['open_interest'] == 396109
    assert packet['versions'][0]['source_updated_at'] < RECEIVED
    assert packet['publication_verified'] is False
    assert packet['historical_forecast_admission'] is False
    assert records[0]['published_at'] == RECEIVED
    assert records[0]['proxy_mapping']['validated'] is False
    assert 'cftc_revision_history' not in records[0]
    assert 'raw_body_utf8' not in json.dumps(records)
    urls = [u for u in calls if u.startswith(sources.CFTC)]
    assert len(urls) == 1
    assert '%3Aupdated_at' in urls[0]


def test_missing_optional_metadata_keeps_live_net_and_collection_success():
    rows = json.loads(body()); rows[0].pop(':updated_at')
    raw = json.dumps(rows).encode()
    def fetch(url):
        if url.startswith(sources.CFTC): return raw
        raise ValueError('unrelated fixture source unavailable')
    bundle = sources.build_bundle(instruments=['XAU'], fetch=fetch,
        clock=lambda: RECEIVED, budget=sources.SourceBudget(min_interval=0))
    record = bundle['instruments']['XAU']['edge_family_sources']['positioning'][0]
    assert record['net_position'] == 210335
    assert bundle['raw_sources']['cftc:XAU']['parse_status'] == 'PARSED'
    archive = bundle.get('cftc_revision_archives', {}).get('XAU') or {}
    assert archive.get('status') == 'UNAVAILABLE'
    assert archive['reason'] == 'CFTC_REVISION_TIMESTAMP_INVALID'
    assert not any(x.get('phase') == 'revision_history' for x in bundle['errors'])
