"""Independent checks for purge boundaries, parameter dimensions and plateaus."""
from copy import deepcopy
import unittest
import numpy as np
import pandas as pd
from backtest.research_factor_search import parameter_grid,restrict_targets,select_training,tail_metrics
from factors.research_axis import calculate
from scripts.factor_parameter_budget import calibrate


class ParameterSearchTests(unittest.TestCase):
    def test_exposure_calibration_uses_discovery_and_keeps_budget_headroom(self):
        source=dict(id='train',phase='discovery',metrics={'max_drawdown':-.2},annual_one_way_turnover=6.)
        cfg={'portfolio':{'max_drawdown':-.1,'max_annual_one_way_turnover':3.}}
        budget=dict(maximum_exposure_pct=100,minimum_exposure_pct=10,exposure_step_pct=5,drawdown_headroom=.8,turnover_headroom=.8)
        result=calibrate(source,cfg,budget);self.assertEqual(result['exposure_pct'],40)
        self.assertFalse(result['uses_validation']);self.assertFalse(result['below_minimum'])
        with self.assertRaises(ValueError):calibrate(dict(source,phase='validation'),cfg,budget)
        tiny=calibrate(dict(source,annual_one_way_turnover=100.),cfg,budget)
        self.assertTrue(tiny['below_minimum'])

    def config(self):
        return {'search':dict(periods=[5,10,20,40],relative_valuation_periods=[60,252],smoothing_periods=[1,5,20],
            overrides={'liquidity_accel':dict(parameter='m',values=[20,40,60])},min_coverage=.5,min_abs_rank_ic=.02,
            min_year_stability=.6,min_observations=100,fdr=.1,primary_horizon=20,min_same_direction_neighbors=1,
            min_neighbor_fraction=.5,robust_quantile=.25,parameters_per_family=3),'portfolio':{'primary_topn':5}}

    def test_grid_changes_one_parameter_and_keeps_default(self):
        original=dict(operation='turnover_accel',params={'n':5,'m':20},label='test')
        records=parameter_grid('liquidity','accel',{'source_factor':'test','label':'test'},self.config(),{'test':original})
        self.assertTrue(any(r['is_default'] for r in records))
        for row in records:
            changed=[k for k,v in row['definition']['params'].items() if original['params'].get(k)!=v]
            self.assertLessEqual(len(changed),1);self.assertEqual(row['definition']['params']['n'],5)
        self.assertEqual(original['params'],{'n':5,'m':20})

    def test_exit_open_purged_at_training_boundary(self):
        dates=pd.Index(pd.bdate_range('2024-01-01',periods=40).strftime('%Y-%m-%d'))
        pos=np.array([0,5,10,15,20]);future=pd.DataFrame({'a':np.arange(5.)},index=dates[pos]);valid=np.ones((5,1),dtype=bool)
        result=restrict_targets({5:(pos,future,valid)},dates,dict(start=dates[0],end=dates[15]))
        np.testing.assert_array_equal(result[5][0],[0,5])
        changed=future.copy();changed.iloc[2:]=999
        pd.testing.assert_frame_equal(result[5][1],restrict_targets({5:(pos,changed,valid)},dates,dict(start=dates[0],end=dates[15]))[5][1])

    def test_isolated_peak_rejected_and_plateau_accepted(self):
        def row(value,ic):
            m=dict(rank_ic_mean=ic,direction=1 if ic>0 else -1,year_direction_fraction=1.,observations=200,p_value=.001,
                   signed_top_bottom_spread=.01,hac_t=4.)
            return dict(id='n'+str(value),family_id='f',value=value,coverage=1.,horizons={'5':deepcopy(m),'20':deepcopy(m)},
                        tails={'5':{'mean_forward_excess':.01}})
        rows,_=select_training([row(5,.001),row(10,.1),row(20,.001)],self.config())
        self.assertFalse(next(r for r in rows if r['value']==10)['robust_pass'])
        rows,shortlist=select_training([row(5,.06),row(10,.07),row(20,.065)],self.config())
        self.assertTrue(all(r['robust_pass'] for r in rows));self.assertEqual(len(shortlist['f']),3)

    def test_valuation_smoothing_is_causal_and_exact(self):
        frame=pd.DataFrame({'a':[1.,2.,4.,8.,16.]})
        panel={'close':frame*0+10,'valuation:pb':frame}
        spec=dict(operation='yield',params={'field':'pb','smooth_n':3})
        expected=(1/frame).rolling(3).mean()
        actual=calculate(spec,panel);pd.testing.assert_frame_equal(actual,expected)
        short=calculate(spec,{k:v.iloc[:4] for k,v in panel.items()})
        pd.testing.assert_frame_equal(actual.iloc[:4],short)

    def test_tail_keeps_frozen_direction_and_never_promotes_future_tradable(self):
        frame=pd.DataFrame([[1.,2.,3.]],index=['2024-01-02'],columns=['a','b','c'])
        eligible=frame.notna();future=frame*0+[.1,.2,.3]
        target=(np.array([0]),future,np.array([[False,True,True]]))
        low=tail_metrics(frame,eligible,target,-1,[1])['1']
        self.assertIsNone(low['mean_forward_return']);self.assertEqual(low['unavailable_label_fraction'],1.)
        high=tail_metrics(frame,eligible,target,1,[1])['1']
        self.assertAlmostEqual(high['mean_forward_return'],.3)


if __name__=='__main__':unittest.main()
