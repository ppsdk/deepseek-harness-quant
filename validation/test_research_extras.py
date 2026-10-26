"""Saved selection scope and independently calculated multi-period chart contracts."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch,Mock

import numpy as np
import pandas as pd

from backtest import research_pools as pools,research_runner as runner
from data import research_charts as charts


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(pools,'DB',Path(self.temp.name)/'pools.db');p.start();self.addCleanup(p.stop)

    def save(self):
        return pools.save_pool(dict(name='测试选股',codes=['000001.SZ','600000.SH','000001.SZ'],signal_date='2024-06-03',source_strategy='reversal20'))

    def test_saved_codes_survive_reload_deduplicate_and_change_strategy(self):
        saved=self.save();loaded=pools.get_pool(saved['id'])
        self.assertEqual(loaded['codes'],['000001.SZ','600000.SH'])
        self.assertEqual(len(pools.list_pools()['pools']),1)
        self.assertEqual(pools.list_pools({'q':'没有'})['pools'],[])
        cfg=runner.research_config()
        p=runner.validate_params(dict(pool_id=loaded['id'],strategy='lowvol_defense',start='2024-06-04',topn=1,stocks=2,codes='999999.SZ'),cfg)
        self.assertEqual(p['codes'],'000001.SZ,600000.SH')
        self.assertEqual(p['strategy'],'lowvol_defense')

    def test_future_list_invalid_pool_and_too_small_scope_are_rejected(self):
        saved=self.save();cfg=runner.research_config()
        for values in [dict(start='2024-06-03',stocks=2,topn=1),dict(start='2024-06-04',stocks=1,topn=1),dict(start='2024-06-04',stocks=3,topn=3)]:
            with self.subTest(values=values),self.assertRaises(ValueError):runner.validate_params(dict(values,pool_id=saved['id']),cfg)
        with self.assertRaises(ValueError):runner.validate_params(dict(pool_id='not_found'),cfg)
        for payload in [None,dict(name='空',codes=[]),dict(name='坏代码',codes=['../secret'])]:
            with self.assertRaises(ValueError):pools.save_pool(payload)

    def test_pool_snapshot_and_actual_price_columns_remain_in_backtest_data(self):
        from data.cache import DailyCache
        cache=DailyCache(str(Path(self.temp.name)/'bars.db'))
        dates=pd.bdate_range('2024-01-01',periods=130).strftime('%Y-%m-%d')
        for code in ['000001.SZ','600000.SH','000002.SZ']:
            c=np.arange(len(dates))*.01+10
            frame=pd.DataFrame(dict(date=dates,open=c,high=c+1,low=c-1,close=c+.1,volume=10000,amount=100000,turn=1,is_st=0))
            cache.put_daily(code,frame,adjust='qfq',source='synthetic-test')
        saved=self.save();cfg=runner.research_config()
        p=runner.validate_params(dict(pool_id=saved['id'],strategy='lowvol_defense',start='2024-06-04',end=dates[-1],stocks=2,topn=1),cfg)
        with patch.object(runner,'DailyCache',return_value=cache):panels,data=runner.load_panels(p,cfg)
        self.assertEqual(set(panels['close'].columns),{'000001.SZ','600000.SH'})
        self.assertEqual(data['stock_pool']['code_hash'],saved['code_hash'])
        self.assertEqual(data['stock_pool']['signal_date'],'2024-06-03')


class ChartTests(unittest.TestCase):
    def test_trade_markers_follow_actual_fills_and_aggregate_to_week(self):
        history=pd.DataFrame({'date':['2024-05-13','2024-05-14','2024-05-15']})
        fills=[dict(code='000001.SZ',date='2024-05-14',signal_date='2024-05-13',side='buy',fill_price=10,quantity=100),
               dict(code='000001.SZ',date='2024-05-15',signal_date='2024-05-14',side='sell',fill_price=11,quantity=100),
               dict(code='000001.SZ',date='2024-05-16',signal_date='2024-05-15',side='buy',fill_price=12,quantity=100)]
        run=dict(title='示例',trades=fills,report={'settings':{'trade_chart':{'marker_labels':{'buy':'买','sell':'卖'}}}})
        with patch('factors.research_manager.experiment_detail',return_value=run):
            daily=charts.trade_annotations('test','000001.SZ',history,[{'date':d} for d in history.date],'2024-05-13','2024-05-16')
            weekly=charts.trade_annotations('test','000001.SZ',history,[{'date':'2024-05-15'}],'2024-05-13','2024-05-16')
        self.assertEqual([m['date'] for m in daily['markers']],['2024-05-14','2024-05-15'])
        self.assertEqual(daily['missing_bar_fills'],1)
        self.assertEqual([m['bar_date'] for m in weekly['markers']],['2024-05-15']*2)
        self.assertEqual(weekly['markers'][0]['signal_date'],'2024-05-13')
        self.assertEqual(weekly['markers'][0]['label'],'买')

    def frame(self):
        dates=pd.bdate_range('2022-01-03','2024-05-15').strftime('%Y-%m-%d');c=np.arange(len(dates),dtype=float)+100
        return pd.DataFrame(dict(date=dates,open=c-.5,high=c+2,low=c-2,close=c,volume=10,turn=1))

    def test_week_month_ohlcv_and_last_actual_day(self):
        f=self.frame()
        for period in ['week','month']:
            rule=charts.read_config('research_services')['chart']['timeframes'][period]['rule']
            r=charts.aggregate(f,rule);last=f[pd.to_datetime(f.date).dt.to_period('W-FRI' if period=='week' else 'M')==pd.Timestamp('2024-05-15').to_period('W-FRI' if period=='week' else 'M')]
            self.assertEqual(r.iloc[-1]['date'],'2024-05-15')
            for key,expected in [('open',last.open.iloc[0]),('high',last.high.max()),('low',last.low.min()),('close',last.close.iloc[-1]),('volume',last.volume.sum())]:self.assertEqual(r.iloc[-1][key],expected)
        missing=f[f.date<'2024-05-01'].copy();missing.loc[missing.date.str.startswith('2024-04'),'volume']=np.nan
        self.assertTrue(pd.isna(charts.aggregate(missing,'ME').iloc[-1]['volume']))

    def test_weekly_indicator_computed_after_aggregation_and_overlay_types(self):
        frame=self.frame();cache=Mock();cache.get_daily.return_value=frame
        with patch.object(charts,'DailyCache',return_value=cache):
            r=charts.chart_data('000001.SZ','测试',dict(start='2024-01-01',end='2024-05-15',timeframe='week',indicators='ma_20,ema_20,boll_upper_20,boll_lower_20,rsi_14'))
        weekly=charts.aggregate(frame,'W-FRI').set_index('date').close
        expected=weekly.tail(20).mean();sd=weekly.tail(20).std(ddof=0);day=r['bars'][-1]['date']
        self.assertAlmostEqual(r['indicators']['ma_20']['values'][day],expected)
        self.assertAlmostEqual(r['indicators']['boll_upper_20']['values'][day],expected+2*sd,places=8)
        self.assertAlmostEqual(r['indicators']['boll_lower_20']['values'][day],expected-2*sd,places=8)
        self.assertAlmostEqual(r['indicators']['ema_20']['values'][day],weekly.ewm(span=20,adjust=False,min_periods=20).mean().iloc[-1],places=8)
        self.assertTrue(r['indicators']['ema_20']['overlay']);self.assertFalse(r['indicators']['rsi_14']['overlay'])
        self.assertIn('根K线',r['indicators']['ma_20']['label'])

    def test_market_summary_uses_daily_closes_and_configured_index(self):
        frame=self.frame();cache=Mock();cache.get_daily.return_value=frame
        with patch.object(charts,'DailyCache',return_value=cache):
            r=charts.market(dict(index='000001.SH',timeframe='month',end='2024-05-15'))
        self.assertEqual(r['name'],'上证指数');self.assertEqual(r['adjust'],'none')
        self.assertEqual(r['volume_unit'],'数据源单位')
        self.assertAlmostEqual(r['summary']['returns']['20'],frame.close.iloc[-1]/frame.close.iloc[-21]-1)
        self.assertTrue(all(c.kwargs['adjust']=='none' for c in cache.get_daily.call_args_list))
        with self.assertRaises(ValueError):charts.market({'index':'000001.SZ'})
        with self.assertRaises(ValueError):charts.chart_data('000001.SZ','',{'timeframe':'not_supported'})
        with self.assertRaises(ValueError):charts.chart_data('000001.SZ','',{'start':'2024-05-16','end':'2024-05-15'})


if __name__=='__main__':unittest.main()
