"""Online prices are isolated from local storage; chart indicators use the workbench engine."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock

import numpy as np
import pandas as pd

from data import market_online as online,market_cloud as cloud

class MarketCloudTests(unittest.TestCase):
    def setUp(self):
        online._cache.clear()
        self.quote=';'.join(self.quote_row(s) for s in ['sh000300','sh000001'])
        self.dates=pd.bdate_range('2025-01-02',periods=90)
        index=np.arange(len(self.dates),dtype=float)
        self.close=100+index*.15+np.sin(index/4)*4
        self.rows=[]
        for i,day in enumerate(self.dates):
            close=float(self.close[i])
            self.rows.append(dict(day=day.strftime('%Y-%m-%d'),open=str(close-.2),
                high=str(close+1),low=str(close-1),close=str(close),volume=str(1000+i),
                ma_price7='777',ma_price13='1313',ma_price20='2020'))
        for target in ['data.cache.DailyCache.__init__','data.market_analysis.compute','data.market_analysis.breadth']:
            p=patch(target,side_effect=AssertionError('Local market computation is forbidden'));p.start();self.addCleanup(p.stop)

    def quote_row(self,symbol):
        fields=['0']*40;fields[3]='123';fields[30]='20261027161412';fields[32]='-1.23'
        return 'v_'+symbol+'="'+'~'.join(fields)+'"'

    def source(self,url,params=None):
        return SimpleNamespace(text=self.quote if 'qt.gtimg' in url else 'callback('+json.dumps(self.rows)+');')

    def test_current_remote_quotes_do_not_load_local_old_analysis(self):
        # Statistical market analysis must not invoke the local indicator engine.
        with patch.object(online,'_get',side_effect=self.source),patch('factors.research_indicators.calculate',side_effect=AssertionError('local indicator called')):
            r=cloud.analysis()
        self.assertEqual(r['record']['date'],'2026-10-27');self.assertFalse(r['local_calculation'])
        row=r['record']['indices'][0];self.assertEqual(row['close'],123);self.assertEqual(row['ma20'],2020)
        self.assertIsNone(row['ma60']);self.assertTrue(r['record']['warnings'])

    def test_workbench_computes_arbitrary_ema_from_cloud_prices_without_local_prices(self):
        query=dict(index='000300.SH',start=self.dates[59].strftime('%Y-%m-%d'),end=self.dates[-1].strftime('%Y-%m-%d'),indicators='ema_20',
                   indicator_params=json.dumps({'ema_20':{'n':13}}),overlays=json.dumps([{'base':'ema_20','params':{'n':7}}]))
        with patch.object(online,'_get',side_effect=self.source):r=cloud.chart(query)
        self.assertTrue(r['local_calculation']);self.assertFalse(r['local_price_access'])
        self.assertEqual(r['indicator_params']['ema_20']['n'],13)
        self.assertEqual(len(r['bars']),31)
        expected=pd.Series(self.close,index=self.dates).ewm(span=13,adjust=False,min_periods=13).mean()
        overlay=pd.Series(self.close,index=self.dates).ewm(span=7,adjust=False,min_periods=7).mean()
        last=self.dates[-1].strftime('%Y-%m-%d')
        self.assertAlmostEqual(r['indicators']['ema_20']['values'][last],expected.loc[self.dates[-1]],places=9)
        self.assertAlmostEqual(r['indicators']['ema_20#1']['values'][last],overlay.loc[self.dates[-1]],places=9)

    def test_workbench_computes_macd_and_shape_with_arbitrary_parameters(self):
        params={'technical:macd':{'fast':10,'slow':20,'signal':5}}
        query=dict(index='000300.SH',start=self.dates[59].strftime('%Y-%m-%d'),end=self.dates[-1].strftime('%Y-%m-%d'),
                   indicators='macd_dif,macd_hist,rsi_divergence',indicator_params=json.dumps(params))
        with patch.object(online,'_get',side_effect=self.source):r=cloud.chart(query)
        self.assertIn('macd_dif',r['indicators']);self.assertIn('macd_hist',r['indicators'])
        self.assertTrue(any(item['id']=='rsi_divergence' for item in r['patterns']['studies']))
        c=pd.Series(self.close,index=self.dates)
        dif=c.ewm(span=10,adjust=False,min_periods=10).mean()-c.ewm(span=20,adjust=False,min_periods=20).mean()
        dea=dif.ewm(span=5,adjust=False,min_periods=5).mean()
        last=self.dates[-1].strftime('%Y-%m-%d')
        self.assertAlmostEqual(r['indicators']['macd_dif']['values'][last],float(dif.loc[self.dates[-1]]),places=9)
        self.assertAlmostEqual(r['indicators']['macd_hist']['values'][last],float((dif-dea).loc[self.dates[-1]]*2),places=9)

    def test_cloud_only_mode_still_maps_provider_values_without_local_calculate(self):
        cfg=deepcopy(online.settings());cfg['public']['chart_indicator_mode']='cloud_only'
        query=dict(index='000300.SH',start=self.dates[59].strftime('%Y-%m-%d'),end=self.dates[-1].strftime('%Y-%m-%d'),indicators='ma_20',
                   indicator_params=json.dumps({'ma_20':{'n':13}}),overlays=json.dumps([{'base':'ma_20','params':{'n':7}}]))
        with patch.object(online,'settings',return_value=cfg),patch.object(online,'_get',side_effect=self.source),patch('factors.research_indicators.calculate',side_effect=AssertionError('local calculate called')):
            r=cloud.chart(query)
        last=self.dates[-1].strftime('%Y-%m-%d')
        self.assertEqual(r['indicators']['ma_20']['values'][last],1313)
        self.assertEqual(r['indicators']['ma_20#1']['values'][last],777)
        self.assertFalse(r['local_calculation'])

    def test_empty_selection_means_candles_only_and_network_errors_stay_errors(self):
        with patch.object(online,'_get',side_effect=self.source):r=cloud.chart(dict(index='000300.SH',indicators=''))
        self.assertFalse(r['indicators']);self.assertEqual(r['warnings'],[])
        online._cache.clear()
        with patch.object(online,'_get',side_effect=ValueError('Network unavailable')):
            with self.assertRaisesRegex(ValueError,'Network unavailable'):cloud.analysis()

    def test_historical_cloud_snapshot_is_not_replaced_by_current_quote(self):
        with patch.object(online,'_get',side_effect=self.source):
            with self.assertRaisesRegex(ValueError,'历史分析快照'):cloud.analysis({'date':'2025-01-01'})

    def test_unconfigured_remote_endpoint_is_clear_and_token_not_leaked(self):
        cfg=cloud.settings();cfg['provider']='remote';cfg['analysis_url']='';cfg['analysis_url_env']='TEST_CLOUD_MISSING_URL'
        with patch.object(cloud,'settings',return_value=cfg):
            with self.assertRaisesRegex(ValueError,'TEST_CLOUD_MISSING_URL'):cloud.request('analysis')
        response=Mock(status_code=403);response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        session=Mock();session.__enter__=Mock(return_value=session);session.__exit__=Mock(return_value=False);session.request.return_value=response
        cfg['analysis_url']='https://example.com/analysis'
        with patch.object(cloud,'settings',return_value=cfg),patch.object(cloud.requests,'Session',return_value=session):
            with self.assertRaisesRegex(ValueError,'HTTP 403'):cloud.request('analysis')

if __name__=='__main__':unittest.main()