"""One bounded pass of public CFTC row versions into existing private Yandex storage.

No SSH, production/database/position writes, training or inferred publications.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode

from seiltanzer.cftc_revision_history import capture_versions, validate_versions, MAX_BODY_BYTES
from seiltanzer.edge_family_sources import CFTC, COT_MARKETS, SourceBudget, fetch_public

if __package__ in {'scripts'}:
    from scripts import ede_ledger_archive as archive
else:
    import ede_ledger_archive as archive


def archive_packet(client, packet, directory, *, expected_sha):
    packet = validate_versions(packet)
    source = Path(directory) / ('cftc-public-row-versions-' + packet['market_code'] + '.json')
    with source.open('x', encoding='utf-8') as handle:
        json.dump(packet, handle, sort_keys=True, separators=(',', ':'), allow_nan=False)
        handle.write('\n')
    packed = source.with_suffix('.json.gz')
    manifest = archive.prepare(source, packed, expected_sha=expected_sha)
    return archive.upload(client, packed, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from seiltanzer.macro_offhost_bundle import current_repository_sha
    if current_repository_sha() != args.expected_sha:
        raise SystemExit('CFTC_ARCHIVE_CODE_SHA_MISMATCH')
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', endpoint_url='https://storage.yandexcloud.net', region_name='ru-central1',
        config=Config(connect_timeout=10, read_timeout=60, retries={'max_attempts': 1}))
    budget = SourceBudget(seconds=90)
    report = {'archived_by_sha': args.expected_sha, 'receipts': [], 'errors': [],
        'production_writes': False, 'models_produced': 0, 'publication_verified': False}
    select = ':id,:created_at,:updated_at,report_date_as_yyyy_mm_dd,cftc_contract_market_code,' \
        'market_and_exchange_names,noncomm_positions_long_all,noncomm_positions_short_all,open_interest_all'
    with tempfile.TemporaryDirectory(prefix='cftc-version-audit-') as directory:
        for code, contract in COT_MARKETS.items():
            url = CFTC + '?' + urlencode({'$where': f"cftc_contract_market_code='{contract}'",
                '$order': 'report_date_as_yyyy_mm_dd DESC', '$limit': 128, '$select': select})
            try:
                budget.reserve('publicreporting.cftc.gov')
                raw = fetch_public(url, timeout=min(12., budget.remaining()),
                    max_bytes=MAX_BODY_BYTES, budget=budget)
                packet = capture_versions(raw, contract=contract, received_ts=time.time(),
                    source_id='cftc:' + code + ':' + hashlib.sha256(raw).hexdigest())
            except Exception as exc:
                if isinstance(exc, HTTPError) and exc.code in {403, 451, 429, 418}:
                    budget.stop_host('publicreporting.cftc.gov')
                report['errors'].append({'instrument': code, 'reason':
                    str(exc)[:160] if isinstance(exc, ValueError) else type(exc).__name__})
                continue
            # Cloud failure is fatal; no success receipt is emitted before full GET.
            receipt = archive_packet(client, packet, directory, expected_sha=args.expected_sha)
            report['receipts'].append({'instrument': code, 'row_n': len(packet['versions']),
                'source_body_sha256': packet['raw_body_sha256'], 'received_ts': packet['received_ts'],
                'first_report_ts': packet['versions'][0]['report_ts'],
                'latest_report_ts': packet['versions'][-1]['report_ts'], 'archive': receipt})
    report['status'] = 'OK' if len(report['receipts']) == len(COT_MARKETS) else 'PARTIAL'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    print('CFTC_VERSION_ARCHIVE=' + json.dumps(report, sort_keys=True))
    if report['status'] != 'OK':
        raise SystemExit('CFTC_VERSION_ARCHIVE_PARTIAL_SOURCE')


if __name__ == '__main__':
    main()
