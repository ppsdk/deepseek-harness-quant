"""Regression of actual request contracts and independent parameter instances."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from factors.research_library import read_config,factor_definitions
from factors.research_controls import catalogue,validate
from data import research_charts as charts,research_services as services,akshare_loader as loader
from backtest.research_ml import validate_recipe
from backtest import research_signals as signals


class WorkspaceRepairsTest(unittest.TestCase):
    def test_all_numeric_fields_are_exposed_and_arbitrary_horizon_is_valid(self):
        defs=factor_definitions()
        self.assertIn('m',catalogue(defs)['turnover_acceleration_5']['fields'])
        self.assertEqual(validate({'turnover_acceleration_5':{'m':37}})['turnover_acceleration_5']['m'],37)
        synthetic={'demo':dict(kind='technical',operation='demo',label='Demo',params={'new_window':17,'alpha':.5})}
        self.assertEqual(set(catalogue(synthetic)['demo']['fields']),{'new_window','alpha'})
        recipe=validate_recipe(dict(model='ridge',features=['ema_20'],horizon=7),defs)
        self.assertEqual(recipe['horizon'],7)

    def test_pattern_parameter_instances_do_not_overwrite_primary(self):
        dates=pd.bdate_range('2023-01-02',periods=350).strftime('%Y-%m-%d')
        c=np.arange(350)*.01+10+np.sin(np.arange(350)/7)
        frame=pd.DataFrame(dict(date=dates,open=c,close=c,high=c+.2,low=c-.2,volume=1000.,amount=10000.,turn=1.))
        key=read_config('research_patterns')['chart_presets']['gaussian']['indicators'][0]
        query=dict(start=dates[100],end=dates[-1],indicators=key)
        with patch.object(charts,'DailyCache') as cache:
            cache.return_value.get_daily.return_value=frame
            plain=charts.chart_data('000001.SZ','Synthetic',query)
            stacked=charts.chart_data('000001.SZ','Synthetic',{**query,'overlays':json.dumps([{'base':key,'params':{'window':13,'sigma':4}}])})
        for name,line in plain['indicators'].items():self.assertEqual(stacked['indicators'][name],line)
        self.assertTrue(any(name.startswith(key+'#1:') for name in stacked['indicators']))
        self.assertEqual(read_config('research_patterns')['families']['gaussian']['params']['window'],21)

    def test_retry_reads_entire_failed_list_and_respects_requested_dates(self):
        failed=[dict(code='000001.SZ',state='failed'),dict(code='600000.SH',state='failed')]
        fake=SimpleNamespace(pid=12345,poll=lambda:None)
        with TemporaryDirectory() as folder,patch.object(services,'BASE',Path(folder)),patch.object(services,'_download_process',None),patch.object(loader,'read_status',side_effect=lambda summary=False:dict(state='partial',stocks=failed[-1:] if summary else failed)),patch.object(services.subprocess,'Popen',return_value=fake) as spawn:
            result=services.update_stocks(dict(start='2024-01-01',end='2025-12-31',retry_failed=True))
            command=spawn.call_args.args[0]
            self.assertIn('000001.SZ,600000.SH',command)
            self.assertIn('2024-01-01',command);self.assertIn('2025-12-31',command)
            self.assertEqual(result['pid'],12345)
            self.assertTrue(services.update_stocks()['existing']);self.assertEqual(spawn.call_count,1)

    def test_directory_update_uses_local_fallback_without_rewriting_cache(self):
        rows=pd.DataFrame([dict(code='600519.SH',name='fixture')])
        local_meta=dict(source='local_cache',attempts=2,warning='fixture warning')
        online_meta=dict(source='online',attempts=1)
        with patch.object(loader,'load_config',return_value=dict(universe='all_a')),patch.object(loader,'load_all_a_universe',return_value=(rows,local_meta)),patch.object(loader,'persist_universe') as persist,patch.object(services,'update_listing_dates',return_value=dict(updated=0,errors=[])):
            result=services.update_directory()
        persist.assert_not_called()
        self.assertEqual(result['source'],'local_cache')
        self.assertIn('沿用本地目录',result['message'])
        with patch.object(loader,'load_config',return_value=dict(universe='all_a')),patch.object(loader,'load_all_a_universe',return_value=(rows,online_meta)),patch.object(loader,'persist_universe') as persist,patch.object(services,'update_listing_dates',return_value=dict(updated=1,errors=[])):
            result=services.update_directory()
        persist.assert_called_once()
        self.assertEqual(result['source'],'online')

    def test_interrupted_status_is_read_only_and_invalid_dates_do_not_launch(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'status.json';text=json.dumps(dict(state='running',pid=98765));path.write_text(text)
            with patch.object(loader,'STATUS',path),patch.object(services,'pid_alive',return_value=False):
                self.assertEqual(loader.read_status()['state'],'interrupted')
            self.assertEqual(path.read_text(),text)
        with patch.object(loader,'read_status',return_value=dict(state='completed')),patch.object(services,'_download_process',None),patch.object(services.subprocess,'Popen') as spawn:
            with self.assertRaises(ValueError):services.update_stocks(dict(start='2025-12-31',end='2024-01-01'))
            spawn.assert_not_called()

    def test_summary_keeps_failures_outside_recent_progress_window(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'status.json'
            path.write_text(json.dumps(dict(state='partial',stocks=[dict(code='000001.SZ',state='failed')]+[dict(code='600000.SH',state='ok')]*150)))
            with patch.object(loader,'STATUS',path):result=loader.read_status(summary=True)
            self.assertEqual(len(result['stocks']),100)
            self.assertEqual(result['failed_stocks'][0]['code'],'000001.SZ')

    def test_candidate_creation_uses_shared_screen_and_signal_date(self):
        result=dict(candidates=[dict(code='000001.SZ')],signal_date='2025-01-02',strategy='turn_low',params={'end':'2025-01-02'})
        with patch('backtest.research_workspace.screen',return_value=result) as screen,patch('backtest.research_pools.save_pool',return_value=dict(count=1,signal_date='2025-01-02')) as save:
            signals.create_candidates(dict(pool_name='Research',strategy='turn_low'))
            self.assertEqual(screen.call_args.args[0],dict(strategy='turn_low'))
            self.assertEqual(save.call_args.args[0]['signal_date'],'2025-01-02')
            self.assertEqual(save.call_args.args[0]['source_strategy'],'turn_low')


if __name__=='__main__':unittest.main()
