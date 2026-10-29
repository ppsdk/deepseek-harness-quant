import unittest
import numpy as np
import pandas as pd

from backtest.research_rule_strategies import components,confirmed,fundamental_mask,market_gate,signals
from factors.research_library import read_config


class RuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.cfg=read_config('technical_rule_research')

    def fixture(self,n=210):
        rng=np.random.default_rng(29)
        close=pd.DataFrame(30*np.exp(np.cumsum(rng.normal(.001,.012,n))),index=pd.bdate_range('2020-01-01',periods=n),columns=['600000.SH'])
        return dict(close=close,open=close*.999,high=close*1.02,low=close*.98,volume=close*0+10000,amount=close*0+1e8)

    def test_valuation_screen_uses_exact_dates_and_rejects_missing(self):
        e=pd.DataFrame(True,index=['a','b','c','d'],columns=['stock'])
        v={n:pd.DataFrame(x,index=e.index,columns=e.columns) for n,x in dict(pe=[10,0,31,10],pb=[1,1,1,1],pcf=[4,4,4,np.nan]).items()}
        m=fundamental_mask(v,e,self.cfg['screens']['reasonable_value'])
        self.assertEqual(m.stock.tolist(),[True,False,False,False])
        self.assertTrue(fundamental_mask(v,e,self.cfg['screens']['no_fundamental']).all().all())

    def test_future_mutation_cannot_change_prior_signals(self):
        p=self.fixture();q={k:v.copy() for k,v in p.items()}
        for name in ['close','open','high','low']:q[name].iloc[170:]*=4
        e=p['close'].notna()
        for spec in [self.cfg['strategies']['ma20_60'],self.cfg['strategies']['ma_kdj_dmi']]:
            a=signals(p,components(p,self.cfg['indicators']),e,e,spec,self.cfg['indicators'])
            b=signals(q,components(q,self.cfg['indicators']),e,e,spec,self.cfg['indicators'])
            for k in ['_entry','_exit']:pd.testing.assert_frame_equal(a[1][k].iloc[:170],b[1][k].iloc[:170])

    def test_fundamental_rejection_blocks_entry_but_preserves_exit(self):
        p=self.fixture();e=p['close'].notna();f=e&False
        _,s,_=signals(p,components(p,self.cfg['indicators']),e,f,self.cfg['strategies']['ma20_60'],self.cfg['indicators'])
        self.assertEqual(s['_entry'].fillna(0).to_numpy().sum(),0)
        self.assertGreater(s['_exit'].notna().to_numpy().sum(),0)

    def test_confirmation_does_not_bridge_unknown_days(self):
        c=pd.Series([True,True,True,True,True]);known=pd.Series([True,True,False,True,True])
        x=confirmed(c,known,2)
        self.assertEqual(x.iloc[1],1);self.assertTrue(pd.isna(x.iloc[2]));self.assertEqual(x.iloc[3],0);self.assertEqual(x.iloc[4],1)

    def test_market_gate_is_prefix_causal(self):
        p=self.fixture();frame=p['close'].rename(columns={'600000.SH':'close'})
        a=market_gate(frame,self.cfg['market'])[0]
        b=market_gate(frame.iloc[:160],self.cfg['market'])[0]
        pd.testing.assert_series_equal(a.iloc[:160],b)

    def test_kdj_high_cross_exit_survives_confirmation(self):
        p=self.fixture(100);p['close'].iloc[:,0]=np.arange(100)+100
        common=components(p,self.cfg['indicators']);common['k'].iloc[-2:,0]=[90,80];common['d'].iloc[-2:,0]=[85,85]
        settings=dict(self.cfg['indicators'],exit_confirm=2)
        spec=dict(self.cfg['strategies']['ma_kdj'],fast=5,slow=20)
        e=p['close'].notna();_,s,_=signals(p,common,e,e,spec,settings)
        self.assertEqual(s['_exit'].iloc[-1,0],1)


if __name__=='__main__':unittest.main()
