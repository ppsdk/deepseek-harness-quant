"""Independent normalization math, fitted transforms and future perturbations."""
from copy import deepcopy
import unittest
import numpy as np
import pandas as pd
from backtest import research_preprocessing as prep
from backtest.research_ml import predict_walkforward
from backtest.research_ml import validate_recipe
from backtest.research_timing import evaluate
from scripts.mainboard_repair_factor_scan import scan_factor, build_stability_table
from validation.test_research_library import sample


class PreprocessingTests(unittest.TestCase):
    def test_negative_correlation_constant_and_90_percent_gate(self):
        rng=np.random.default_rng(10);independent=rng.normal(size=(1000,5))
        x=np.column_stack([independent[:,0],-independent[:,0],np.ones(1000),independent[:,1:]])
        spec=prep.validate(dict(scaling='standard',correlation_filter='greedy',max_abs_correlation=.5,
                               reduction='pca',component_mode='variance',variance_ratio=.9),7)
        transformer=prep.make_transformer(spec);transformed=transformer.fit_transform(x)
        audit=prep.describe(transformer,spec,list('abcdefg'))
        self.assertEqual(audit['selected_features'],list('adefg'))
        self.assertLessEqual(audit['correlation_filter']['max_abs_retained_correlation'],.5)
        self.assertGreaterEqual(audit['cumulative_explained_variance'],.9)
        self.assertEqual(transformed.shape[1],5)  # independent inputs need all five
        before=deepcopy(audit);transformer.transform(x[:2]*1e9)
        self.assertEqual(before,prep.describe(transformer,spec,list('abcdefg')))
        self.assertEqual(audit['correlation_filter']['dropped_features']['b']['correlated_with'],'a')
        with self.assertRaises(ValueError):prep.CorrelationFilter(.5,3).fit(x[:,:3])
        fixed={**spec,'component_mode':'count','n_components':1}
        insufficient=prep.make_transformer(fixed);insufficient.fit_transform(x)
        with self.assertRaises(ValueError):prep.describe(insufficient,fixed,list('abcdefg'))

    def test_low_correlation_pipeline_does_not_learn_from_future(self):
        panels=sample(160)
        recipe=dict(model='ridge',features=['roc_20','rsi_14','volatility_20'],horizon=5,train_days=60,min_train_days=30,retrain_days=40,
                    preprocessing=dict(scaling='standard',correlation_filter='greedy',max_abs_correlation=.5,min_features=2,reduction='pca',variance_ratio=.9))
        full,folds=predict_walkforward(deepcopy(panels),recipe)
        prefix,_=predict_walkforward({k:v.iloc[:120].copy() for k,v in panels.items()},recipe)
        pd.testing.assert_frame_equal(full.iloc[:120],prefix)
        for fold in folds:
            audit=fold['preprocessing']
            self.assertLessEqual(audit['correlation_filter']['max_abs_retained_correlation'],.5)
            self.assertGreaterEqual(audit['cumulative_explained_variance'],.9)

    def test_legacy_model_scaling_is_reported_and_explicit_none_is_respected(self):
        definitions={'x':{'kind':'technical'}}
        self.assertEqual(validate_recipe(dict(model='ridge',features=['x']),definitions)['preprocessing']['scaling'],'standard')
        self.assertEqual(validate_recipe(dict(model='ridge',features=['x'],preprocessing={'scaling':'none'}),definitions)['preprocessing']['scaling'],'none')

    def test_cross_section_values_constants_and_missing(self):
        frame=pd.DataFrame([[1.,2.,3.,np.nan],[4.,4.,4.,np.nan]])
        z=prep.normalize_frame(frame,'zscore')
        np.testing.assert_allclose(z.iloc[0,:3],[-np.sqrt(1.5),0,np.sqrt(1.5)])
        np.testing.assert_allclose(prep.normalize_frame(frame,'minmax').iloc[0,:3],[0,.5,1])
        self.assertTrue(z.iloc[1,:3].eq(0).all())
        self.assertTrue(z.iloc[:,3].isna().all())
        for mode in prep.settings()['normalization']:
            pd.testing.assert_frame_equal(prep.normalize_frame(frame,mode).iloc[:1],prep.normalize_frame(frame.iloc[:1],mode))

    def test_scaler_and_pca_fit_only_training_and_are_auditable(self):
        x=np.random.default_rng(4).normal(size=(300,3))*[1,100,10000]
        x[:,2]=x[:,1]*100+1
        spec=prep.validate(dict(scaling='standard',reduction='pca',component_mode='variance',variance_ratio=.9),3)
        transformer=prep.make_transformer(spec)
        train=transformer.fit_transform(x)
        before=deepcopy(prep.describe(transformer,spec,['a','b','c']))
        transformer.transform([[1e9,-1e9,1e15]])
        self.assertEqual(before,prep.describe(transformer,spec,['a','b','c']))
        self.assertEqual(train.shape[1],2)
        self.assertGreaterEqual(before['cumulative_explained_variance'],.9)
        with self.assertRaises(ValueError):prep.validate(dict(reduction='pca',component_mode='count',n_components=4),3)
        with self.assertRaises(ValueError):prep.validate(dict(variance_ratio=float('nan')),3)

    def test_walkforward_pca_predictions_invariant_to_future_prices(self):
        panels=sample(160)
        recipe=dict(model='ridge',features=['roc_20','rsi_14','volatility_20'],horizon=5,train_days=60,min_train_days=30,retrain_days=40,
                    preprocessing=dict(normalization='rank',scaling='standard',reduction='pca',component_mode='count',n_components=2))
        full,folds=predict_walkforward(deepcopy(panels),recipe)
        prefix,_=predict_walkforward({k:v.iloc[:120].copy() for k,v in panels.items()},recipe)
        pd.testing.assert_frame_equal(full.iloc[:120],prefix)
        self.assertEqual(folds[0]['preprocessing']['output_dimension'],2)
        self.assertTrue(all(f['last_training_outcome_date']<f['prediction_from'] for f in folds))

    def test_recovery_requires_slopes_and_confirmation_and_stays_causal(self):
        rule=dict(operation='ma_risk_hysteresis_v2',fast=2,slow=4,risk_period=5,drawdown_period=6,drawdown_threshold=-.2,
                  fast_slope_days=1,slow_slope_days=2,recovery_fast_slope=0.,recovery_slow_slope=0.,recovery_drawdown_threshold=-.1,recovery_confirm=3)
        frame=pd.DataFrame({'close':[10.,11.,12.,13.,14.,15.,16.,6.,7.,8.,9.,10.,11.,12.,13.,14.,15.,16.]})
        full=evaluate(frame,'test',{'rules':{'test':rule}})
        pd.testing.assert_series_equal(full.iloc[:14],evaluate(frame.iloc[:14],'test',{'rules':{'test':rule}}))
        self.assertEqual(full.iloc[7],0.)
        self.assertEqual(full.iloc[10],0.)
        self.assertEqual(full.iloc[-1],1.)

    def test_factor_coverage_and_unstable_direction(self):
        panels=sample(40)
        raw=panels['close'].copy();eligible=raw.notna();eligible.iloc[:,2:]=False
        row=scan_factor('x',raw,eligible,panels,str(raw.index[0]),str(raw.index[-1]),dict(horizons=[5],sample_every_days=5,min_stocks=2))
        self.assertEqual(row['coverage'],1.)
        selection=dict(minimum_coverage=.5,minimum_abs_rank_ic=.01,minimum_direction_years=.6)
        metric=lambda ic:dict(rank_ic_mean=ic,rank_ic_ir=1.,direction_year_fraction=1.)
        rows=[dict(factor='x',coverage=1.,horizons={'5':metric(.1),'20':metric(.1)}),dict(factor='x',coverage=1.,horizons={'5':metric(.1),'20':metric(-.1)})]
        self.assertFalse(build_stability_table(rows,selection)[0]['stable_pass'])

    def test_frozen_discovery_fit_ignores_all_validation_values_and_labels(self):
        from scripts.mainboard_pca_research import fit_discovery
        days=pd.bdate_range('2020-01-01',periods=60).strftime('%Y-%m-%d')
        rng=np.random.default_rng(1);x=rng.normal(size=(60,4,3));y=rng.normal(size=(60,4))
        window=dict(start=days[0],end=days[39])
        recipe=dict(horizon=5,max_training_samples=1000,alpha=1.,preprocessing=dict(scaling='standard',reduction='pca',component_mode='count',n_components=2))
        m,t,a=fit_discovery(x,y,days,window,recipe,['a','b','c'])
        changed=x.copy();changed[40:]=1e12;y2=y.copy();y2[34:]=1e12
        m2,t2,a2=fit_discovery(changed,y2,days,window,recipe,['a','b','c'])
        np.testing.assert_allclose(m.coef_,m2.coef_)
        self.assertEqual(a,a2)
        self.assertEqual(a['last_training_outcome_date'],days[39])


if __name__=='__main__':unittest.main()
