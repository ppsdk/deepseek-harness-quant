"""Causal signal and separate-sleeve accounting checks, no performance assertions."""
from copy import deepcopy
import unittest

import numpy as np
import pandas as pd

from backtest.research_dividend_timing import signals, volatility_budget
from backtest.research_cash_actions import CashActions
from scripts.dividend_timing_research import run
from factors.research_library import read_config


class DividendTimingTests(unittest.TestCase):
    def test_cash_actions_require_raw_prices_and_apply_dividend_tax_by_fifo(self):
        events=[dict(id='x-div',code='x',record_date='2024-01-02',ex_date='2024-01-03',
                     pay_date='2024-01-04',available_date='2024-01-01',cash_per_share=.10,
                     split_ratio=1.,tax_kind='personal_stock')]
        panels={'close':pd.DataFrame({'x':[10.,10.,9.9,10.]},index=['2024-01-01','2024-01-02','2024-01-03','2024-01-04']),
                '_cash_actions':events,'_execution_price_basis':'raw'}
        actions=CashActions(panels); shares={}; basis={}; peaks={}; marks={}
        actions.buy('x',100.,'2024-01-01'); shares['x']=100.; basis['x']=10.; peaks['x']=10.; marks['x']=10.
        actions.start_day('2024-01-02',shares,basis,peaks,marks,None)
        actions.end_day('2024-01-02')
        actions.start_day('2024-01-03',shares,basis,peaks,marks,None)
        self.assertEqual(shares['x'],100.)
        self.assertAlmostEqual(actions.start_day('2024-01-04',shares,basis,peaks,marks,None),10.)
        self.assertAlmostEqual(actions.sell('x',100.,'2024-01-31'),2.)
        self.assertAlmostEqual(actions.report('2024-01-31')['gross_dividends'],10.)
        with self.assertRaises(ValueError): CashActions({'close':panels['close'],'_cash_actions':events})

    def test_cash_actions_split_preserves_position_value(self):
        events=[dict(id='split',code='x',record_date='2024-01-01',ex_date='2024-01-02',
                     pay_date='2024-01-02',available_date='2023-12-31',cash_per_share=0.,
                     split_ratio=2.,tax_kind='fund_exempt')]
        panels={'close':pd.DataFrame({'x':[10.,5.]},index=['2024-01-01','2024-01-02']),
                '_cash_actions':events,'_execution_price_basis':'raw'}
        actions=CashActions(panels); shares={'x':100.}; basis={'x':10.}; peaks={'x':10.}; marks={'x':10.}
        actions.start_day('2024-01-02',shares,basis,peaks,marks,None)
        self.assertEqual(shares['x'],200.)
        self.assertEqual(basis['x'],5.)
        self.assertEqual(peaks['x'],5.)
    def test_volatility_sizing_has_trailing_formula_and_is_scale_invariant(self):
        frame=self.frame();p={'close':pd.DataFrame({'x':frame.close})}
        budget,vol=volatility_budget(p,20,.10,.3,242)
        expected=p['close'].iloc[:,0].pct_change(fill_method=None).rolling(20).std()*np.sqrt(242)
        pd.testing.assert_series_equal(vol,expected)
        pd.testing.assert_series_equal(budget,(.10/expected).clip(lower=.3,upper=1)*100)
        prefix,_=volatility_budget({'close':p['close'].iloc[:200]},20,.10,.3,242)
        pd.testing.assert_series_equal(prefix,budget.iloc[:200])
        scaled,_=volatility_budget({'close':p['close']*3},20,.10,.3,242)
        pd.testing.assert_series_equal(scaled,budget)

    def test_dynamic_exposure_executes_previous_day_and_missing_risk_means_cash(self):
        from backtest.research_portfolio import simulate,exposure_budget
        days=['2024-01-01','2024-01-02','2024-01-03','2024-01-04']
        p={k:pd.DataFrame({'x':[v]*4},index=days) for k,v in dict(open=10.,high=11.,low=9.,close=10.,volume=1000.,is_st=np.nan).items()}
        p['_target_exposure_pct']=pd.Series([0.,40.,100.,60.],index=days)
        params=dict(initial_capital=1000.,start=days[1],end=days[-1],topn=1,rebalance_days=1,min_trade_interval_days=1,
                    commission_bps=0.,min_commission=0.,slippage_bps=0.,stamp_tax_bps=0.)
        out=simulate(p,p['close'],params)
        self.assertEqual(out['trades'][0]['date'],days[2])
        self.assertEqual(out['trades'][0]['signal_date'],days[1])
        self.assertAlmostEqual(out['trades'][0]['quantity'],40.)
        p['_target_exposure_pct'].iloc[1]=np.nan
        self.assertEqual(exposure_budget(p,days[1],params),0.)
        p['_target_exposure_pct'].iloc[1]=200.
        with self.assertRaises(ValueError):exposure_budget(p,days[1],params)

    def frame(self, n=300):
        days=pd.bdate_range('2020-01-01',periods=n).strftime('%Y-%m-%d')
        close=10*np.exp(np.sin(np.arange(n)/20)*.05+np.arange(n)*.001)
        return pd.DataFrame(dict(open=close*.998,high=close*1.02,low=close*.98,close=close,
                                 volume=100000.,amount=1000000.,is_st=np.nan),index=days)

    def test_prefix_and_future_perturbation(self):
        frame=self.frame();p={'close':pd.DataFrame({'x':frame.close})}
        full,_=signals(p,60,.02,3)
        short,_=signals({'close':p['close'].iloc[:250]},60,.02,3)
        changed=p['close'].copy();changed.iloc[250:]*=3
        after,_=signals({'close':changed},60,.02,3)
        for name in full:
            pd.testing.assert_frame_equal(full[name].iloc[:250],short[name])
            pd.testing.assert_frame_equal(full[name].iloc[:250],after[name].iloc[:250])
            self.assertTrue(full[name].iloc[:59].isna().all().all())

    def test_symmetric_buffer_and_confirmation_have_independent_math(self):
        close=pd.DataFrame({'x':[10.]*5+[12.]*3+[8.]*4})
        actual,ma=signals({'close':close},5,.02,2)
        above=(close>ma*1.02).where(ma.notna()).astype(float)
        expected=above.rolling(2).sum().eq(2).astype(float).where(ma.notna())
        pd.testing.assert_frame_equal(actual['_entry'],expected)
        self.assertTrue(actual['_entry'].iloc[5].eq(0).all())
        self.assertTrue(actual['_entry'].iloc[6].eq(1).all())
        self.assertTrue(actual['_exit'].iloc[9].eq(1).all())

    def test_sleeves_actual_cash_conservation_t1_and_etf_no_stamp_tax(self):
        cfg=read_config('dividend_timing_research');frame=self.frame()
        period=dict(start=frame.index[200],end=frame.index[-1])
        for group in ('stock','etf'):
            row=run(frame,'x',group,dict(id='test',slow=60,band=.01),period,cfg,ledger=True)
            self.assertTrue(row['trades'])
            self.assertTrue(all(t['date']>t['signal_date'] for t in row['trades']))
            for account in row['daily_account']:
                self.assertAlmostEqual(account['cash']+account['market_value'],account['equity'])
            if group=='etf':
                self.assertTrue(all(t['stamp_tax']==0 for t in row['trades']))
            else:
                self.assertEqual(set(t['sleeve'] for t in row['trades']),{0,1})

    def test_control_and_timing_share_warmup_and_higher_cost_is_visible(self):
        cfg=read_config('dividend_timing_research');frame=self.frame()
        period=dict(start=frame.index[0],end=frame.index[-1])
        v=dict(id='test',slow=60,band=.01)
        baseline=run(frame,'x','etf',dict(id='buy_hold'),period,cfg)
        base=run(frame,'x','etf',v,period,cfg)
        stressed=run(frame,'x','etf',v,period,cfg,stress=True)
        self.assertEqual(base['actual_start'],baseline['actual_start'])
        self.assertEqual(base['metrics']['n_days'],baseline['metrics']['n_days'])
        self.assertLess(stressed['metrics']['final_nav'],base['metrics']['final_nav'])

    def test_bad_parameters_are_rejected(self):
        for args in ((0,.01,3),(5,float('nan'),3),(5,.01,0)):
            with self.assertRaises(ValueError):signals({'close':pd.DataFrame({'x':[1.,2.]})},*args)


if __name__=='__main__':unittest.main()
