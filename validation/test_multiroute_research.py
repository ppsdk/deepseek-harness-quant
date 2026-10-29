"""Behavioral tests for causal boundaries and the ten-name execution contract."""
import unittest
import numpy as np
import pandas as pd
from backtest.research_multiroute import (causal_graph_features, feature, training_positions,
    label_panel, market_states, fit_preprocessing, predict_frozen, make_model, timed_score)
from backtest.research_portfolio import simulate
from factors.research_library import read_config


class MultiRouteTests(unittest.TestCase):
    def setUp(self):
        self.cfg=read_config('multiroute_strategy_research')
        self.dates=pd.Index(pd.bdate_range('2020-01-01',periods=180).strftime('%Y-%m-%d'))
        rng=np.random.default_rng(8)
        self.close=pd.DataFrame(20*np.exp(np.cumsum(rng.normal(0,.01,(180,16)),axis=0)),index=self.dates,columns=[str(i) for i in range(16)])
        self.mask=self.close.notna()
        self.panels=dict(close=self.close,open=self.close*.999,high=self.close*1.03,low=self.close*.97,
            volume=self.close*1000,amount=self.close*1e7,is_st=self.close*0)

    def test_all_technical_features_ignore_appended_future(self):
        cutoff=140
        for key,spec in self.cfg['features'].items():
            if spec['operation'].startswith('valuation'):continue
            short={k:v.iloc[:cutoff] for k,v in self.panels.items()}
            pd.testing.assert_frame_equal(feature(self.panels,spec).iloc[:cutoff],feature(short,spec),obj=key)

    def test_graph_truncation_and_future_perturbation(self):
        cfg=dict(self.cfg['graph'],window=40,min_observations=25,update_days=10)
        ranks={n:self.close.pct_change(5,fill_method=None).rank(axis=1,pct=True) for n in cfg['features']}
        full,updates=causal_graph_features(self.close,self.mask,ranks,cfg)
        short,_=causal_graph_features(self.close.iloc[:130],self.mask.iloc[:130],{n:v.iloc[:130] for n,v in ranks.items()},cfg)
        for n in full:pd.testing.assert_frame_equal(full[n].iloc[:130],short[n])
        self.assertTrue(all(u['history_end']==u['date'] for u in updates))

    def test_training_label_end_is_bounded(self):
        end=self.dates[120];h=20
        pos=training_positions(self.dates,self.dates[0],end,h,1)
        self.assertEqual(self.dates[pos[-1]+h+1],end)
        self.assertTrue(all(self.dates[i+h+1]<=end for i in pos))
        target=label_panel(self.panels['open'],self.mask,h)
        self.assertTrue(target.iloc[-h-1:].isna().all().all())

    def test_preprocessing_excludes_validation_and_pca_retains_variance(self):
        rng=np.random.default_rng(77)
        features={str(i):pd.DataFrame(rng.uniform(size=self.close.shape),index=self.dates,columns=self.close.columns) for i in range(5)}
        names=list(features);end=self.dates[100]
        p,x,y,a=fit_preprocessing(features,names,self.mask,self.dates,self.dates[0],end,self.cfg['preprocessing'],10,5,label_panel(self.close,self.mask,10))
        self.assertLessEqual(a['last_outcome_date'],end)
        self.assertGreaterEqual(a['cumulative_explained_variance'],.9)
        self.assertLessEqual(a['correlation_filter']['max_abs_retained_correlation'],.5)
        changed={n:v.copy() for n,v in features.items()}
        for v in changed.values():v.loc[self.dates> end]=999
        _,xx,yy,aa=fit_preprocessing(changed,names,self.mask,self.dates,self.dates[0],end,self.cfg['preprocessing'],10,5,label_panel(self.close,self.mask,10))
        np.testing.assert_allclose(x,xx);np.testing.assert_allclose(y,yy);self.assertEqual(a,aa)
        model=make_model('ridge',self.cfg['models']['ridge'],42);model.fit(x,y)
        prediction=predict_frozen(model,p,features,names,self.mask,self.dates[110])
        self.assertTrue(prediction.iloc[:110].isna().all().all())
        self.assertTrue(prediction.iloc[110:].notna().all().all())

    def test_regime_switch_changes_selection_and_risk_exposure(self):
        state=pd.DataFrame(dict(offensive=[1,0,0],regime_gate=[1,1,0],ma_gate=[1,0,0]),index=self.dates[:3])
        a=self.close.iloc[:3]*0+1;b=a*2
        score,gate=timed_score(a,b,state,'regime_switch')
        self.assertTrue(score.iloc[0].eq(1).all());self.assertTrue(score.iloc[1].eq(2).all());self.assertTrue(score.iloc[2].isna().all())
        self.assertEqual(list(gate),[1,1,0])

    def test_market_gate_is_prefix_causal(self):
        frame=pd.DataFrame(dict(close=self.close.mean(axis=1)))
        a=market_states(frame,self.close,self.mask,self.cfg['timing'])
        b=market_states(frame.iloc[:140],self.close.iloc[:140],self.mask.iloc[:140],self.cfg['timing'])
        pd.testing.assert_frame_equal(a.iloc[:140],b)

    def test_real_ledger_holds_at_most_ten_and_regular_spacing(self):
        score=self.close.rank(axis=1,pct=True)
        p=dict(self.cfg['execution'],start=self.dates[1],end=self.dates[-1],rebalance_days=5,timing_rule='none')
        sim=simulate(self.panels,score,p)
        self.assertGreater(len(sim['trades']),10)
        self.assertLessEqual(max(a['holding_count'] for a in sim['daily_account']),10)
        days=sorted({t['date'] for t in sim['trades']});positions={d:i for i,d in enumerate(self.dates)}
        self.assertTrue(all(positions[b]-positions[a]>=5 for a,b in zip(days,days[1:])))
        for a in sim['daily_account']:self.assertAlmostEqual(a['equity'],a['cash']+a['market_value'])


if __name__=='__main__':unittest.main()
