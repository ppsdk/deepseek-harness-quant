"""Hand-calculated values, missing observations and causal prefix consistency."""
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from factors.research_indicators import calculate
from backtest.research_portfolio import simulate
from scripts.mainboard_strategy_research import evaluate_return_target


class ResearchFactorsTest(unittest.TestCase):
    def test_index_excess_and_relative_compound_return_are_different(self):
        policy=dict(benchmark=dict(code='synthetic'),targets=dict(annual_return=.2,excess_annual_return=.2,
            accepted_return_measures=['absolute','excess_pp'],max_drawdown=-.1,max_holdings=5,max_replacements=5))
        item=dict(metrics=dict(annual_return=.1,max_drawdown=-.08),max_holdings=5,frequency_pass=True,max_bought_codes=5,max_sold_codes=5)
        with patch('scripts.mainboard_strategy_research.benchmark_snapshot',return_value=dict(metrics=dict(annual_return=-.15))):
            result=evaluate_return_target(item,['2025-01-02','2025-12-31'],policy)
        self.assertAlmostEqual(result['metrics']['annual_excess_pp'],25.)
        self.assertAlmostEqual(result['metrics']['relative_annual_return'],1.1/.85-1)
        self.assertTrue(result['target_pass'])
        item['metrics']['max_drawdown']=-.12
        with patch('scripts.mainboard_strategy_research.benchmark_snapshot',return_value=dict(metrics=dict(annual_return=-.15))):
            self.assertFalse(evaluate_return_target(item,['2025-01-02','2025-12-31'],policy)['target_pass'])

    def test_exact_twenty_percentage_points_is_not_rejected_by_roundoff(self):
        policy=dict(benchmark=dict(code='synthetic'),targets=dict(annual_return=.4,excess_annual_return=.2,
            accepted_return_measures=['excess_pp'],max_drawdown=-.1,max_holdings=5,max_replacements=5))
        item=dict(metrics=dict(annual_return=.3,max_drawdown=-.1),max_holdings=5,frequency_pass=True,max_bought_codes=5,max_sold_codes=5)
        with patch('scripts.mainboard_strategy_research.benchmark_snapshot',return_value=dict(metrics=dict(annual_return=.1))):
            self.assertTrue(evaluate_return_target(item,['2025-01-02','2025-12-31'],policy)['target_pass'])

    def test_trend_efficiency_is_signed_and_flat_path_is_missing(self):
        close = pd.DataFrame({'rise': [10., 11., 10., 12.], 'fall': [12., 11., 10., 9.], 'flat': [5.]*4})
        actual = calculate({'operation': 'research_trend_efficiency', 'params': {'n': 3}}, {'close': close})
        self.assertAlmostEqual(actual.rise.iloc[-1], 2/4)
        self.assertAlmostEqual(actual.fall.iloc[-1], -1.)
        self.assertTrue(np.isnan(actual.flat.iloc[-1]))

    def test_gap_risk_uses_previous_close_and_does_not_fill_missing(self):
        close = pd.DataFrame({'stock': [10., 10., 10., 10.]})
        opening = pd.DataFrame({'stock': [10., 11., 9., 12.]})
        spec = {'operation': 'research_gap_risk', 'params': {'n': 2}}
        actual = calculate(spec, {'open': opening, 'close': close})
        self.assertAlmostEqual(actual.stock.iloc[2], .1)
        self.assertAlmostEqual(actual.stock.iloc[3], .15)
        opening.iloc[2, 0] = np.nan
        self.assertTrue(np.isnan(calculate(spec, {'open': opening, 'close': close}).stock.iloc[-1]))

    def test_future_append_does_not_change_previous_values(self):
        close = pd.DataFrame({'stock': [10., 12., 11., 13., 90., 1.]})
        for op in ['research_trend_efficiency', 'research_gap_risk']:
            spec = {'operation': op, 'params': {'n': 2}}
            full = calculate(spec, {'close': close, 'open': close + 1})
            prefix = calculate(spec, {'close': close.iloc[:4], 'open': close.iloc[:4] + 1})
            pd.testing.assert_frame_equal(full.iloc[:4], prefix)

    def test_blocked_old_holding_occupies_a_slot_before_new_buys(self):
        dates = pd.bdate_range('2024-01-02', periods=9).strftime('%Y-%m-%d').tolist()
        codes = ['600001.SH', '600002.SH', '600003.SH', '600004.SH']
        panels = {key: pd.DataFrame(value,index=dates,columns=codes)
                  for key,value in dict(open=10.,high=11.,low=9.,close=10.,volume=1000.,is_st=0.).items()}
        score = pd.DataFrame(10.,index=dates,columns=codes)
        score.iloc[0]=[4.,3.,2.,1.]
        score.loc[dates[5]:]=[1.,2.,4.,3.]
        # The old holding is a one-price bar on the first eligible rebalance day.
        for key in ['open','high','low','close']:
            panels[key].loc[dates[6], codes[0]] = 1.
        params = dict(start=dates[1],end=dates[-1],initial_capital=1000.,topn=2,
                      rebalance_days=1,min_trade_interval_days=5,max_traded_codes_per_day=5,
                      commission_bps=0,min_commission=0,stamp_tax_bps=0,slippage_bps=0,
                      target_exposure_pct=100,max_position_pct=100)
        result = simulate(panels,score,params)
        self.assertLessEqual(max(row['holding_count'] for row in result['daily_account']),2)
        self.assertIn(codes[0],result['positions'])
        self.assertIn(codes[2],result['positions'])
        self.assertTrue(any(row['reason']=='holding_limit' for row in result['rejections']))


if __name__ == '__main__':
    unittest.main()
