import json
import subprocess
import sys

from seiltanzer.config import ALL_INSTRUMENTS
from seiltanzer.edge_family_adapters import FAMILIES


def audit(tmp_path, bundle):
    path = tmp_path / 'bundle.json'
    path.write_text(json.dumps(bundle))
    result = subprocess.run([sys.executable, '-m', 'scripts.audit_readiness_matrix',
                             '--input', str(path), '--json'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_missing_capture_is_not_ready_and_includes_all_configured_cells(tmp_path):
    report = audit(tmp_path, {'instruments': {}, 'captured_ts': 100})
    assert len(report['cells']) == len(ALL_INSTRUMENTS) * len(FAMILIES)
    assert all(c['input_available'] is False and c['forecast_available'] is False
               and c['reason'] == 'INSTRUMENT_CAPTURE_MISSING' for c in report['cells'])


def test_input_features_do_not_imply_model_samples_or_forecast(tmp_path):
    code = next(iter(ALL_INSTRUMENTS))
    report = audit(tmp_path, {'captured_ts': 100, 'instruments': {code: {
        'readiness': {'event': {'available': True, 'readiness': 'DATA_AVAILABLE_MODEL_PENDING',
            'features': {'event.test': 0}, 'observed_ts': 90, 'max_age_sec': 20,
            'feature_provenance': {'event.test': {'body_sha256': 'a' * 64}},
            'reason': 'OBSERVED_FEATURES_REQUIRE_VALIDATED_NET_ACTION_MODEL',
            'forecast_available': False, 'needs_data': ['out-of-sample calibration']}}}}})
    cell = next(c for c in report['cells'] if c['instrument'] == code and c['family'] == 'event')
    assert cell['input_available'] is True
    assert cell['freshness'] == 'FRESH_AT_CAPTURE'
    assert cell['forecast_available'] is False
    assert cell['independent_group_count'] is None
    assert cell['model_status'] == 'UNAVAILABLE'
    assert cell['source_hashes'] == ['a' * 64]


def test_missing_family_refuses_false_all_features_present(tmp_path):
    code = next(iter(ALL_INSTRUMENTS))
    report = audit(tmp_path, {'instruments': {code: {'readiness': {}}}})
    cells = [c for c in report['cells'] if c['instrument'] == code]
    assert all(c['reason'] == 'FAMILY_READINESS_MISSING' and not c['input_available'] for c in cells)
