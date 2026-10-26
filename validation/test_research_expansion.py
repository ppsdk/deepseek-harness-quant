"""Causal timing, actual exit execution, new indicator math and context integration."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

from backtest import research_runner as runner,research_timing as timing,research_pools as pools
from factors.research_indicators import calculate
from factors.research_library import read_config
from factors.research_diagnostics import clean
from validation.test_research_library import sample


class TimingTests(unittest.TestCase):
    def frame(self):
        rng=np.random.default_rng(3);c=100*np.exp(np.cumsum(rng.normal(.0005,.015,620)))
        return pd.DataFrame({'close':c,'low':c*.98,'high':c*(1.02+rng.random(620)*.02)},index=pd.bdate_range('2022-01-03',periods=620).strftime('%Y-%m-%d'))

    def test_all_rules_are_invariant_to_future_bars_and_rsrs_needs_real_warmup(self):
        cfg=read_config('research_services')['timing'];frame=self.frame()
        for key in cfg['rules']:
            with self.subTest(rule=key):
                full=timing.evaluate(frame,key,cfg);short=timing.evaluate(frame.iloc[:500],key,cfg)
                pd.testing.assert_series_equal(full.iloc[:500],short)
                self.assertTrue(full.dropna().isin([0.,1.]).all())
        rsrs=timing.evaluate(frame,'index_rsrs',cfg)
        self.assertTrue(rsrs.iloc[:266].isna().all());self.assertTrue(rsrs.iloc[280:].notna().any())
        mean60=frame.close.rolling(60).mean()
        expected=(frame.close>mean60).astype(float).where(mean60.notna())
        pd.testing.assert_series_equal(timing.evaluate(frame,'index_above_ma60',cfg),expected)

    def test_daily_exit_uses_previous_close_retries_one_price_bar_and_waits_to_reenter(self):
        panels=sample(35);codes=list(panels['close'].columns);dates=panels['close'].index
        p=runner.validate_params(dict(start=dates[10],end=dates[23],stocks=4,topn=1,rebalance_days=10,timing_rule='index_above_ma60',timing_execution='daily_exit'),runner.research_config())
        panels['_timing']=pd.DataFrame(1.,index=dates,columns=codes);panels['_timing'].loc[dates[12]:dates[15]]=0.
        # A fixed score makes the first code the sole target on both rebalance dates.
        score=pd.DataFrame({c:float(4-i) for i,c in enumerate(codes)},index=dates)
        panels['high'].loc[dates[13],codes[0]]=panels['low'].loc[dates[13],codes[0]]
        result=runner.simulate(panels,score.where(panels['_timing'].eq(1)),p)
        sells=[r for r in result['trades'] if r['side']=='sell']
        self.assertEqual(sells[0]['date'],dates[14]);self.assertEqual(sells[0]['signal_date'],dates[13]);self.assertEqual(sells[0]['trigger'],'market_risk_exit')
        self.assertTrue(any(r['date']==dates[13] and r['reason']=='one_price_bar' for r in result['rejections']))
        buys=[r['date'] for r in result['trades'] if r['side']=='buy'];self.assertEqual(buys,[dates[10],dates[20]])

    def test_compare_uses_one_snapshot_and_archives_both_costed_results(self):
        panels=sample(120);days=panels['close'].index
        panels['_timing']=pd.DataFrame(1.,index=days,columns=panels['close'].columns);panels['_timing'].iloc[85:100]=0.
        with TemporaryDirectory() as td,patch.object(runner,'ARCHIVES',Path(td)),patch.object(runner,'load_panels',return_value=(panels,{'snapshot_hash':'same','selected_codes':list(panels['close'].columns)})) as load:
            r=timing.compare(dict(strategy='reversal20',start=days[70],end=days[-1],stocks=4,topn=1,timing_rule='index_above_ma60',timing_execution='daily_exit'))
            self.assertEqual(load.call_count,1);self.assertEqual(len(list(Path(td).glob('*.json'))),2)
            self.assertEqual(r['baseline']['data']['snapshot_hash'],r['timed']['data']['snapshot_hash'])
            self.assertEqual(r['baseline']['params']['timing_rule'],'none');self.assertGreater(r['timed']['costs']['total'],0)
            self.assertNotEqual(r['baseline']['nav'],r['timed']['nav'])


class IndicatorAndIntegrationTests(unittest.TestCase):
    def test_cmo_donchian_turn_volatility_and_aroon_match_independent_values(self):
        p=sample(90);c=p['close'];d=c.diff();up=d.clip(lower=0).rolling(14).sum();down=-d.clip(upper=0).rolling(14).sum()
        pd.testing.assert_frame_equal(calculate({'operation':'cmo','params':{'n':14}},p),(100*(up-down)/(up+down)).mask((up+down)==0,0))
        pd.testing.assert_frame_equal(calculate({'operation':'donchian_upper','params':{'n':20}},p),p['high'].shift().rolling(20).max())
        pd.testing.assert_frame_equal(calculate({'operation':'turn_volatility','params':{'n':60}},p),p['turn'].rolling(60).std(ddof=1))
        p['high']=p['close'].copy();p['high'].iloc[:]=np.arange(len(c))[:,None]+10
        a=calculate({'operation':'aroon_up','params':{'n':25}},p)
        self.assertTrue(a.iloc[:25].isna().all().all());self.assertTrue(a.iloc[25:].eq(100).all().all())
        with self.assertRaises(ValueError):runner.validate_params({'strategy':'turn_stable','start':'2018-01-01'},runner.research_config())

    def test_daily_cleaning_does_not_use_future_rows(self):
        policy=read_config('research_services')['factor_analysis'];raw=pd.DataFrame([[1,2,3,1000],[2,3,4,5],[1e9,-1e9,0,1]])
        for mode in policy['preprocessing']:
            pd.testing.assert_frame_equal(clean(raw,mode,policy).iloc[:2],clean(raw.iloc[:2],mode,policy))
        self.assertLess(clean(raw,'daily_winsor',policy).iloc[0,3],1000)
        with self.assertRaises(ValueError):clean(raw,'future_fill',policy)

    def test_catalog_integrates_articles_tutorials_and_exposes_missing_etf_data(self):
        from backtest.research_catalog import catalog
        r=catalog();self.assertEqual(r['counts']['社区文章参考'],4);self.assertEqual(r['counts']['开源项目与教程'],8)
        etf=[x for x in r['entries'] if x['id'] in ('jq_etf_discount','jq_etf_execution')]
        self.assertTrue(all(not x['strategy_ids'] for x in etf));self.assertTrue(all('需要' in x['status'] or '未实现' in x['status'] for x in etf))
        self.assertTrue(any(x['id']=='turn_stable' and x['sources'] for x in r['entries']))

    def test_assistant_uses_selected_pool_and_generates_draft_without_model_call(self):
        from backtest.research_assistant import draft
        with TemporaryDirectory() as td,patch.object(pools,'DB',Path(td)/'pools.db'),patch('data.research_services.timing_status',return_value={'benchmark':{'code':'000300.SH'},'signals':[],'executions':{}}):
            p=pools.save_pool({'name':'我的名单','codes':['000001.SZ'],'signal_date':'2024-06-03'})
            r=draft({'task':'review_timing','strategy':'turn_stable','pool_id':p['id'],'question':'先检查费用和未来数据。'})
        self.assertIn('2024-06-03',r['prompt']);self.assertIn('000001.SZ',r['prompt']);self.assertIn('turn_volatility_60',r['context_text']);self.assertIn('点击',r['message'])
        with self.assertRaises(ValueError):draft({'task':'execute_arbitrary_file'})


if __name__=='__main__':unittest.main()
