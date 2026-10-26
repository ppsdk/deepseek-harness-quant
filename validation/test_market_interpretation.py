"""Historical bins must not infer future signals or manufacture dividend history."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from data import market_dashboard as market, market_interpretation as context
from validation.test_market_dashboard import frame


class InterpretationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        p = patch.object(market, 'ROOT', Path(self.temp.name)); p.start(); self.addCleanup(p.stop)
        self.cfg = market.settings()

    def test_historical_ranks_and_pressure_do_not_change_before_future_prices(self):
        s = pd.Series([3., 1., 1., 2., 9.])
        ranks = context.rank_history(s, 3, 3)
        self.assertEqual(ranks.iloc[2], 100 / 3)
        self.assertAlmostEqual(ranks.iloc[3], 100 * 2.5 / 3)
        f = frame(1000); frames = {'sector:' + k: f.copy() for k in self.cfg['sectors']}
        original = context.pressure_history(frames, self.cfg)
        for v in frames.values(): v.loc[900:, 'close'] *= 5
        changed = context.pressure_history(frames, self.cfg)
        pd.testing.assert_series_equal(original.iloc[:900], changed.iloc[:900])
        # Ten identical sectors have precisely the one-sector inverse temperature.
        expected = market.price_metrics(f.iloc[:900], self.cfg['temperature'])['temperature']
        self.assertAlmostEqual(original.iloc[899], 100 - expected)

    def test_forward_returns_use_next_open_and_require_complete_future_window(self):
        f = frame(12); f['open'] = [100, 100, 110, 90, 105, 100, 100, 100, 100, 100, 100, 100]
        f['low'] = [99, 99, 109, 89, 104, 99, 99, 99, 99, 99, 99, 99]
        scores = pd.Series([10., 20., 30.], index=[f.date.iloc[0], f.date.iloc[1], f.date.iloc[-1]])
        outcomes = context.future_outcomes(scores, f, 2)
        self.assertEqual(len(outcomes), 2)
        r = outcomes.iloc[0]
        self.assertEqual(r.entry, f.date.iloc[1]); self.assertEqual(r.exit, f.date.iloc[3])
        self.assertAlmostEqual(r['return'], -.1); self.assertAlmostEqual(r.adverse, -.1)
        f.loc[1, 'low'] = np.nan
        self.assertEqual(len(context.future_outcomes(scores, f, 2)), 1)
        self.assertEqual(len(context.future_outcomes(scores, None, 2)), 0)

    def test_spaced_observations_and_large_move_counts(self):
        data = pd.DataFrame(dict(date=['2020-01-01', '2020-01-02', '2020-01-03'],
                                 entry_index=[1, 2, 3], exit_index=[3, 4, 5],
                                 **{'return': [.1, .9, -.1]}, adverse=[-.02, -.9, -.15]))
        s = context.statistics(data, .05)
        self.assertEqual(s['n'], 2); self.assertEqual(s['days'], 3)
        self.assertEqual(s['big_up'], .5); self.assertEqual(s['big_down'], .5)
        self.assertAlmostEqual(s['mean'], 0); self.assertEqual(s['path_down'], .5)

    def test_dividend_archive_preserves_first_observation_and_revisions(self):
        old = (date.today() - timedelta(days=20)).isoformat()
        context.archive_dividends([dict(date=old, value=5)])
        a = context.archive_dividends([dict(date=old, value=5), dict(date=old, value=6)])
        self.assertEqual(len(a), 2)
        self.assertTrue(all(x['available_date'] == date.today().isoformat() for x in a))
        self.assertNotEqual(a[0]['available_date'], old)

    def test_historical_dividends_require_real_availability_and_no_future_revision(self):
        f = frame(600); dates = f.date.tolist()
        financial = dict(bond=[dict(date=d, value=2) for d in dates])
        archive = [dict(date=dates[400], available_date=dates[420], value=5)]
        history = context.dividend_history(f, financial, archive, self.cfg)
        # The old observation is already older than the alignment tolerance when first obtained.
        self.assertEqual(history.notna().sum(), 0)
        archive = [dict(date=dates[420], available_date=dates[421], value=5)]
        first = context.dividend_history(f, financial, archive, self.cfg)
        self.assertTrue(first.loc[:dates[420]].isna().all()); self.assertTrue(pd.notna(first.loc[dates[421]]))
        revised = archive + [dict(date=dates[420], available_date=dates[425], value=9)]
        second = context.dividend_history(f, financial, revised, self.cfg)
        pd.testing.assert_series_equal(first.loc[:dates[424]], second.loc[:dates[424]])

    def test_import_dates_confirmation_limits_and_conflicts(self):
        day = (date.today() - timedelta(days=10)).isoformat()
        payload = dict(csv=f'date,dividend_yield,available_date\n{day},5,{day}', confirmed_dates=True)
        with patch.object(market, 'refresh', return_value=dict(ok=True)) as refresh:
            self.assertEqual(context.import_dividends(payload)['count'], 1)
            refresh.assert_called_once_with('history_import')
        saved = market._read('dividend_history.json', [])
        self.assertEqual(saved[0]['available_date'], day)
        for bad in [dict(payload, confirmed_dates=False), dict(payload, csv=payload['csv'].replace(',5,', ',9,')),
                    dict(payload, csv=f'date,dividend_yield,available_date\n{day},5,2000-01-01'),
                    dict(payload, csv=f'date,dividend_yield,available_date\n{day},NaN,{day}')]:
            with self.subTest(bad=bad), self.assertRaises(ValueError): context.import_dividends(bad)
        self.assertEqual(market._read('dividend_history.json', []), saved)

    def test_fixed_bins_baselines_holdout_and_missing_red_history_are_honest(self):
        cfg = deepcopy(self.cfg); f = frame(1000)
        histories = {k: f.copy() for k in cfg['indices']}
        histories.update({'sector:' + k: f.copy() for k in cfg['sectors']})
        fear = []
        for k, item in cfg['fear_indices'].items():
            g = f.copy(); g['close'] = 10 + np.arange(len(g)) % 40
            histories['fear:' + k] = g; fear.append(dict(id=k, stale=False, value=28))
        current = dict(pressure=dict(score=67), dividend=dict(score=56), fear=fear)
        result = context.build(histories, {}, current, cfg)
        self.assertEqual(len(result['items']), 4)
        nasdaq = next(x for x in result['items'] if x['id'] == 'nasdaq')
        self.assertEqual(nasdaq['target_name'], '纳斯达克100')
        self.assertEqual([(b['low'], b['high']) for b in nasdaq['outcomes']['20']['bins']], list(zip(cfg['interpretation']['metrics']['nasdaq']['bins'][:-1], cfg['interpretation']['metrics']['nasdaq']['bins'][1:])))
        self.assertEqual(sum(b['current'] for b in nasdaq['outcomes']['20']['bins']), 1)
        red = next(x for x in result['items'] if x['id'] == 'dividend')
        self.assertEqual(red['history_days'], 0)
        self.assertTrue(all(b['verdict'] == '次数不足，暂不判断' and b['full']['n'] == 0 for b in red['outcomes']['20']['bins']))
        pressure = next(x for x in result['items'] if x['id'] == 'pressure')
        baseline = pressure['outcomes']['20']['baseline']
        self.assertGreater(baseline['days'], baseline['n'])
        self.assertGreater(pressure['validation_from'], pressure['history_from'])


if __name__ == '__main__':
    unittest.main()
