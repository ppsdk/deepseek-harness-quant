"""Independent score arithmetic, point-in-time alignment and refresh recovery."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch, Mock

import numpy as np
import pandas as pd

from data import market_dashboard as market


def frame(days=1100):
    dates = pd.bdate_range(end=date.today(), periods=days).strftime('%Y-%m-%d')
    close = 100 * np.exp(np.arange(days) * .0003 + np.sin(np.arange(days) / 11) * .06)
    return pd.DataFrame(dict(date=dates, open=close, high=close + 1, low=close - 1, close=close, volume=1000))


class MarketDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        p = patch.object(market, 'ROOT', Path(self.temp.name)); p.start(); self.addCleanup(p.stop)
        self.cfg = market.settings()
        with market._lock:
            market._state.clear(); market._state.update(state='idle', completed=0, total=0, message='尚未更新')

    def test_percentile_ties_bounds_and_history_window(self):
        self.assertEqual(market.percentile(pd.Series([2., 2., 2.]), 3, 3), 50)
        self.assertAlmostEqual(market.percentile(pd.Series([100., 1., 2., 3.]), 3, 3), 100 * 2.5 / 3)
        self.assertIsNone(market.percentile(pd.Series([1., np.nan]), 3, 2))
        self.assertEqual(market.linear_score(-3, [-1, 5]), 0)
        self.assertEqual(market.linear_score(6, [-1, 5]), 100)

    def test_native_decoder_failure_stays_a_feed_error(self):
        with patch.object(market.subprocess, 'run', return_value=Mock(returncode=1)):
            with self.assertRaisesRegex(ValueError, '保留已有缓存'):
                market.decode_sina('synthetic', 2)
        with patch.object(market.subprocess, 'run', return_value=Mock(returncode=0, stdout='[{"close":12}]')) as run:
            self.assertEqual(market.decode_sina('synthetic', 2), [{'close': 12}])
            self.assertFalse(run.call_args.kwargs.get('shell', False))

    def test_temperature_independent_metrics_and_no_future_rows_in_prefix(self):
        f = frame(); spec = self.cfg['temperature']; r = market.price_metrics(f, spec)
        c = f.close
        self.assertAlmostEqual(r['volatility'], c.pct_change().tail(20).std(ddof=1) * np.sqrt(252))
        self.assertAlmostEqual(r['drawdown'], c.iloc[-1] / c.tail(252).max() - 1)
        self.assertAlmostEqual(r['return_20'], c.iloc[-1] / c.iloc[-21] - 1)
        self.assertGreaterEqual(r['temperature'], 0); self.assertLessEqual(r['temperature'], 100)
        altered = f.copy(); altered.loc[900:, 'close'] *= 5
        self.assertEqual(market.price_metrics(f.iloc[:900], spec), market.price_metrics(altered.iloc[:900], spec))
        self.assertIsNone(market.price_metrics(f.iloc[:80], spec)['temperature'])

    def test_dividend_components_and_weighted_total_are_recomputed(self):
        f = frame(); day = f.date.iloc[-1]
        financial = dict(dividend=[dict(date=day, value=5)], bond=[dict(date=day, value=2)])
        r = market.dividend_score(f, financial, self.cfg); p = r['components']
        expected_gap = (3 - (-1)) / 6 * 100
        dd = 1 - f.close.iloc[-1] / f.close.tail(252).max()
        self.assertAlmostEqual(p['yield_gap']['score'], expected_gap)
        self.assertAlmostEqual(p['drawdown']['score'], dd / .3 * 100)
        self.assertAlmostEqual(r['score'], .5 * expected_gap + .25 * dd / .3 * 100 + .25 * p['volatility']['score'])
        self.assertEqual(set(r['input_dates'].values()), {day})

    def test_missing_stale_and_misaligned_finance_never_make_a_total(self):
        f = frame(); day = f.date.iloc[-1]
        old = (date.fromisoformat(day) - timedelta(days=30)).isoformat()
        self.assertIsNone(market.dividend_score(f, {}, self.cfg)['score'])
        stale = dict(dividend=[dict(date=old, value=5)], bond=[dict(date=old, value=2)])
        self.assertIsNone(market.dividend_score(f, stale, self.cfg)['score'])
        # A newer bond must not be paired with an older yield across a large gap.
        misaligned = dict(dividend=[dict(date=day, value=5)], bond=[dict(date=old, value=2)])
        self.assertIsNone(market.dividend_score(f, misaligned, self.cfg)['score'])
        future = (date.today() + timedelta(days=10)).isoformat()
        base = dict(dividend=[dict(date=day, value=5)], bond=[dict(date=day, value=2)])
        expected = market.dividend_score(f, base, self.cfg)
        base['dividend'].append(dict(date=future, value=99)); base['bond'].append(dict(date=future, value=99))
        self.assertEqual(market.dividend_score(f, base, self.cfg)['score'], expected['score'])

    def test_comparison_uses_common_dates_and_exact_common_base(self):
        f = frame(30); frames = dict(csi300=f, nasdaq=f.iloc[1::2].copy(), sp500=f.iloc[2:].copy())
        frames['nasdaq']['close'] *= 500
        r = market.comparison(frames, self.cfg, 365)
        self.assertEqual(r['start'], f.date.iloc[3])
        self.assertEqual(len(r['series']), 3)
        for s in r['series']:
            self.assertEqual(s['values'][0]['value'], 100)
            self.assertEqual([p['date'] for p in s['values']], [p['date'] for p in r['series'][0]['values']])
        self.assertAlmostEqual(r['series'][1]['values'][-1]['value'], r['series'][0]['values'][-1]['value'])
        self.assertEqual(market.comparison(dict(frames, nasdaq=None), self.cfg, 365)['series'], [])

    def test_quote_parsers_preserve_missing_volume_and_filter_bad_and_future_rows(self):
        today = date.today().strftime('%Y%m%d'); future = (date.today() + timedelta(days=4)).strftime('%Y%m%d')
        raw = pd.DataFrame(dict(date=[today, future, today], open=[1, 2, 0], high=[2, 3, 1], low=[.5, 1, 0], close=[1, 2, 0]))
        r = market.normalize_quotes(raw)
        self.assertEqual(len(r), 1); self.assertEqual(r.date.iloc[0], date.today().isoformat())
        self.assertTrue(pd.isna(r.volume.iloc[0]))
        response = Mock(); response.json.return_value = dict(data=[dict(tradeDate=today, open=1, high=2, low=.5, close=1, tradingVol=123)])
        with patch.object(market, '_request', return_value=response):
            f = market.fetch_quotes(self.cfg['indices']['csi300'], self.cfg)
        self.assertEqual(f.volume.iloc[0], 123)

    def test_refresh_reuses_running_job_and_preserves_data_on_failed_source(self):
        cfg = deepcopy(self.cfg); cfg['indices'] = {'csi300': cfg['indices']['csi300']}; cfg['sectors'] = {}; cfg['fear_indices'] = {}
        financial = dict(dividend=[dict(date=date.today().isoformat(), value=5)], bond=[dict(date=date.today().isoformat(), value=2)])
        market._write('financial.json', financial)
        f = frame(); local = market.cache(); local.put_daily(cfg['indices']['csi300']['code'], f, adjust='none')
        market._state.update(state='running')
        self.assertTrue(market.refresh()['existing'])
        market._state.update(state='running', completed=0)
        with patch.object(market, 'fetch_quotes', side_effect=ValueError('source down')), \
                patch.object(market, 'fetch_financial', side_effect=ValueError('source down')), \
                patch.object(market, 'build_snapshot', return_value=dict(ok=True)):
            market._worker(cfg, [('index:csi300', cfg['indices']['csi300'])])
        self.assertEqual(market._state['state'], 'partial'); self.assertEqual(market._state['failed'], 3)
        self.assertEqual(market._read('financial.json', {}), financial)
        self.assertEqual(len(local.get_daily(cfg['indices']['csi300']['code'], adjust='none')), len(f))

    def test_startup_configuration_and_dashboard_get_are_network_free(self):
        with patch.object(market, 'refresh', return_value={'ok': True}) as refresh:
            market.startup(); refresh.assert_called_once_with('startup')
        with patch.object(market, '_request', side_effect=AssertionError('GET must not fetch')):
            d = market.dashboard()
        self.assertEqual(d['comparison']['series'], [])
        with self.assertRaises(ValueError): market.dashboard({'days': 'wrong'})
        from deck.research_api import get, post
        with patch.object(market, 'dashboard', return_value={'ok': True}), patch.object(market, 'refresh', return_value={'ok': True}):
            self.assertTrue(get('/api/studio/dashboard', {})['ok'])
            self.assertTrue(post('/api/studio/dashboard/refresh', {}, threading.Lock())['ok'])


if __name__ == '__main__':
    unittest.main()
