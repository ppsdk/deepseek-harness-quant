"""Independent contracts for normalized blends, causal events and shortlist bounds."""
import unittest
import numpy as np
import pandas as pd
from backtest.research_assembly import blend,signals,stable_shortlist,choose_complements,joint_ranking


class AssemblyTests(unittest.TestCase):
    def test_blend_uses_cross_section_ranks_and_requires_every_input(self):
        a=pd.DataFrame([[1.,2.,3.],[1.,2.,3.]],columns=list('abc'))
        b=pd.DataFrame([[3.,2.,1.],[3.,np.nan,1.]],columns=list('abc'))
        eligible=a.notna();records={'a':{'direction':-1},'b':{'direction':1}}
        out=blend({'a':a,'b':b},records,{'a':.75,'b':.25},eligible)
        self.assertAlmostEqual(out.iloc[0,0],1.);self.assertTrue(np.isnan(out.iloc[1,1]))
        for bad in [0.,float('nan'),float('inf'),True]:
            with self.assertRaises(ValueError):blend({'a':a},records,{'a':bad},eligible)

    def test_rank_never_uses_ineligible_stock(self):
        a=pd.DataFrame([[999.,1.,2.]],columns=list('abc'));eligible=pd.DataFrame([[False,True,True]],columns=list('abc'))
        out=blend({'a':a},{'a':{'direction':1}},{'a':1},eligible)
        self.assertTrue(np.isnan(out.a.iloc[0]));self.assertAlmostEqual(out.c.iloc[0],1.)

    def test_ma_events_are_prefix_causal_and_leave_unknown_as_nan(self):
        c=pd.DataFrame({'a':[10.,11.,12.,13.,14.,12.,11.,10.,9.,8.,12.,13.]})
        v=dict(signal='trend',fast=2,slow=3,exit_period=3,confirm_days=2)
        full=signals({'close':c},v);short=signals({'close':c.iloc[:8]},v)
        for key in full:pd.testing.assert_frame_equal(full[key].iloc[:8],short[key])
        self.assertTrue(full['_entry'].iloc[:2].isna().all().all());self.assertEqual(full['_exit'].iloc[7,0],1.)
        p=dict(v,signal='pullback',slope_days=2,min_slow_slope=0.,min_price_vs_fast=-.1,max_price_vs_fast=.1)
        for k,value in signals({'close':c},p).items():pd.testing.assert_frame_equal(value.iloc[:8],signals({'close':c.iloc[:8]},p)[k])

    def test_shortlist_is_bounded_and_prefix_causal(self):
        core=pd.DataFrame([[1.,2.,3.],[3.,2.,1.]],columns=list('abc'));score=core*0+1
        out=stable_shortlist(score,core,-1,core.notna(),2)
        self.assertEqual(out.iloc[0].notna().tolist(),[True,True,False])
        self.assertTrue((out.notna().sum(axis=1)<=2).all())
        pd.testing.assert_frame_equal(out.iloc[:1],stable_shortlist(score.iloc[:1],core.iloc[:1],-1,core.iloc[:1].notna(),2))

    def test_complements_reject_high_correlation_and_same_family(self):
        core={'id':'a','family_id':'core'}
        rows=[dict(id=k,family_id=('core' if k=='b' else k),category='momentum',robust_score=score,rank_ic=.1) for k,score in [('b',4),('c',3),('d',2)]]
        corr=pd.DataFrame(np.eye(4),index=list('abcd'),columns=list('abcd'));corr.loc['c','a']=corr.loc['a','c']=.8
        corr.loc['d','a']=corr.loc['a','d']=.2
        self.assertEqual([r['id'] for r in choose_complements(core,rows,corr,['momentum'],.5,2)],['d'])
        self.assertEqual(choose_complements(core,rows,corr,['momentum'],.5,0),[])

    def test_joint_ranking_checks_pressure_constraints(self):
        def row(annual,passed):return dict(constraints_pass=passed,checks={'exposure':True,'activity':True,'positive_return':True},metrics={'annual_return':annual,'max_drawdown':-.05},annual_one_way_turnover=2.)
        self.assertGreater(joint_ranking(row(.08,True),row(.07,True)),joint_ranking(row(.5,True),row(.4,False)))


if __name__=='__main__':unittest.main()
