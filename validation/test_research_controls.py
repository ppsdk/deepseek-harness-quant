"""Independent numeric, risk-timing and metadata regression cases."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtest import research_runner as runner
from backtest.research_portfolio import capped_weights, weights
from factors import research_controls as controls
from factors.research_library import raw_factor
from data import research_charts as charts
from data import research_cache as cache


class IndicatorControlsTest(unittest.TestCase):
    def test_reject_bad_numbers_unknown_keys_and_conflicting_periods(self):
        bad = [{'ema_20': {'n': True}}, {'ema_20': {'n': float('nan')}},
               {'ema_20': {'n': 2.5}}, {'ema_20': {'n': 0}}, {'missing': {'n': 5}},
               {'ema_20': {'oops': 5}}, {'technical:macd': {'fast': 30, 'slow': 10}},
               {'pattern:rsi_divergence': {'min_separation': 30, 'max_separation': 10}},
               'not json', []]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                controls.validate(value)
        self.assertEqual(controls.validate('{"ema_20":{"n":5}}'), {'ema_20': {'n': 5}})

    def test_changed_legacy_window_has_known_numeric_result_and_no_global_leak(self):
        panel = {'close': pd.DataFrame({'000001.SZ': [10., 11., 12., 15.]})}
        controls.apply(panel, {'mom_20': {'n': 2}})
        result = raw_factor('mom_20', panel)
        self.assertAlmostEqual(result.iloc[-1, 0], 15 / 11 - 1)
        fresh = {'close': panel['close'].copy()}
        controls.apply(fresh, {})
        self.assertNotIn('mom_20', fresh['_indicator_specs'])

    def test_chart_and_factor_use_identical_effective_parameters(self):
        dates = pd.bdate_range('2023-01-02', periods=40).strftime('%Y-%m-%d')
        close = np.arange(40, dtype=float) + 10
        frame = pd.DataFrame(dict(date=dates, open=close, high=close+1,
                                  low=close-1, close=close, volume=1000., turn=1.))
        values = {'ema_20': {'n': 3}}
        with patch.object(charts, 'DailyCache') as daily:
            daily.return_value.get_daily.return_value = frame
            chart = charts.chart_data('000001.SZ', '合成股票', dict(start=dates[10],
                    end=dates[-1], indicators='ema_20', indicator_params=json.dumps(values)))
        expected = pd.Series(close, index=dates).ewm(span=3, adjust=False, min_periods=3).mean()
        actual = chart['indicators']['ema_20']['values']
        for day in dates[10:]:
            self.assertAlmostEqual(actual[day], expected.loc[day], places=7)
        self.assertEqual(chart['indicator_params'], values)
        self.assertGreater(controls.warmup_days({'ema_20': {'n': 800}}),
                           controls.warmup_days({'ema_20': {'n': 20}}))


class PortfolioControlsTest(unittest.TestCase):
    def setUp(self):
        self.days = pd.bdate_range('2024-01-01', periods=9).strftime('%Y-%m-%d').tolist()
        self.codes = ['000001.SZ', '000002.SZ']
        self.p = {f: pd.DataFrame(v, index=self.days, columns=self.codes)
                  for f, v in dict(open=10., high=11., low=9., close=10.,
                                   volume=1000., turn=1., is_st=0.).items()}
        self.score = self.p['close']*0 + [2., 1.]
        self.params = runner.validate_params(dict(strategy='reversal20', start=self.days[1],
                    end=self.days[-1], stocks=2, topn=2, rebalance_days=100,
                    initial_capital=10000, commission_bps=0, min_commission=0,
                    slippage_bps=0, stamp_tax_bps=0), runner.research_config())

    def simulate(self, **extra):
        return runner.simulate(self.p, self.score, self.params | extra)

    def test_total_and_single_stock_cap_leave_cash(self):
        result = self.simulate(target_exposure_pct=60, max_position_pct=20)
        first = result['daily_account'][0]
        self.assertAlmostEqual(first['market_value'], 4000.)
        self.assertAlmostEqual(first['cash'], 6000.)
        self.assertTrue(all(p['weight'] <= .2+1e-10 for p in result['position_details']))
        self.assertAlmostEqual(capped_weights({'A': 8, 'B': 1, 'C': 1}, .6, .25).sum(), .6)
        zero=self.simulate(target_exposure_pct=0)
        self.assertFalse(zero['trades'])
        self.assertAlmostEqual(zero['cash'],10000.)

    def test_trailing_high_is_a_close_and_not_a_slipped_buy_price(self):
        r=self.simulate(trailing_stop_pct=1,slippage_bps=500)
        self.assertFalse(any(t['trigger']=='trailing_stop' for t in r['trades']))

    def test_inverse_volatility_matches_hand_allocation_and_leaves_missing_slot(self):
        dates = pd.bdate_range('2023-01-02', periods=9).strftime('%Y-%m-%d')
        r = np.array([0, .01, -.02, .03, -.01, .02, -.01, .04, -.03])
        closes = pd.DataFrame({'A': 10*np.cumprod(1+r), 'B': 10*np.cumprod(1+2*r),
                               'C': np.nan}, index=dates)
        p = self.params | dict(topn=3, allocation='inverse_volatility', allocation_lookback=5)
        w = weights({'close': closes}, ['A', 'B', 'C'], dates[-1], p)
        self.assertAlmostEqual(w['A']/w['B'], 2.)
        self.assertAlmostEqual(sum(w.values()), 2/3)
        self.assertNotIn('C', w)

    def test_stop_uses_prior_close_then_gap_open_without_rebalancing_other_stock(self):
        self.p['close'].at[self.days[2], self.codes[0]] = 8.
        self.p['open'].at[self.days[3], self.codes[0]] = 7.
        r = self.simulate(stop_loss_pct=10)
        sells = [t for t in r['trades'] if t['side']=='sell']
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]['trigger'], 'stop_loss')
        self.assertEqual(sells[0]['signal_date'], self.days[2])
        self.assertEqual(sells[0]['date'], self.days[3])
        self.assertEqual(sells[0]['fill_price'], 7.)
        self.assertEqual(len([t for t in r['trades'] if t['code']==self.codes[1]]), 1)

    def test_blocked_stop_remains_pending_until_next_tradable_open(self):
        self.p['close'].at[self.days[2], self.codes[0]] = 8.
        for f in ('open','high','low','close'):
            self.p[f].at[self.days[3], self.codes[0]] = 7.
        self.p['open'].at[self.days[4], self.codes[0]] = 6.
        r = self.simulate(stop_loss_pct=10)
        sell = [t for t in r['trades'] if t['side']=='sell'][0]
        self.assertEqual(sell['date'], self.days[4])
        self.assertEqual(sell['signal_date'], self.days[2])
        self.assertEqual(sell['fill_price'], 6.)
        self.assertTrue(any(t['trigger']=='stop_loss' for t in r['rejections']))

    def test_take_profit_trailing_and_max_holding_trigger_next_day(self):
        for key, price, trigger in [('take_profit_pct', 12., 'take_profit'),
                                    ('trailing_stop_pct', 8., 'trailing_stop'),
                                    ('max_holding_days', 10., 'max_holding_days')]:
            with self.subTest(key=key):
                self.p['close'].at[self.days[2], self.codes[0]] = price
                r = self.simulate(**{key: 2 if key=='max_holding_days' else 10})
                sell = [t for t in r['trades'] if t['side']=='sell' and t['code']==self.codes[0]][0]
                self.assertEqual(sell['date'], self.days[3])
                self.assertEqual(sell['trigger'], trigger)

    def test_cooldown_prevents_immediate_repurchase(self):
        self.p['close'].at[self.days[2], self.codes[0]] = 8.
        r = self.simulate(stop_loss_pct=10, rebalance_days=1, risk_cooldown_days=2)
        sold = next(t for t in r['trades'] if t['trigger']=='stop_loss')
        later = [t for t in r['trades'] if t['code']==sold['code'] and t['side']=='buy' and t['date']>sold['date']]
        self.assertTrue(later)
        self.assertGreaterEqual(self.days.index(later[0]['date'])-self.days.index(sold['date']), 3)

    def test_accounting_cost_basis_and_future_do_not_change_past(self):
        p = dict(commission_bps=10, min_commission=5, stamp_tax_bps=5,
                 slippage_bps=20, target_exposure_pct=60, max_position_pct=40)
        r = self.simulate(**p)
        buys = [t for t in r['trades'] if t['side']=='buy']
        for position in r['position_details']:
            trade = next(t for t in buys if t['code']==position['code'])
            self.assertAlmostEqual(position['cost_price'], (trade['amount']+trade['commission'])/trade['quantity'])
        for row in r['daily_account']:
            self.assertAlmostEqual(row['equity'], row['cash']+row['market_value'])
            self.assertGreaterEqual(row['cash'], -1e-8)
        old = deepcopy(r['trades'])
        self.p['close'].iloc[-1] = 1000.
        self.assertEqual(self.simulate(**p)['trades'], old)

    def test_failed_sell_does_not_fund_new_purchase(self):
        self.params['topn']=1
        self.score.iloc[1:]=[0., 1.]
        for f in ('open','high','low','close'):
            self.p[f].at[self.days[2], self.codes[0]]=10.
        r=self.simulate(rebalance_days=1,target_exposure_pct=50)
        self.assertFalse(any(t['date']==self.days[2] and t['side']=='buy' for t in r['trades']))


class MetadataControlsTest(unittest.TestCase):
    def test_different_pending_job_cannot_be_reused(self):
        import hashlib
        import os
        import threading
        from backtest import research_jobs as jobs
        args={'stocks':20}
        signature=hashlib.sha256(json.dumps(args,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
        job=dict(id='a'*32,state='running',pid=os.getpid(),parameter_hash=signature)
        with patch.dict(jobs.ACTIVE,{'backtest':job['id']},clear=True),patch.dict(jobs.RECORDS,{job['id']:job},clear=True):
            self.assertEqual(jobs.start(dict(action='backtest',params=args),threading.Lock())['id'],job['id'])
            with self.assertRaisesRegex(ValueError,'参数不同'):
                jobs.start(dict(action='backtest',params={'stocks':30}),threading.Lock())

    def test_parameters_and_positions_are_saved_without_changing_old_archives(self):
        fixture=PortfolioControlsTest();fixture.setUp()
        values=fixture.params|dict(indicator_params={'mom_20':{'n':5}},target_exposure_pct=60,stop_loss_pct=8)
        controls.apply(fixture.p,values['indicator_params'])
        with tempfile.TemporaryDirectory() as directory,patch.object(runner,'ARCHIVES',Path(directory)), \
             patch.object(runner,'load_panels',return_value=(fixture.p,{'selected_codes':fixture.codes})), \
             patch.object(runner,'factor_score',return_value=fixture.score):
            result=runner.run_research(values)
            saved=json.loads(next(Path(directory).glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(saved['params']['indicator_params'],values['indicator_params'])
        self.assertEqual(saved['position_details'],result['position_details'])
        self.assertEqual(len(saved['provenance']['source_hash']),64)
        self.assertEqual(saved['report']['indicator_parameters'][0]['parameters'][0]['value'],5)

    def test_metadata_copy_and_file_version_invalidation(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'test.db';p.write_bytes(b'a')
            count=[]
            def read():count.append(1);return {'data':list(count)}
            first=cache.metadata('test-case',cache.file_stamp(p),read)
            first['data'].append('changed')
            self.assertEqual(cache.metadata('test-case',cache.file_stamp(p),read)['data'],[1])
            p.write_bytes(b'changed')
            self.assertEqual(len(cache.metadata('test-case',cache.file_stamp(p),read)['data']),2)
            wal=p.with_name('test.db-wal');before=cache.file_stamp(p);wal.write_bytes(b'x')
            self.assertNotEqual(cache.file_stamp(p),before)

    def test_pool_budget_rejects_future_selection(self):
        from backtest.research_positions import plan
        with patch('backtest.research_pools.get_pool',return_value=dict(codes=['000001.SZ'],signal_date='2024-05-01')):
            with self.assertRaisesRegex(ValueError,'股票池选出日期'):
                plan(dict(pool_id='test',start='2024-01-01',end='2024-04-01'))

    def test_budget_before_default_backtest_start_is_valid(self):
        from backtest.research_positions import plan
        frame=pd.DataFrame(dict(date=['2019-12-31'],close=[10.]))
        with patch('data.cache.DailyCache') as daily:
            daily.return_value.get_daily_batch.return_value={'000001.SZ':frame}
            result=plan(dict(codes='000001.SZ',end='2019-12-31',initial_capital=10000,target_exposure_pct=60))
        self.assertAlmostEqual(result['positions'][0]['budget'],6000.)
        self.assertAlmostEqual(result['cash_budget'],4000.)

    def test_gzip_quality(self):
        from deck.deck_server import accepts_gzip
        for value in ('gzip','br, gzip;q=0.8','*;q=0.5'):
            self.assertTrue(accepts_gzip(value))
        for value in ('br','gzip;q=0','gzip;q=bad','*;q=1, gzip;q=0'):
            self.assertFalse(accepts_gzip(value))


if __name__=='__main__':unittest.main()
