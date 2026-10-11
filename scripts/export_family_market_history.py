"""Read existing Yandex private archive; never upload raw reviews publicly."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path

from seiltanzer.edge_family_archive import _loads
from seiltanzer.edge_family_private_archive import restore
from seiltanzer.edge_family_market import features_from_archive


def export_history(client, output):
    history = {}
    audit = {'read_only': True, 'raw_archive_publication': False,
             'feature_observation_count': 0, 'reason': 'PRIVATE_ARCHIVE_RESTORE_FAILED'}
    try:
        raw, _ = restore(client)
        if raw is None:
            audit['reason'] = 'PRIVATE_ARCHIVE_INITIAL_EMPTY'
        else:
            history = features_from_archive(_loads(raw))
            audit['reason'] = 'PRIVATE_ARCHIVE_OBSERVATIONS_RESTORED'
    except Exception:
        # Archive/provider errors may contain private identifiers. Do not print
        # them, or represent inaccessible storage as a successful empty restore.
        history = {}
    audit['family_counts'] = {family: sum(len(rows.get(family, [])) for rows in history.values())
                              for family in ('macro','event','order_flow','intermarket','positioning','value_carry','option','session')}
    audit['feature_observation_count'] = sum(audit['family_counts'].values())
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(history, allow_nan=False, separators=(',', ':')).encode()
    if len(raw) > 96_000_000:
        raise ValueError('PRIVATE_FEATURE_HISTORY_EXCEEDS_BOUND')
    path.write_bytes(raw)
    path.chmod(0o600)
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Print counters only. Dependencies never print archive/account exceptions.
    with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        import boto3
        from botocore.config import Config
        client = boto3.client('s3', endpoint_url='https://storage.yandexcloud.net', region_name='ru-central1',
                             config=Config(connect_timeout=10, read_timeout=60, retries={'max_attempts': 1}))
        audit = export_history(client, args.output)
    print('FAMILY_HISTORY_READ', json.dumps(audit, sort_keys=True))


if __name__ == '__main__':
    main()
