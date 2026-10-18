"""Storage tools and live mutation checks share the preexisting canonical hash."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.mark.parametrize('row,expected', [
    ({}, '2a36fd463cdd8c76ce1e857f56b4a66ccc0fa3b1893da323152bd07ea912195c'),
    ({'observation_id':'наблюдение', 'captured_ts':'1700000000', 'target_ts':1700000900,
      'features_json':'{"x":1,"u":"я"}', 'forecast_json':'{"horizon_kind":"fixed_trading_time"}',
      'resolution_status':'insufficient_future_data', 'outcome_json':'null', 'evidence_eligible':0}, '8373873a1be9218b8d61a14f267089bce0696edf1472a59176c2cd0cc4e3c40d'),
    ({'market_price':'nan', 'resolved_ts':'invalid', 'features_json':'bad',
      'forecast_json':{'a':[1,2]}, 'outcome_json':{'x':True}, 'retrospective_replay':1}, 'f55be709bdec2b976caa0adaa550b02e922aa22b90003185a88125f720521812'),
])
def test_source_hash_matches_pinned_preextraction_contract(row, expected):
    path = Path(__file__).resolve().parents[1]/'seiltanzer/g1_source_record.py'
    spec = importlib.util.spec_from_file_location('_standalone_g1_source', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from seiltanzer.g1_dataset_runtime import _source_record_sha256
    assert module.source_record_sha256(row) == expected
    assert _source_record_sha256(row) == expected


def test_source_hash_owner_loads_without_application_dependencies():
    path = Path(__file__).resolve().parents[1]/'seiltanzer/g1_source_record.py'
    result = subprocess.run([sys.executable, '-S', '-c',
        'import runpy,sys; m=runpy.run_path(sys.argv[1]); '
        'assert len(m["source_record_sha256"]({}))==64; '
        'assert not any(n.startswith("seiltanzer") for n in sys.modules)', str(path)],
        capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()
