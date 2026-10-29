import unittest
import numpy as np
import pandas as pd
from backtest.research_selection import choose


class StockSelectionTest(unittest.TestCase):
    def test_retention_uses_actual_holdings_and_never_ignores_blocking(self):
        ranked=pd.Series([5.,4.,3.,2.,1.],index=list('ABCDE'))
        result=choose({},ranked,{'C':1.,'E':1.},'2024-01-01',2,dict(retain_rank=3),blocked=['E'])
        self.assertEqual(result,['C','A'])
        self.assertEqual(choose({},ranked,{'C':1.},'2024-01-01',2,dict(retain_rank=0)),['A','B'])

    def test_correlation_filter_and_appended_future_do_not_change_selection(self):
        dates=pd.bdate_range('2024-01-01',periods=85).strftime('%Y-%m-%d')
        x=np.sin(np.arange(85)*.7)*.01+.001
        y=np.cos(np.arange(85)*1.1)*.01+.001
        close=pd.DataFrame({'A':10*np.cumprod(1+x),'B':20*np.cumprod(1+x),'C':10*np.cumprod(1+y)},index=dates)
        rule=dict(retain_rank=0,correlation_limit=.75,correlation_window=70,correlation_min_observations=60,max_candidates=10)
        ranked=pd.Series([3.,2.,1.],index=['A','B','C'])
        first=choose({'close':close},ranked,{},dates[74],2,rule)
        self.assertEqual(first,['A','C'])
        close.iloc[75:]=np.arange(30).reshape(10,3)+1
        self.assertEqual(choose({'close':close},ranked,{},dates[74],2,rule),first)

    def test_missing_correlation_history_does_not_fill_slots(self):
        close=pd.DataFrame({'A':[10.,11.,12.],'B':[20.,19.,18.]},index=['a','b','c'])
        rule=dict(correlation_limit=.75,correlation_window=120,correlation_min_observations=60,max_candidates=10)
        self.assertEqual(choose({'close':close},pd.Series([2.,1.],index=['A','B']),{},'c',2,rule),[])

    def test_replacement_margin_keeps_holding_for_only_a_small_score_gain(self):
        ranked=pd.Series([.950,.945,.800],index=['A','B','C'])
        stable=dict(retain_rank=0,replacement_score_margin=.01)
        self.assertEqual(choose({},ranked,{'B':1.},'2024-01-01',1,stable),['B'])
        self.assertEqual(choose({},ranked,{'B':1.},'2024-01-01',1,
                                dict(retain_rank=0)),['A'])

    def test_replacement_margin_drops_holding_outside_candidate_pool(self):
        ranked=pd.Series([.9,.8],index=['A','B'])
        policy=dict(retain_rank=0,replacement_score_margin=1.)
        self.assertEqual(choose({},ranked,{'STALE':1.},'2024-01-01',2,policy),['A','B'])


if __name__=='__main__':unittest.main()
