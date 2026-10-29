"""Technical rule causality, trade spacing, exit triggers and relaxed returns."""
from copy import deepcopy
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from backtest.research_technical import candidate
from backtest.research_portfolio import simulate
from scripts.mainboard_technical_research import assess,overall_ranking
from scripts.mainboard_pca_research import fit_discovery


class TechnicalTests(unittest.TestCase):
    def test_overall_ranking_has_no_single_year_or_fixed_return_gate(self):
        row=dict(activity_pass=True,technical_constraints_pass=True,profitable=True,
                 metrics=dict(calmar=.1),annual_one_way_turnover=.7)
        # No year-specific fields are required; a low positive return is valid.
        self.assertEqual(overall_ranking(row,row),(True,True,True,.1,-.7))
        failed=deepcopy(row);failed['technical_constraints_pass']=False
        self.assertGreater(overall_ranking(row,row),overall_ranking(row,failed))

    def test_momentum_and_ma_prefixes_ignore_future_prices(self):
        dates=pd.bdate_range('2020-01-01',periods=40).strftime('%Y-%m-%d')
        close=pd.DataFrame({'a':np.arange(40)+10.,'b':np.arange(40)*.5+20.},index=dates)
        eligible=close.notna()
        common=dict(average='sma',fast=3,slow=6,confirm_days=2,momentum_periods=[3,5],absolute_period=5,skip_days=2,score='momentum',mode='event')
        for operation in ['dual_momentum','ma_trend']:
            spec={**common,'operation':operation}
            full,signals=candidate({'close':close},eligible,eligible,spec)
            short,ss=candidate({'close':close.iloc[:25]},eligible.iloc[:25],eligible.iloc[:25],spec)
            pd.testing.assert_frame_equal(full.iloc[:25],short)
            for key in signals:pd.testing.assert_frame_equal(signals[key].iloc[:25],ss[key])
        fixed={**common,'operation':'dual_momentum','mode':'fixed'}
        score,signals=candidate({'close':close},eligible,eligible,fixed)
        self.assertNotIn('_entry',signals)
        self.assertTrue(score.iloc[:7].isna().all().all())

    def test_stop_loss_and_take_profit_remain_weekly_and_use_original_close_signal(self):
        dates=pd.bdate_range('2024-01-02',periods=15).strftime('%Y-%m-%d')
        for jump,trigger,limits in [(8.,'stop_loss',dict(stop_loss_pct=5)),(13.,'take_profit',dict(take_profit_pct=20))]:
            panels={k:pd.DataFrame(v,index=dates,columns=['600001.SH']) for k,v in dict(open=10.,high=14.,low=7.,close=10.,volume=1000.,is_st=0.,_entry=1.,_exit=0.).items()}
            panels['close'].iloc[2]=jump
            params=dict(start=dates[1],end=dates[-1],initial_capital=10000.,topn=1,trade_mode='event',rebalance_days=20,min_trade_interval_days=5,max_traded_codes_per_day=5,commission_bps=0,min_commission=0,stamp_tax_bps=0,slippage_bps=0,max_position_pct=100,**limits)
            result=simulate(panels,panels['_entry'],params)
            sale=next(t for t in result['trades'] if t['side']=='sell')
            self.assertEqual(sale['date'],dates[6]);self.assertEqual(sale['signal_date'],dates[2]);self.assertEqual(sale['trigger'],trigger)

    def test_low_positive_return_is_not_failed_for_missing_twenty_percent(self):
        row=dict(annual_one_way_turnover=1.,average_exposure=.2,trade_dates=5,metrics=dict(total_return=.02),drawdown_pass=True,frequency_pass=True,holding_count_pass=True,trade_count_pass=True,return_target_pass=False)
        policy=dict(technical_acceptance=dict(max_annual_one_way_turnover=3.,min_average_exposure=.03,min_trade_dates=4))
        with patch('scripts.mainboard_technical_research.finish_summary',return_value=deepcopy(row)):
            result=assess({}, {},policy)
        self.assertTrue(result['target_pass']);self.assertIsNone(result['return_target_pass'])

    def test_forest_fit_excludes_validation_features_and_outcomes(self):
        dates=pd.bdate_range('2020-01-01',periods=60).strftime('%Y-%m-%d')
        rng=np.random.default_rng(40);x=rng.normal(size=(60,4,4));y=rng.normal(size=(60,4))
        recipe=dict(model='random_forest',horizon=5,max_training_samples=1000,alpha=1.,preprocessing=dict(scaling='standard',reduction='pca',variance_ratio=.9))
        window=dict(start=dates[0],end=dates[39])
        m,t,a=fit_discovery(x,y,dates,window,recipe,list('abcd'))
        altered=x.copy();altered[40:]*=1e9;y[34:]=1e9
        m2,t2,a2=fit_discovery(altered,y,dates,window,recipe,list('abcd'))
        np.testing.assert_allclose(m.predict(t.transform(x[:2].reshape(-1,4))),m2.predict(t2.transform(x[:2].reshape(-1,4))))
        self.assertEqual(a,a2);self.assertLess(a['last_training_outcome_date'],dates[40])


if __name__=='__main__':unittest.main()
