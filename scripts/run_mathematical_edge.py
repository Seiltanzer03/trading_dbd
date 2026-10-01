#!/usr/bin/env python3
"""Bounded off-host training; use cached JSON sources or fetch actual Yahoo bars."""
import argparse
import json
import time
from pathlib import Path
from seiltanzer.config import INSTRUMENTS
from seiltanzer.mathematical_edge import CONTRACT, train_instrument
from scripts.run_universal_structured_edge_audit import _fresh_off_host_sources


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sources'); p.add_argument('--output', required=True)
    args = p.parse_args()
    if args.sources:
        data = json.loads(Path(args.sources).read_text()); sources = data['sources']; errors = data.get('errors', {})
    else:
        sources, _, errors = _fresh_off_host_sources()
    captured = time.time()
    rows = {s['instrument']: train_instrument(s['instrument'], s['bars'], captured) for s in sources}
    for code in INSTRUMENTS:
        rows.setdefault(code, {'instrument': code, 'status': 'UNRESOLVED', 'reason': errors.get(code, 'SOURCE_BARS_UNAVAILABLE'), 'weight_fraction': 0.})
    report = {'contract_version': CONTRACT, 'created_ts': captured, 'instruments': rows,
              'production_authority': False, 'automatic_execution': False,
              'user_policy': 'PERSONAL_WORKING_EVIDENCE_BOUNDED_SOFT_RANKING', 'source_errors': errors}
    from seiltanzer.mathematical_edge_archive import replay_frozen_fx_rule
    report['archived_fx_reproduction'] = replay_frozen_fx_rule(Path(__file__).resolve().parents[1] / 'research_fixtures/mathematical_edge')
    report['sources'] = [{k: v for k, v in s.items() if k != 'bars'} | {'bar_count': len(s['bars'])} for s in sources]
    path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False))
    for code, row in rows.items():
        print(code, row['status'], row.get('horizon_minutes'),
              {k: round(v['gain_mbit'], 3) for k, v in row.get('diagnostics', {}).items()})

if __name__ == '__main__':
    main()
