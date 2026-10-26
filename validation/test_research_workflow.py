"""Independent formula, signal timing and gap-repair contracts."""
from copy import deepcopy
from contextlib import ExitStack,closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

from factors.alpha101_engine import evaluate
from factors.research_formula import compile_formula,build_conditions,preview
from factors import research_manager as manager
from data import research_quality as quality
from data.cache import DailyCache
from backtest import research_signals as signals,research_runner as runner
from validation import test_research_controls as fixtures


class FormulaWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.patch=patch.object(manager,'CUSTOM',Path(self.t.name)/'custom.yaml');self.patch.start();self.addCleanup(self.patch.stop)

    def test_aliases_known_numbers_crosses_and_missingness(self):
        p={'close':pd.DataFrame({'A':[1.,2.,3.,2.,5.,np.nan,7.]})}
        pd.testing.assert_frame_equal(evaluate('MA(close, 2)',p),p['close'].rolling(2).mean())
        pd.testing.assert_frame_equal(evaluate('REF(close, 1)',p),p['close'].shift())
        x=evaluate('CROSS(close, 2)',p)['A'].tolist()
        self.assertTrue(np.isnan(x[0]));self.assertEqual(x[1:5],[0,1,0,1]);self.assertTrue(np.isnan(x[6]))
        counts=evaluate('COUNT(close > 1, 2)',p)['A']
        self.assertEqual(counts.iloc[2],2);self.assertTrue(np.isnan(counts.iloc[6]))

    def test_bad_arity_future_dynamic_period_unknown_and_constant_only(self):
        for f in ['MA(close)','CROSS(close,2,3)','REF(close,-1)','delay(close,-1)',
                  'MA(close,2.5)','MA(close,close)','close + unknown_field','close.__class__','close;import os']:
            with self.subTest(f=f),self.assertRaises(ValueError):compile_formula(f)
        self.assertGreaterEqual(compile_formula('MA(MA(close, 500), 500)')['warmup_calendar_days'],3000)

    def test_formula_builder_and_saved_factor_chart_metadata(self):
        built=build_conditions({'join':'all','conditions':[dict(left='RSI(close,14)',operator='gt',right='30'),dict(left='close',operator='cross_up',right='MA(close,20)')]})
        self.assertIn('CROSS',built['formula']);self.assertIn('&&',built['formula'])
        result=manager.save_factor(dict(id='my_line',label='自己的均线',category='趋势',description='说明',formula='MA(close, 3)',overlay=True,revision=manager.revision()))
        item=manager.custom_factors()[result['id']]
        self.assertTrue(item['chart']);self.assertTrue(item['overlay']);self.assertIn('close',item['requires'])
        derived=compile_formula('MA(my_line, 100)')
        self.assertGreaterEqual(derived['warmup_calendar_days'],309)

    def test_no_future_and_preview_does_not_save(self):
        days=pd.bdate_range('2024-01-01',periods=50).strftime('%Y-%m-%d');c=np.arange(50,dtype=float)+10
        frame=pd.DataFrame(dict(date=days,open=c,high=c+1,low=c-1,close=c,volume=1000.,amount=10000.,turn=1.))
        with tempfile.TemporaryDirectory() as folder:
            cache=DailyCache(Path(folder)/'bars.db');cache.put_daily('000001.SZ',frame,source='akshare')
            with patch.object(runner,'DailyCache',return_value=cache),patch('data.research_charts.DailyCache',return_value=cache):
                result=preview(dict(formula='MA(close, 3)',codes='000001.SZ',stocks=1,start=days[10],end=days[-1]))
            self.assertAlmostEqual(result['rows'][-1]['value'],c[-3:].mean())
            self.assertAlmostEqual(result['chart']['indicators']['formula_preview']['values'][days[-1]],c[-3:].mean())
            self.assertFalse(manager.CUSTOM.exists())
        full=evaluate('CROSS(RSI(close,14),30)',{'close':pd.DataFrame({'A':c+np.sin(c)*10})})
        shorter=evaluate('CROSS(RSI(close,14),30)',{'close':pd.DataFrame({'A':c[:40]+np.sin(c[:40])*10})})
        pd.testing.assert_frame_equal(full.iloc[:40],shorter)


class SignalWorkflowTest(unittest.TestCase):
    def test_fixed_plan_captures_dependencies_and_rejects_other_pool(self):
        pool=dict(id='a'*32,name='候选',count=2,codes=['000001.SZ','000002.SZ'],signal_date='2024-01-01')
        with tempfile.TemporaryDirectory() as folder,patch.object(signals,'DB',Path(folder)/'plans.db'),patch('backtest.research_pools.get_pool',return_value=pool):
            saved=signals.save(dict(name='均线',pool_id=pool['id'],entry='CROSS(ma_20,ma_60)',exit='CROSS(ma_60,ma_20)'))
            plan=signals.plans({'id':saved['id']});self.assertIn('ma_20',plan['definitions'])
            cfg=runner.research_config()
            cfg['parameters']['pool_id']['options']['b'*32]='另一个池'
            with self.assertRaisesRegex(ValueError,'不一致'):
                runner.validate_params(dict(signal_plan_id=saved['id'],pool_id='b'*32),cfg)

    def test_entry_waits_prior_close_and_position_remains_until_exit(self):
        f=fixtures.PortfolioControlsTest();f.setUp();p=f.p
        p['_entry']=p['close']*0.;p['_exit']=p['close']*0.
        p['_entry'].at[f.days[2],f.codes[0]]=1
        p['_exit'].at[f.days[5],f.codes[0]]=1
        r=runner.simulate(p,f.score,f.params|dict(topn=1,rebalance_days=1))
        self.assertEqual(len(r['trades']),2)
        self.assertEqual(r['trades'][0]['date'],f.days[3]);self.assertEqual(r['trades'][0]['trigger'],'technical_entry')
        self.assertEqual(r['trades'][1]['date'],f.days[6]);self.assertEqual(r['trades'][1]['trigger'],'technical_exit')
        self.assertTrue(all(t['signal_date']<t['date'] for t in r['trades']))

    def test_conflict_no_entry_and_missing_entry_not_zero(self):
        f=fixtures.PortfolioControlsTest();f.setUp();p=f.p
        p['_entry']=p['close']*0+np.nan;p['_exit']=p['close']*0
        p['_entry'].at[f.days[2],f.codes[0]]=1;p['_exit'].at[f.days[2],f.codes[0]]=1
        self.assertFalse(runner.simulate(p,f.score,f.params)['trades'])


class QualityWorkflowTest(unittest.TestCase):
    def test_listing_date_update_preserves_unknown_and_does_not_accept_future(self):
        from data.research_services import update_listing_dates
        frame=pd.DataFrame({'证券代码':['000001','000002'],'上市日期':['1991-04-03','2999-01-01']})
        with tempfile.TemporaryDirectory() as folder:
            import sqlite3
            db=Path(folder)/'stock_basic.db'
            with closing(sqlite3.connect(db)) as con,con:
                con.execute('CREATE TABLE stock_basic(code TEXT PRIMARY KEY,ipo_date TEXT)')
                con.executemany('INSERT INTO stock_basic VALUES(?,?)',[('000001.SZ',None),('000002.SZ',None)])
            sources=[dict(function='stock_info_bj_name_code',kwargs={},code='证券代码',ipo='上市日期',market='SZ')]
            from data.research_services import settings
            from factors.research_library import read_config as real_read
            def config(name):
                return dict(real_read(name),directory_sources=sources) if name=='research_workflow' else real_read(name)
            with patch('data.cache.CACHE_DIR',Path(folder)),patch('akshare.stock_info_bj_name_code',return_value=frame),patch('factors.research_library.read_config',side_effect=config):
                result=update_listing_dates()
            self.assertEqual(result['updated'],1)
            with closing(sqlite3.connect(db)) as con,con:rows=dict(con.execute('SELECT code,ipo_date FROM stock_basic'))
            self.assertEqual(rows['000001.SZ'],'1991-04-03');self.assertIsNone(rows['000002.SZ'])

    def frame(self):
        return pd.DataFrame(dict(date=['2024-01-02','2024-01-03','2024-01-05'],open=10.,high=11.,low=9.,close=10.,volume=100.,amount=1000.,turn=1.,is_st=None))

    def test_real_calendar_missing_fields_and_prelisting_excluded(self):
        calendar=['2024-01-02','2024-01-03','2024-01-04','2024-01-05'];frame=self.frame()
        frame.loc[1,'volume']=0;frame.loc[2,'turn']=np.nan
        r=quality.inspect_frame(frame,'2024-01-01','2024-01-07',calendar,'2024-01-03')
        self.assertEqual(r['missing_dates'],['2024-01-04']);self.assertEqual(r['zero_volume_days'],1)
        self.assertEqual(r['fields']['turn']['dates'],['2024-01-05'])
        self.assertNotIn('2024-01-06',r['missing_dates']);self.assertFalse(r['start_unverified'])

    def test_source_conflict_not_merged_and_missing_only_from_real_source(self):
        a=self.frame();a.loc[1,'amount']=np.nan;b=self.frame();b.loc[1,'amount']=2000.
        merged,ok=quality.merge_compatible(a,b);self.assertTrue(ok);self.assertEqual(merged.loc[1,'amount'],2000.)
        b['close']*=1.5
        merged,ok=quality.merge_compatible(a,b);self.assertFalse(ok);self.assertTrue(np.isnan(merged.loc[1,'amount']))

    def test_repair_restores_date_and_fields_then_rereads_cache(self):
        full=self.frame();full=pd.concat([full,pd.DataFrame([dict(full.iloc[0],date='2024-01-04')])],ignore_index=True).sort_values('date')
        old=full[full.date!='2024-01-04'].copy();old.loc[old.date=='2024-01-03','amount']=np.nan
        calendar=full.date.tolist();code='000001.SZ';pool=([code],{code:{'name':'合成'}},{code:'2020-01-01'})
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            cache=DailyCache(Path(folder)/'bars.db');cache.put_daily(code,old,source='akshare')
            stack.enter_context(patch('data.cache.DailyCache',return_value=cache))
            stack.enter_context(patch.object(quality,'REPORT',Path(folder)/'quality.json'))
            stack.enter_context(patch.object(quality,'universe',return_value=pool))
            stack.enter_context(patch.object(quality,'local_calendar',return_value=(calendar,'合成日历')))
            stack.enter_context(patch.object(quality,'refresh_calendar',return_value=calendar))
            stack.enter_context(patch('data.fetcher_akshare.fetch_daily',return_value=full))
            stack.enter_context(patch('data.akshare_loader.STATUS',Path(folder)/'status.json'))
            result=quality.repair(dict(start='2024-01-01',end='2024-01-07',codes=[code]))
            self.assertEqual(result['repair']['restored_dates'],1)
            self.assertEqual(result['repair']['restored_fields'],1)
            self.assertEqual(result['summary']['missing_dates'],0);self.assertEqual(result['summary']['missing_fields'],0)
            self.assertEqual(len(cache.get_daily(code)),4)
            self.assertTrue(quality.REPORT.exists())

    def test_old_valid_fields_survive_same_price_refresh_with_source_holes(self):
        old=self.frame();new=old.copy();new.loc[1,'amount']=np.nan
        overlap,agrees=quality.merge_compatible(new,old[old.date.isin(new.date)])
        self.assertTrue(agrees);self.assertEqual(overlap.loc[1,'amount'],1000.)

    def test_failure_keeps_old_cache_and_reports_remaining(self):
        old=self.frame();code='000001.SZ';calendar=old.date.tolist()+['2024-01-08']
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            cache=DailyCache(Path(folder)/'bars.db');cache.put_daily(code,old,source='akshare')
            stack.enter_context(patch('data.cache.DailyCache',return_value=cache))
            stack.enter_context(patch.object(quality,'REPORT',Path(folder)/'quality.json'))
            stack.enter_context(patch.object(quality,'universe',return_value=([code],{},{})))
            stack.enter_context(patch.object(quality,'local_calendar',return_value=(calendar,'test')))
            stack.enter_context(patch.object(quality,'refresh_calendar',side_effect=ConnectionError('calendar offline')))
            stack.enter_context(patch('data.fetcher_akshare.fetch_daily',side_effect=ConnectionError('offline')))
            stack.enter_context(patch('data.akshare_loader.STATUS',Path(folder)/'status.json'))
            result=quality.repair(dict(start='2024-01-01',end='2024-01-08',codes=[code]))
            self.assertEqual(len(cache.get_daily(code)),3);self.assertEqual(result['repair']['restored_dates'],0)
            self.assertEqual(len(result['repair']['details'][0]['errors']),3)
            self.assertTrue(result['repair']['calendar_error']);self.assertEqual(result['summary']['missing_dates'],1)

if __name__=='__main__':unittest.main()
