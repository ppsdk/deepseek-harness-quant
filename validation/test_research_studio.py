"""Causal factor/ML experiments and management persistence contracts."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch

import numpy as np
import pandas as pd

from validation.test_research_library import sample
from factors import research_manager as manager
from factors.research_library import factor_definitions,raw_factor,read_config
from backtest import research_ml as ml,research_runner as runner,research_jobs as jobs
from data import research_services as services


class FormulaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(manager,'CUSTOM',Path(self.temp.name)/'factors.yaml');p.start();self.addCleanup(p.stop)

    def save(self,key,formula):
        return manager.save_factor(dict(id=key,label=key,formula=formula,revision=manager.revision()))

    def test_formula_matches_independent_return_and_survives_reload(self):
        self.save('my_return','close / delay(close, 5) - 1')
        p=sample(30)
        pd.testing.assert_frame_equal(raw_factor('my_return',p),p['close']/p['close'].shift(5)-1)
        self.save('nested_return','rank(my_return)')
        p=sample(30)
        pd.testing.assert_frame_equal(raw_factor('nested_return',p),(p['close']/p['close'].shift(5)-1).rank(axis=1,pct=True))
        self.save('my_amount','adv20')
        p=sample(30);pd.testing.assert_frame_equal(raw_factor('my_amount',p),p['amount'].rolling(20).mean())

    def test_cycle_builtin_unknown_unsafe_and_stale_revision_rejected(self):
        self.save('first_factor','roc_20')
        self.save('second_factor','first_factor')
        for key,formula in [('first_factor','second_factor'),('roc_20','close'),('unknown_factor','not_installed'),('unsafe_factor',"__import__('os')")]:
            with self.subTest(formula=formula),self.assertRaises(ValueError):self.save(key,formula)
        with self.assertRaises(ValueError):manager.save_factor(dict(id='stale_factor',label='旧版本',formula='close',revision='old'))

    def test_dependency_edits_refresh_descendant_requirements(self):
        self.save('first_factor','fund_roe')
        self.save('second_factor','first_factor')
        self.assertIn('fund:roe',manager.custom_factors()['second_factor']['requires'])
        self.save('first_factor','turn')
        self.assertEqual(manager.custom_factors()['second_factor']['requires'],['turn'])
        cfg=runner.research_config();cfg['strategies']['test']={'factor_list':[{'name':'second_factor'}]}
        with self.assertRaises(ValueError):runner.validate_params({'strategy':'test','start':'2018-01-01'},cfg)

    def test_factor_forward_return_is_next_open_and_missing_prices_excluded(self):
        p=sample(90)
        # Eight stocks, price paths with known next-open returns and stable ordering.
        for key in p:
            p[key]=pd.concat([p[key],p[key].set_axis(['000003.SZ','000004.SZ','600003.SH','600004.SH'],axis=1)],axis=1)
        for j,c in enumerate(p['close'].columns):
            path=10*(1+(j+1)*.001)**np.arange(90)
            for f,m in [('open',1),('close',1.001),('high',1.02),('low',.98)]:p[f][c]=path*m
        self.save('path_order','close / delay(close, 1) - 1')
        cfg=runner.research_config()
        with patch.object(runner,'research_config',return_value=cfg),patch.object(runner,'load_panels',return_value=(p,{})),patch.object(manager,'ANALYSES',Path(self.temp.name)/'analyses'):
            r=manager.analyse(dict(factor='path_order',start=p['close'].index[10],end=p['close'].index[70],stocks=8,topn=2,horizon=5,groups=4))
        self.assertAlmostEqual(r['ic_mean'],1,places=7)
        expected=np.mean([(1+.001*j)**5-1 for j in (7,8)])
        self.assertAlmostEqual(r['groups'][-1]['mean_forward_return'],expected,places=10)
        self.assertTrue((Path(self.temp.name)/'analyses'/(r['run_id']+'.json')).exists())
        with patch.object(manager,'ANALYSES',Path(self.temp.name)/'analyses'):
            self.assertEqual(manager.analysis_records({'id':r['run_id']})['ic_mean'],r['ic_mean'])
            self.assertEqual(len(manager.analysis_records({})['analyses']),1)
            with self.assertRaises(ValueError):manager.analysis_records({'id':'../secret'})


class MachineLearningTests(unittest.TestCase):
    def recipe(self,model='decision_tree'):
        return dict(model=model,features=['roc_20','rsi_14'],horizon=5,train_days=60,min_train_days=30,retrain_days=40)

    def test_all_five_models_train_and_prediction_has_no_future_dependency(self):
        p=sample(145);short={k:v.iloc[:120].copy() for k,v in p.items()}
        for model in ml.ml_config()['models']:
            with self.subTest(model=model):
                full,folds=ml.predict_walkforward(deepcopy(p),self.recipe(model))
                earlier,_=ml.predict_walkforward(deepcopy(short),self.recipe(model))
                self.assertTrue(full.iloc[-1].notna().all())
                pd.testing.assert_frame_equal(full.iloc[:120],earlier)
                self.assertTrue(all(f['last_training_outcome_date']<f['prediction_from'] for f in folds))
                index=p['close'].index
                self.assertTrue(all(index.get_loc(f['prediction_from'])-index.get_loc(f['last_training_feature_date'])==7 for f in folds))

    def test_training_recipe_rejects_recursive_models_and_bad_bounds(self):
        definitions=factor_definitions()
        for r in [dict(features=['ml_random_forest']),dict(features=['missing']),dict(train_days=60,min_train_days=100),dict(retrain_days=1.5)]:
            with self.assertRaises(ValueError):ml.validate_recipe(r,definitions)

    def test_ml_result_uses_shared_cash_ledger_and_archives_training(self):
        with tempfile.TemporaryDirectory() as td:
            p=sample(145);start=p['close'].index[95];end=p['close'].index[-1]
            with patch.object(runner,'load_panels',return_value=(p,{'selected_codes':list(p['close'].columns)})),patch.object(runner,'ARCHIVES',Path(td)):
                result=ml.run_ml(dict(start=start,end=end,stocks=4,topn=2,rebalance_days=5,slippage_bps=20,recipe=self.recipe()))
            self.assertGreater(len(result['trades']),0)
            self.assertGreater(result['costs']['slippage_cost'],0)
            self.assertGreater(result['costs']['commission'],0)
            saved=json.loads(next(Path(td).glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(saved['recipe']['model'],'decision_tree');self.assertTrue(saved['training'])


class ServiceTests(unittest.TestCase):
    def test_timing_missing_benchmark_and_no_future_leak(self):
        from backtest.research_timing import attach_timing
        p=sample(100);cache=Mock();cache.get_daily.return_value=None
        with self.assertRaises(ValueError):attach_timing(p,{'timing_rule':'index_above_ma60'},cache,'2024-01-01','2024-12-31')
        df=pd.DataFrame({'date':p['close'].index,'close':np.arange(100)+100.})
        cache.get_daily.return_value=df
        attach_timing(p,{'timing_rule':'index_above_ma60'},cache,'','')
        old=p['_timing'].copy();df.loc[90:,'close']=1
        attach_timing(p,{'timing_rule':'index_above_ma60'},cache,'','')
        pd.testing.assert_frame_equal(old.iloc[:90],p['_timing'].iloc[:90])
        self.assertTrue(p['_timing'].iloc[:59].isna().all().all())

    def test_papers_preserve_metadata_and_failure_keeps_existing_cache(self):
        atom=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2601.00001v1</id><title>Test title</title><summary>Original abstract.</summary><author><name>A</name></author><published>2026-01-01</published></entry></feed>'
        response=Mock(content=atom)
        with tempfile.TemporaryDirectory() as td,patch.object(services,'PAPERS',Path(td)/'papers.json'),patch('requests.get',return_value=response) as get:
            services.update_papers();r=services.paper_list();self.assertEqual(r['papers'][0]['abstract'],'Original abstract.')
            self.assertEqual(r['papers'][0]['authors'],['A']);self.assertTrue(r['papers'][0]['url'].startswith('https://'))
            get.side_effect=RuntimeError('offline')
            with self.assertRaises(RuntimeError):services.update_papers()
            self.assertEqual(services.paper_list(),r)

    def test_existing_download_is_reused_and_no_process_spawned(self):
        import os
        self.assertTrue(services.pid_alive(os.getpid()))
        self.assertFalse(services.pid_alive(-1))
        with patch('data.akshare_loader.read_status',return_value={'state':'running','pid':123,'completed':3,'requested_stocks':10}),patch.object(services,'pid_alive',return_value=True),patch('subprocess.Popen') as spawn:
            self.assertTrue(services.update_stocks()['existing']);spawn.assert_not_called()

    def test_experiment_annotations_and_path_traversal_rejection(self):
        with tempfile.TemporaryDirectory() as td,patch.object(runner,'ARCHIVES',Path(td)),patch.object(manager,'META',Path(td)/'notes.db'):
            key='research_example_'+'a'*32
            (Path(td)/(key+'.json')).write_text(json.dumps({'run_id':'a'*32,'title':'example'}),encoding='utf-8')
            manager.annotate(dict(id=key,note='复查费用',favorite=True))
            self.assertTrue(manager.experiment_list({})['runs'][0]['annotation']['favorite'])
            with self.assertRaises(ValueError):manager.experiment_detail('../secret')

    def test_jobs_are_deduplicated_and_stale_jobs_report_interruption(self):
        event=threading.Event();entered=threading.Event()
        def update():entered.set();event.wait(5);return {'message':'完成'}
        with tempfile.TemporaryDirectory() as td,patch.object(jobs,'JOBS',Path(td)),patch.object(services,'update_papers',side_effect=update):
            try:
                first=jobs.start({'action':'papers'},threading.Lock());entered.wait(2)
                second=jobs.start({'action':'papers'},threading.Lock());self.assertEqual(first['id'],second['id'])
                event.set()
                for _ in range(100):
                    if jobs.status(first['id'])['state']!='running':break
                    time.sleep(.02)
                self.assertEqual(jobs.status(first['id'])['state'],'done')
                d=jobs.status(first['id']);d.update(state='running',pid=-1)
                jobs.RECORDS.pop(first['id'],None)
                (Path(td)/(first['id']+'.json')).write_text(json.dumps(d),encoding='utf-8')
                self.assertEqual(jobs.status(first['id'])['state'],'done')
                (Path(td)/(first['id']+'.result.json')).unlink()
                self.assertEqual(jobs.status(first['id'])['state'],'failed')
            finally:event.set()

    def test_finished_job_remains_readable_when_status_file_is_locked(self):
        entered=threading.Event();release=threading.Event()
        def update():entered.set();release.wait(3);return {'message':'完成'}
        with tempfile.TemporaryDirectory() as td,patch.object(jobs,'JOBS',Path(td)),patch.object(services,'update_papers',side_effect=update):
            job=jobs.start({'action':'papers'},threading.Lock());entered.wait(2)
            with patch.object(jobs,'_write_disk',side_effect=PermissionError('Windows reader')):
                release.set()
                for _ in range(100):
                    if 'papers' not in jobs.ACTIVE:break
                    time.sleep(.02)
                self.assertEqual(jobs.status(job['id'])['state'],'done')
                self.assertEqual(jobs.status(job['id'],with_result=True)['message'],'完成')
                self.assertNotIn('papers',jobs.ACTIVE)


if __name__=='__main__':unittest.main()
