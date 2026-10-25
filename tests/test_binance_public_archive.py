"""Protocol fixtures exercise parsing, not evidence of a trading advantage."""
import hashlib
import importlib
import importlib.util
import io
import zipfile
from datetime import datetime, timezone

import pytest

from seiltanzer.mathematical_edge_sources import RequestBudget


def archive_module():
    assert importlib.util.find_spec('seiltanzer.binance_public_archive') is not None, 'exact Binance public archive collector missing'
    return importlib.import_module('seiltanzer.binance_public_archive')


def ts(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


START = ts('2026-09-01')
NAME = 'BTCUSDT-5m-2026-09.zip'


def line(start=START, unit=1_000_000, **changes):
    values = dict(open='100', high='102', low='99', close='101', volume='10', quote='1000', count='5', buy='6', buyquote='600')
    values.update(changes)
    return ','.join(map(str, [int(start*unit), values['open'], values['high'], values['low'], values['close'], values['volume'],
        int((start+300)*unit)-1, values['quote'], values['count'], values['buy'], values['buyquote'], '0']))+'\n'


def zipped(data=None, member=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as output:
        output.writestr(member or NAME[:-4]+'.csv', line() if data is None else data)
    return stream.getvalue()


def parse(data=None, name=NAME, checksum=None, **kwargs):
    body = zipped() if data is None else data
    proof = checksum if checksum is not None else f'{hashlib.sha256(body).hexdigest()}  {name}\n'.encode()
    return archive_module().parse_archive(body, proof, filename=name, symbol='BTCUSDT', cutoff=START+600, **kwargs)


def test_archive_decodes_documented_microseconds_and_real_trade_aggregates():
    bars, flow = parse()
    assert bars == [{'bar_end_ts':START+300, 'open':100., 'high':102., 'low':99., 'close':101.}]
    assert flow[0]['bar_end_ts'] == START+300
    assert flow[0]['taker_buy_base_volume'] == 6
    assert flow[0]['volume'] == 10


@pytest.mark.parametrize('body,proof,reason', [
    (zipped(), b'0'*64+b'  '+NAME.encode(), 'CHECKSUM'),
    (zipped(member='../bad.csv'), None, 'MEMBER'),
    (zipped(line(unit=1000)), None, 'TIMESTAMP'),
    (zipped(line(buy='11')), None, 'VOLUME'),
    (zipped(line(low='103')), None, 'OHLC'),
    (zipped(line()+line()), None, 'DUPLICATE'),
    (zipped(line(start=START-300)), None, 'PERIOD'),
])
def test_archive_rejects_wrong_identity_clock_and_invalid_observations(body, proof, reason):
    with pytest.raises(ValueError, match=reason):
        parse(body, checksum=proof)


def test_archive_excludes_uncompleted_bars_without_filling_a_gap():
    bars, flow = parse(zipped(line()+line(start=START+600)))
    assert len(bars) == len(flow) == 1


class Response:
    def __init__(self, body=b'', status=200):
        self.content = body
        self.status_code = status
        self.headers = {}


class Client:
    def __init__(self, denied=False):
        self.calls = []
        self.denied = denied

    def get(self, url, *, timeout):
        self.calls.append(url)
        if self.denied:
            return Response(status=451)
        filename = url.rsplit('/', 1)[-1].removesuffix('.CHECKSUM')
        start = ts(filename.removesuffix('.zip').split('-5m-')[1])
        body = zipped(line(start=start), member=filename[:-4]+'.csv')
        return Response((f'{hashlib.sha256(body).hexdigest()}  {filename}\n'.encode()) if url.endswith('.CHECKSUM') else body)


def test_collector_uses_configured_symbol_and_records_actual_later_receipt(monkeypatch):
    module = archive_module()
    cutoff = ts('2026-09-04')
    monkeypatch.setattr(module.time, 'time', lambda: cutoff+17)
    client = Client()
    source = module.fetch_public_archive('BTCUSD', cutoff, client, RequestBudget(30, 0), days=2)
    assert source['provider'] == 'Binance' and source['ticker'] == 'BTCUSDT'
    assert source['source_semantics']['currency_basis_mismatch'] is False
    assert source['fetched_ts'] == cutoff+17
    assert source['collection']['historical_first_receipt_known'] is False
    assert all(url.startswith('https://data.binance.vision/data/spot/daily/klines/BTCUSDT/5m/') for url in client.calls)
    assert len(client.calls) == 4
    assert len(source['bars']) == 2


def test_collector_stops_after_denial_without_retry_or_alternate_host():
    client = Client(denied=True)
    with pytest.raises(RuntimeError, match='451'):
        archive_module().fetch_public_archive('BTCUSD', START+86400, client, RequestBudget(30, 0), days=1)
    assert len(client.calls) == 1


def test_archive_uncompressed_size_is_bounded():
    with pytest.raises(ValueError, match='SIZE'):
        parse(zipped('0'*12_000_001))
