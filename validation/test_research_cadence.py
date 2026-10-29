"""Trade spacing, causal MA decisions and genuine emergency exits."""
from copy import deepcopy
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from backtest import research_runner as runner,research_cadence as cadence
from backtest.research_timing import evaluate
from factors.research_library import read_config

class CadenceTest(unittest.TestCase):
    def setUp(self):
        self.days=pd.bdate_range('2024-01-01',periods=25).strftime('%Y-%m-%d').tolist();self.codes=['000001.SZ','600000.SH']
        self.panels={f:pd.DataFrame(v,index=self.days,columns=self.codes) for f,v in dict(open=10.,close=10.,high=11.,low=9.,volume=1000.,is_st=0.).items()}
        self.panels['_entry']=self.panels['close']*0
        self.panels['_entry'].loc[self.days[3]:,self.codes[0]]=1
        self.panels['_exit']=self.panels['close']*0
        self.panels['_exit'].loc[self.days[5]:,self.codes[0]]=1
        self.panels['_entry'].loc[self.days[6]:,self.codes[1]]=1
        self.score=self.panels['close']*0+[2.,1.]
        self.p=runner.validate_params(dict(strategy='reversal20',trade_mode='event',start=self.days[1],end=self.days[-1],stocks=2,topn=1,rebalance_days=20,commission_bps=0,min_commission=0,stamp_tax_bps=0,slippage_bps=0),runner.research_config())

    def test_event_ignores_fixed_20_day_cycle_but_enforces_global_spacing(self):
        r=runner.simulate(self.panels,self.score,self.p)
        self.assertEqual(r['trades'][0]['date'],self.days[4])
        self.assertEqual(next(t['date'] for t in r['trades'] if t['side']=='sell'),self.days[9])
        days=sorted(set(t['date'] for t in r['trades']))
        self.assertTrue(all(self.days.index(b)-self.days.index(a)>=5 for a,b in zip(days,days[1:])))
        self.assertTrue(all(t['signal_date']<t['date'] for t in r['trades']))

    def test_market_exit_can_break_spacing_and_reentry_waits_again(self):
        panels=deepcopy(self.panels);panels['_timing']=self.score*0+1
        panels['_timing'].loc[self.days[5]:self.days[6]]=0
        r=runner.simulate(panels,self.score.where(panels['_timing'].eq(1)),self.p|dict(timing_execution='daily_exit'))
        risk=next(t for t in r['trades'] if t['trigger']=='market_risk_exit')
        self.assertEqual(risk['date'],self.days[6])
        self.assertEqual(risk['signal_date'],self.days[5])
        self.assertTrue(all(self.days.index(t['date'])>=11 for t in r['trades'][2:] if t['side']=='buy'))

    def test_symbol_limit_counts_both_buys_and_sells(self):
        codes=[f'60000{i}.SH' for i in range(1,7)]
        panels={f:pd.DataFrame(v,index=self.days,columns=codes) for f,v in dict(open=10.,close=10.,high=11.,low=9.,volume=1000.,is_st=0.).items()}
        score=pd.DataFrame(np.tile([6.,5.,4.,3.,2.,1.],(len(self.days),1)),index=self.days,columns=codes)
        p=runner.validate_params(dict(strategy='reversal20',start=self.days[1],end=self.days[-1],stocks=6,topn=4,rebalance_days=100,commission_bps=0,min_commission=0,stamp_tax_bps=0,slippage_bps=0,max_traded_codes_per_day=2),runner.research_config())
        r=runner.simulate(panels,score,p)
        daily={}
        for t in r['trades']:daily.setdefault(t['date'],set()).add(t['code'])
        self.assertTrue(all(len(c)<=2 for c in daily.values()))
        self.assertTrue(any(x['reason']=='daily_symbol_limit' for x in r['rejections']))

    def test_full_five_name_replacement_never_touches_more_than_five_names_per_day(self):
        codes=[f'60010{i}.SH' for i in range(10)]
        panels={f:pd.DataFrame(v,index=self.days,columns=codes) for f,v in dict(open=10.,close=10.,high=11.,low=9.,volume=1000.,is_st=0.).items()}
        initial=np.r_[range(10,5,-1),range(5,0,-1)];new=list(range(1,6))+list(range(20,15,-1))
        score=pd.DataFrame(np.tile(initial,(len(self.days),1)),index=self.days,columns=codes)
        score.loc[self.days[1]:]=new
        p=runner.validate_params(dict(strategy='reversal20',start=self.days[0],end=self.days[-1],stocks=10,topn=5,rebalance_days=1,min_trade_interval_days=5,commission_bps=0,min_commission=0,stamp_tax_bps=0,slippage_bps=0,max_traded_codes_per_day=5),runner.research_config())
        r=runner.simulate(panels,score,p)
        daily={}
        for trade in r['trades']:daily.setdefault(trade['date'],set()).add(trade['code'])
        self.assertTrue(all(len(codes)<=5 for codes in daily.values()))
        self.assertEqual(max(len(codes) for codes in daily.values()),5)

    def test_blocked_sell_cannot_force_sixth_holding(self):
        codes=[f'60020{i}.SH' for i in range(10)]
        panels={f:pd.DataFrame(v,index=self.days,columns=codes) for f,v in dict(open=10.,close=10.,high=11.,low=9.,volume=1000.,is_st=0.).items()}
        initial=np.r_[range(10,5,-1),range(5,0,-1)];new=list(range(1,6))+list(range(20,15,-1))
        score=pd.DataFrame(np.tile(initial,(len(self.days),1)),index=self.days,columns=codes)
        score.loc[self.days[1]:]=new
        panels['high'].loc[self.days[6],codes[0]]=10.
        panels['low'].loc[self.days[6],codes[0]]=10.
        p=runner.validate_params(dict(strategy='reversal20',start=self.days[0],end=self.days[-1],stocks=10,topn=5,rebalance_days=1,min_trade_interval_days=5,commission_bps=0,min_commission=0,stamp_tax_bps=0,slippage_bps=0,max_traded_codes_per_day=5),runner.research_config())
        r=runner.simulate(panels,score,p)
        self.assertLessEqual(max(row['holding_count'] for row in r['daily_account']),5)
        self.assertTrue(any(x['code']==codes[0] and x['side']=='sell' and x['reason']=='one_price_bar' for x in r['rejections']))

    def test_ordinary_take_profit_does_not_bypass_interval(self):
        panels=deepcopy(self.panels)
        panels['close'].loc[self.days[5]:,self.codes[0]]=12
        r=runner.simulate(panels,self.score,self.p|dict(take_profit_pct=1))
        self.assertTrue(all(self.days.index(t['date'])>=9 for t in r['trades'] if t['side']=='sell'))

    def test_repair_summary_uses_effective_symbol_interval(self):
        module = __import__('scripts.mainboard_repair_research', fromlist=['finish_summary'])
        item = {'minimum_regular_trade_gap':5,'minimum_regular_after_any_gap':5,
                'metrics':{'max_drawdown':0.,'annual_return':0.,'calmar':0.},
                'max_traded_codes':1,'max_holdings':1,'return_target_pass':False,
                'annual_one_way_turnover':0.}
        policy={'targets':{'max_traded_codes_per_day':5,'max_holdings':5,'max_drawdown':-0.10}}
        with patch.object(module,'summarize',return_value=item):
            result=module.finish_summary({},dict(rebalance_days=100,min_trade_interval_days=5),policy)
        self.assertEqual(result['ordinary_interval_days'],5)
        self.assertTrue(result['frequency_pass'])

    def test_ma_and_risk_states_have_no_future_dependency(self):
        days=pd.bdate_range('2023-01-02',periods=250).strftime('%Y-%m-%d')
        c=np.r_[np.linspace(10,20,160),np.linspace(20,9,40),np.linspace(9,22,50)]
        panel={'close':pd.DataFrame({'A':c},index=days)}
        cadence.attach(panel,self.p);short={'close':panel['close'].iloc[:190].copy()};cadence.attach(short,self.p)
        pd.testing.assert_frame_equal(panel['_entry'].iloc[:190],short['_entry']);pd.testing.assert_frame_equal(panel['_exit'].iloc[:190],short['_exit'])
        cfg=read_config('research_services')['timing'];frame=pd.DataFrame({'close':c},index=days)
        full=evaluate(frame,'index_strong_risk_ma',cfg)
        pd.testing.assert_series_equal(full.iloc[:190],evaluate(frame.iloc[:190],'index_strong_risk_ma',cfg))
        self.assertEqual(full.iloc[200],0);self.assertEqual(full.iloc[-1],1)

if __name__=='__main__':unittest.main()
