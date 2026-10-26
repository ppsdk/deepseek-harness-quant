"""Accounting and timing checks independent of downloaded market performance."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtest import research_runner as runner


class ResearchBacktestTest(unittest.TestCase):
    def setUp(self):
        self.days = ['2024-01-01', '2024-01-02', '2024-01-03', '2024-01-04']
        self.codes = ['000001.SZ', '000002.SZ']
        self.panels = {key: pd.DataFrame(value, index=self.days, columns=self.codes)
                       for key, value in {'open': 10., 'high': 11., 'low': 9., 'close': 10.,
                                          'volume': 1000., 'turn': 1., 'is_st': np.nan}.items()}
        self.score = pd.DataFrame([[1., 0.], [0., 1.], [0., 1.], [0., 1.]],
                                  index=self.days, columns=self.codes)
        self.params = runner.validate_params({'strategy': 'reversal20', 'start': self.days[1],
                    'end': self.days[-1], 'topn': 1, 'stocks': 2, 'initial_capital': 1000,
                    'rebalance_days': 1, 'commission_bps': 0, 'min_commission': 0,
                    'stamp_tax_bps': 0, 'slippage_bps': 0}, runner.research_config())

    def run_sim(self, **overrides):
        return runner.simulate(self.panels, self.score, {**self.params, **overrides})

    def test_prior_close_signal_and_next_open(self):
        self.panels['open'].loc[self.days[1], self.codes[0]] = 12.
        r = self.run_sim()
        first = r['trades'][0]
        self.assertEqual(first['code'], self.codes[0])
        self.assertEqual(first['signal_date'], self.days[0])
        self.assertEqual(first['date'], self.days[1])
        self.assertEqual(first['fill_price'], 12.)
        self.assertTrue(all(t['signal_date'] < t['date'] for t in r['trades']))

    def test_actual_turnover_only_and_no_final_forced_sale(self):
        self.score.loc[:] = [1., 0.]
        r = self.run_sim(commission_bps=10, min_commission=5, stamp_tax_bps=10)
        self.assertEqual(len(r['trades']), 1)
        self.assertEqual(r['trades'][0]['side'], 'buy')
        self.assertEqual(r['trades'][0]['commission'], 5)
        self.assertEqual(r['trades'][0]['stamp_tax'], 0)
        self.assertAlmostEqual(r['nav_series'].iloc[-1], .995)

    def test_sided_slippage_tax_and_cash_conservation(self):
        r = self.run_sim(slippage_bps=100, commission_bps=10, stamp_tax_bps=50, min_commission=5)
        for t in r['trades']:
            self.assertAlmostEqual(t['fill_price'], 10.1 if t['side'] == 'buy' else 9.9)
            self.assertAlmostEqual(t['commission'], max(5, t['amount'] * .001))
            self.assertAlmostEqual(t['stamp_tax'], t['amount'] * .005 if t['side'] == 'sell' else 0)
            self.assertGreaterEqual(t['cash_after'], -1e-8)
        expected = r['cash'] + sum(q * 10 for q in r['positions'].values())
        self.assertAlmostEqual(r['nav_series'].iloc[-1] * 1000, expected)

    def test_higher_slippage_reduces_constant_price_nav(self):
        zero = self.run_sim()
        charged = self.run_sim(slippage_bps=100)
        self.assertAlmostEqual(zero['nav_series'].iloc[-1], 1)
        self.assertLess(charged['nav_series'].iloc[-1], zero['nav_series'].iloc[-1])

    def test_daily_cash_and_stock_values_conserve_equity(self):
        r = self.run_sim(slippage_bps=50, commission_bps=10, min_commission=5)
        self.assertEqual(len(r['daily_account']), len(r['nav_series']))
        for account, nav in zip(r['daily_account'], r['nav_series']):
            self.assertAlmostEqual(account['cash'] + account['market_value'], account['equity'])
            self.assertAlmostEqual(account['equity'], nav * self.params['initial_capital'])
            self.assertAlmostEqual(account['exposure'], account['market_value'] / account['equity'])

    def test_one_price_and_missing_bars_never_fill(self):
        self.panels['high'].loc[self.days[1], self.codes[0]] = 10.
        self.panels['low'].loc[self.days[1], self.codes[0]] = 10.
        r = self.run_sim()
        self.assertFalse(any(t['date'] == self.days[1] for t in r['trades']))
        self.assertEqual(r['rejections'][0]['reason'], 'one_price_bar')
        self.panels['high'].loc[self.days[1], self.codes[0]] = 11.
        for field in self.panels:
            self.panels[field].loc[self.days[2], self.codes[0]] = np.nan
        r = self.run_sim()
        self.assertTrue(any(t['date'] == self.days[1] for t in r['trades']))
        self.assertFalse(any(t['date'] == self.days[2] and t['code'] == self.codes[0] for t in r['trades']))
        self.assertEqual(r['stale_valuation_days'], 1)

    def test_missing_turnover_is_not_a_neutral_signal(self):
        self.panels['turn'].loc[:] = np.nan
        score = runner.factor_score(self.panels, [{'name': 'turn_20d', 'sign': -1}])
        self.assertTrue(score.isna().all().all())
        r = runner.simulate(self.panels, score, self.params)
        self.assertEqual(r['trades'], [])

    def test_invalid_params_rejected(self):
        for values in ({'slippage_bps': -1}, {'slippage_bps': 'nan'}, {'stocks': 2.5},
                       {'strategy': 'does_not_exist'}, {'strategy': []}, {'start': '2025-01-01', 'end': '2024-01-01'},
                       {'strategy': 'turn_low', 'start': '2018-01-01'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                runner.validate_params(values, runner.research_config())

    def test_strategy_defaults_and_explicit_overrides(self):
        defaults = runner.validate_params({'strategy': 'turn_low'}, runner.research_config())
        self.assertEqual(defaults['rebalance_days'], 40)
        self.assertEqual(defaults['topn'], 20)
        custom = runner.validate_params({'strategy': 'turn_low', 'rebalance_days': 5}, runner.research_config())
        self.assertEqual(custom['rebalance_days'], 5)

    def test_archive_saves_costs_params_and_does_not_label_positive_as_validated(self):
        self.score.loc[:] = [1., 0.]
        data = {'selected_codes': self.codes, 'available_stocks': 2}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runner, 'ARCHIVES', Path(directory)), \
                patch.object(runner, 'load_panels', return_value=(self.panels, data)), \
                patch.object(runner, 'factor_score', return_value=self.score):
            result = runner.run_research({**self.params, 'min_commission': 5})
            saved = json.loads(next(Path(directory).glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(saved['params']['min_commission'], 5)
        self.assertEqual(saved['costs']['commission'], 5)
        self.assertEqual(saved['trades'], result['trades'])
        self.assertEqual(saved['engine'], 'research_ledger_v1')
        self.assertEqual(saved['strategy_spec']['factor_list'][0]['name'], 'mom_20')
        self.assertAlmostEqual(saved['metrics']['max_drawdown'], -.005)


if __name__ == '__main__':
    unittest.main()
