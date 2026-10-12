"""A transport failure must not replace a still-fresh exact broker observation."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

def definitions(path, names, namespace):
    tree = ast.parse(path.read_text())
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)

class BrokerQuoteContinuity(unittest.TestCase):
    def refresh(self, **changes):
        price = dict(value=30695., ts=980., status='live', fresh=True, derived=False,
                     source='TradingView stream OANDA:NAS100USD', instrument_type='broker_cfd')
        price.update(changes)
        clock = [1000.]
        namespace = {'math': math, 'time': SimpleNamespace(time=lambda: clock[0])}
        definitions(ROOT/'seiltanzer/data/bybit.py', {'number', 'fresh_quote'}, namespace)
        def fail(_symbol):
            clock[0] += changes.pop('_transport_delay', 0.)
            raise TimeoutError('temporary broker timeout')
        namespace['_fetch_tradingview_quote'] = fail
        definitions(ROOT/'seiltanzer/data/feeds.py', {'_refresh_primary_price'}, namespace)
        fallback = []
        def ticker(_):
            fallback.append(True)
            raise RuntimeError('fallback reached')
        engine = SimpleNamespace(demo=False, price=price, settings=SimpleNamespace(price_poll_sec=1.),
            instrument=SimpleNamespace(asset_class='index', tradingview_symbol='OANDA:NAS100USD',
                                       swissquote_pair=None, yahoo='^NDX'),
            stream=None, _last_broker_rest_attempt=0., _last_price_rest_attempt=0., PRICE_IDLE_SEC=120.,
            _annotate_freshness=lambda: None,
            _mark_fail=lambda d, *args: d.update(status='delayed'))
        with patch.dict(sys.modules, {'yfinance': SimpleNamespace(Ticker=ticker)}):
            namespace['_refresh_primary_price'](engine)
        return engine.price, fallback

    def test_fresh_exact_observation_survives_one_transport_failure(self):
        price, fallback = self.refresh()
        self.assertEqual(fallback, [])
        self.assertEqual(price['ts'], 980.)
        self.assertEqual(price['value'], 30695.)
        self.assertEqual(price['status'], 'live')
        self.assertIn('timeout', price['error'])

    def test_expired_wrong_symbol_or_non_direct_observations_use_fallback(self):
        for changes in [dict(ts=960.), dict(source='TradingView stream OANDA:SPX500USD'),
                        dict(derived=True), dict(instrument_type='cash_index'),
                        dict(status='delayed'), dict(fresh=False), dict(_transport_delay=15.)]:
            with self.subTest(changes=changes):
                _, fallback = self.refresh(**changes)
                self.assertEqual(fallback, [True])

if __name__ == '__main__':
    unittest.main()
