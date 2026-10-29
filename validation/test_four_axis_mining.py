"""Independent factor math, causal valuation alignment and adjusted inference."""
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
import sqlite3
import json
import numpy as np
import pandas as pd
from factors.research_axis import calculate
from scripts.four_axis_factor_mining import hac,fdr,inspect_factor,valuation_panels,finalize
from scripts.four_axis_factor_audit import representatives,pca_summary


class FourAxisTests(unittest.TestCase):
    def test_coverage_uses_eligible_denominator_and_horizon_gate(self):
        index=['2024-01-02','2024-01-03'];columns=['a','b','c']
        raw=pd.DataFrame([[1.,2.,9.],[1.,np.nan,8.]],index=index,columns=columns)
        eligible=pd.DataFrame([[True,True,False]]*2,index=index,columns=columns)
        cfg={'research_windows':{'full':{'start':index[0],'end':index[-1]}}}
        self.assertEqual(inspect_factor(raw,eligible,{},cfg)['coverage'],.75)
        cfg['effectiveness']=dict(primary_horizon=20,min_coverage=.5,min_abs_rank_ic=.015,min_year_direction_fraction=.6,min_observations=100,fdr=.1,min_same_direction_horizons=2)
        metric=dict(rank_ic_mean=.05,direction=1,year_direction_fraction=1.,observations=200,p_value=.0001,signed_top_bottom_spread=.01,hac_t=5.)
        record=dict(id='a',category='test',coverage=.75,horizons={'20':metric,'5':dict(metric,rank_ic_mean=-.04)})
        result=finalize([record],cfg)[0]
        self.assertFalse(result['effective']);self.assertFalse(result['gates']['horizons'])

    def test_correlation_rejects_duplicates_and_pca_reaches_target(self):
        rng=np.random.default_rng(8);x=rng.normal(size=(2000,3))
        frame=pd.DataFrame(dict(a=x[:,0],b=x[:,0]*2,c=x[:,1],d=x[:,2]))
        records=[dict(id=k,category='test',effective=True) for k in frame]
        selected=representatives(records,frame.corr(),.5,['test'])
        self.assertEqual(selected,['a','c','d'])
        out=pca_summary(frame[selected],.9)
        self.assertEqual(out['components'],3)
        self.assertGreaterEqual(out['cumulative_explained_variance'],.9)
        self.assertLess(out['max_abs_training_pc_corr'],1e-10)

    def test_hac_and_multiple_testing(self):
        np.testing.assert_allclose(fdr([.001,.02,.4]),[.003,.03,.4])
        rng=np.random.default_rng(2);v=rng.normal(size=300);correlated=np.repeat(v,5)+.3
        self.assertGreater(hac(correlated,5)['p'],hac(correlated,0)['p'])
        self.assertEqual(hac([np.nan],4)['p'],1.)

    def test_yield_sign_zero_and_positive_peg(self):
        p={'close':pd.DataFrame({'a':[10.]*4}),'valuation:pe':pd.DataFrame({'a':[10.,-5.,0.,np.nan]})}
        out=calculate(dict(operation='yield',params={'field':'pe'}),p)
        np.testing.assert_allclose(out.a.iloc[:2],[.1,-.2]);self.assertTrue(out.a.iloc[2:].isna().all())
        out=calculate(dict(operation='yield',params={'field':'pe','positive_only':True}),p)
        self.assertTrue(out.a.iloc[1:].isna().all())

    def test_volatility_and_turnover_math_and_prefix(self):
        c=pd.DataFrame({'a':[10.,11.,10.,12.,11.,13.,12.,14.]})
        p=dict(close=c,open=c*.99,high=c*1.02,low=c*.98,volume=c*100,amount=c*1000,turn=c*.2)
        specs=[dict(operation='parkinson_volatility',params={'n':3}),dict(operation='garman_klass',params={'n':3}),dict(operation='amount_cv',params={'n':3}),dict(operation='risk_adjusted_momentum',params={'n':3}),dict(operation='turn_price_impact',params={'n':3})]
        for spec in specs:
            full=calculate(spec,p);short=calculate(spec,{k:v.iloc[:5] for k,v in p.items()})
            pd.testing.assert_frame_equal(full.iloc[:5],short)
        expected=np.sqrt(np.log(1.02/.98)**2/(4*np.log(2)))
        self.assertAlmostEqual(calculate(specs[0],p).a.iloc[-1],expected)
        expected_cv=p['amount'].a.iloc[-3:].std()/p['amount'].a.iloc[-3:].mean()
        self.assertAlmostEqual(calculate(specs[2],p).a.iloc[-1],expected_cv)

    def test_valuation_rows_never_backfill_before_their_own_date(self):
        with TemporaryDirectory() as td:
            folder=Path(td);db=folder/'v.db'
            (folder/'valuation_universe.json').write_text(json.dumps({'codes':['600000.SH']}))
            con=sqlite3.connect(db);con.execute('create table valuations(code,date,pe,pb,ps,pcf,peg,pe_static,source)')
            con.execute("insert into valuations values('600000.SH','2024-01-03',10,1,2,3,1,9,'test')");con.commit();con.close()
            panels={'close':pd.DataFrame({'600000.SH':[10.,10.,10.]},index=['2024-01-02','2024-01-03','2024-01-04'])}
            cfg={'output_dir':str(folder),'valuation_source':{'db':str(db),'source':'test'}}
            values,codes=valuation_panels(panels,cfg)
            self.assertTrue(np.isnan(values['valuation:pe'].iloc[0,0]));self.assertEqual(values['valuation:pe'].iloc[1,0],10.)
            self.assertTrue(np.isnan(values['valuation:pe'].iloc[2,0]))


if __name__=='__main__':unittest.main()
