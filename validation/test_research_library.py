"""Formula, indicator, dated-finance and strategy-filter contracts."""
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from factors.alpha101_engine import evaluate, parse
from factors.research_indicators import calculate
from factors.research_library import factor_definitions, read_config
from backtest import research_runner as runner, research_workspace as workspace
from data import research_fundamentals as finance


def sample(n=340):
    rng = np.random.default_rng(123)
    c = pd.DataFrame(np.exp(rng.normal(0, .02, (n, 4)).cumsum(axis=0))*20,
                     index=pd.bdate_range('2024-01-01', periods=n).strftime('%Y-%m-%d'),
                     columns=['000001.SZ','000002.SZ','600001.SH','600002.SH'])
    vol = pd.DataFrame(rng.uniform(1e5, 1e6, c.shape), index=c.index, columns=c.columns)
    p = dict(close=c, open=c*.997, high=c*1.02, low=c*.98, volume=vol,
             amount=vol*c, cap=c*1e9, vwap=c*.995, raw_close=c,
             turn=c*.01, is_st=c*np.nan)
    for k in ('industry', 'sector', 'subindustry'):
        p[k] = pd.DataFrame([['a','a','b','b']]*n, index=c.index, columns=c.columns)
    return p


class AlphaTests(unittest.TestCase):
    def test_all_101_parse_evaluate_and_do_not_read_future(self):
        formulas = read_config('alpha101')['factors']
        self.assertEqual(len(formulas), 101)
        p = sample()
        # Extend into the future: every existing date must retain the same value.
        short = {k: v.iloc[:300] for k, v in p.items()}
        for key, meta in formulas.items():
            with self.subTest(key=key):
                out = evaluate(meta['formula'], p)
                expected = evaluate(meta['formula'], short)
                self.assertEqual(out.shape, p['close'].shape)
                self.assertFalse(np.isinf(out.to_numpy()).any())
                pd.testing.assert_frame_equal(out.iloc[:300], expected)

    def test_independent_simple_formulas(self):
        p = sample(40)
        forms = read_config('alpha101')['factors']
        pd.testing.assert_frame_equal(evaluate(forms['alpha101_101']['formula'], p),
            (p['close']-p['open'])/((p['high']-p['low'])+.001))
        pd.testing.assert_frame_equal(evaluate(forms['alpha101_012']['formula'], p),
            np.sign(p['volume'].diff()) * -p['close'].diff())
        pd.testing.assert_frame_equal(evaluate(forms['alpha101_004']['formula'], p),
            -p['low'].rank(axis=1,pct=True).rolling(9).rank(pct=True))
        pd.testing.assert_frame_equal(evaluate('adv20', p), p['amount'].rolling(20).mean())

    def test_missing_math_floor_industry_and_no_arbitrary_calls(self):
        p = sample(30)
        p['close'].iloc[20,0] = np.nan
        self.assertTrue(pd.isna(evaluate('(close>1)?1:0',p).iloc[20,0]))
        pd.testing.assert_frame_equal(evaluate('sum(volume,3.9)',p), p['volume'].rolling(3).sum())
        out = evaluate('IndNeutralize(volume,IndClass.industry)',p)
        self.assertAlmostEqual(float(out.iloc[-1,:2].sum()),0,places=6)
        for formula in ("__import__('os')", 'close.__class__', 'exec(1)', 'close;volume'):
            with self.subTest(formula=formula), self.assertRaises(ValueError):
                evaluate(formula,p)


class IndicatorTests(unittest.TestCase):
    def test_registered_rolling_pca_is_causal_and_aligned(self):
        from factors.research_library import raw_factor
        p = sample(180)
        spec = {
            'id': 'rolling_pca_test', 'kind': 'composite',
            'operation': 'rolling_pca_score',
            'dependencies': ['roc_20', 'volatility_20', 'bias_20'],
            'params': {'window': 60, 'components': 1, 'min_samples': 30},
        }
        definitions = {**factor_definitions(), 'rolling_pca_test': spec}
        out = raw_factor('rolling_pca_test', p, definitions)
        self.assertEqual(out.shape, p['close'].shape)
        self.assertEqual(out.index.tolist(), p['close'].index.tolist())
        self.assertEqual(out.columns.tolist(), p['close'].columns.tolist())
        self.assertTrue(np.isfinite(out.iloc[-1].to_numpy()).any())
        future = {k: v.copy() for k, v in p.items()}
        future['close'].iloc[-1] *= 100
        future['open'].iloc[-1] *= 100
        future['high'].iloc[-1] *= 100
        future['low'].iloc[-1] *= 100
        changed = raw_factor('rolling_pca_test', future, definitions)
        pd.testing.assert_frame_equal(out.iloc[:-1], changed.iloc[:-1])

    def test_registered_weighted_rank_composite_without_pca(self):
        from factors.research_library import raw_factor
        p = sample(80)
        spec = {'id': 'weighted_test', 'kind': 'composite',
                'operation': 'weighted_rank_composite',
                'dependencies': ['roc_20', 'volatility_20', 'bias_20'],
                'params': {'weights': [2, 1, 1], 'signs': [1, -1, 1], 'min_features': 2}}
        defs = {**factor_definitions(), 'weighted_test': spec}
        out = raw_factor('weighted_test', p, defs)
        self.assertEqual(out.shape, p['close'].shape)
        self.assertTrue(np.isfinite(out.iloc[-1].to_numpy()).any())

    def test_rsi_sided_flat_wilder_seed_and_cci(self):
        p = sample(60)
        for values, expected in ((np.arange(60)+10,100),(70-np.arange(60),0),(np.full(60,10),50)):
            p['close'] = pd.DataFrame({'x':values},index=p['close'].index)
            r = calculate(dict(operation='rsi',params={'n':14}),p)
            self.assertTrue(r.iloc[:14].isna().all().all())
            self.assertAlmostEqual(r.iloc[14,0],expected)
        p=sample(60);tp=(p['close']+p['high']+p['low'])/3
        expected=(tp.iloc[-1]-tp.iloc[-20:].mean())/(.015*(tp.iloc[-20:]-tp.iloc[-20:].mean()).abs().mean())
        actual=calculate(dict(operation='cci',params={'n':20,'scale':.015}),p).iloc[-1]
        pd.testing.assert_series_equal(actual,expected,check_names=False)

    def test_actual_high_low_breakout_and_missing_inputs(self):
        p=sample(60)
        expected=(p['close']/p['high'].rolling(20).max().shift()-1)*100
        actual=calculate(dict(operation='breakout',params={'n':20}),p)
        pd.testing.assert_frame_equal(actual,expected)
        p['low'].iloc[-1,:]=np.nan
        r=calculate(dict(operation='atr_pct',params={'n':14}),p)
        self.assertTrue(r.iloc[-1].isna().all())
        del p['high']
        with self.assertRaises(KeyError): calculate(dict(operation='cci',params={'n':20,'scale':.015}),p)

    def test_every_technical_indicator_and_shared_numeric_limits(self):
        p=sample()
        for key,spec in factor_definitions().items():
            if spec['kind']=='technical':
                with self.subTest(key=key):
                    rng=np.random.default_rng(len(key))
                    inputs=dict(p,**{f:pd.DataFrame(rng.uniform(.5,2,p['close'].shape),index=p['close'].index,columns=p['close'].columns)
                                     for f in spec.get('requires',[]) if f.startswith(('fund:', 'valuation:'))})
                    result=calculate(spec,inputs)
                    self.assertEqual(result.shape,p['close'].shape)
                    self.assertFalse(np.isinf(result.to_numpy()).any())
                    self.assertTrue(result.iloc[-1].notna().all())
        s=[{'name':'rsi_14','sign':-1,'max':30}]
        score=runner.factor_score(p,s)
        raw=calculate(factor_definitions()['rsi_14'],p)
        self.assertTrue(score.where(raw>30).isna().all().all())
        self.assertEqual(score.iloc[-1].notna().tolist(),raw.iloc[-1].le(30).tolist())

    def test_limits_survive_strategy_persistence_and_reject_inversion(self):
        with tempfile.TemporaryDirectory() as td, patch.object(workspace,'CUSTOM',Path(td)/'custom.yaml'):
            payload=dict(id='range_test',name='RSI',factor_list=[dict(name='rsi_14',min=10,max=30)],revision=workspace.revision())
            workspace.save_strategy(payload)
            self.assertEqual(workspace.custom_strategies()['range_test']['factor_list'][0]['max'],30)
            payload.update(revision=workspace.revision(),factor_list=[dict(name='rsi_14',min=50,max=30)])
            with self.assertRaises(ValueError):workspace.save_strategy(payload)

    def test_vwap_requires_same_day_raw_price_adjustment(self):
        from factors.research_library import attach_extra_panels
        from unittest.mock import Mock
        p=sample(3);p['close'].loc[:,:]=20;p['volume'].loc[:,:]=10;p['amount'].loc[:,:]=640
        cache=Mock()
        cache.get_daily_batch.return_value={c:pd.DataFrame({'date':p['close'].index,'close':40}) for c in p['close'].columns}
        factors=[{'name':'alpha101_041'}]
        attach_extra_panels(p,list(p['close'].columns),p['close'].index[0],p['close'].index[-1],factors,cache)
        self.assertTrue(p['vwap'].eq(32).all().all())
        cache.get_daily_batch.return_value={}
        attach_extra_panels(p,list(p['close'].columns),p['close'].index[0],p['close'].index[-1],factors,cache)
        self.assertTrue(p['vwap'].isna().all().all())


class FinancialTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        patcher=patch.object(finance,'DB',Path(self.temp.name)/'fund.db');patcher.start();self.addCleanup(patcher.stop)

    def row(self,available='2024-04-02',report='2023-12-31',value=10,metric='roe'):
        return dict(code='000001.SZ',metric=metric,value=value,available_date=available,report_date=report)

    def test_unavailable_reports_and_future_revisions_do_not_leak(self):
        finance.store([self.row(),self.row('2024-05-01',value=90),self.row('2024-05-02','2024-03-31',30),self.row('2024-05-03',value=100)])
        index=['2024-04-01','2024-04-02','2024-05-01','2024-05-02','2024-05-03']
        out=finance.panels(index,['000001.SZ'],{'roe'})['roe'].iloc[:,0]
        self.assertTrue(pd.isna(out.iloc[0]));self.assertEqual(out.iloc[1:].tolist(),[10,90,30,30])
        finance.store([self.row(value=999)])
        self.assertEqual(finance.panels(index,['000001.SZ'],{'roe'})['roe'].iloc[1,0],10)

    def test_import_requires_real_dates_and_is_atomic(self):
        payload={'csv':'code,metric,value,available_date,report_date\n000001.SZ,roe,10,2024-04-02,2023-12-31'}
        with self.assertRaises(ValueError):finance.import_csv(payload)
        finance.import_csv({**payload,'confirm_historical_dates':True})
        with self.assertRaises(ValueError):finance.store([self.row(metric='pb'),self.row(metric='bad')])
        self.assertNotIn('pb',finance.coverage())
        with self.assertRaises(ValueError):finance.store([self.row(available='2023-01-01')])

    def test_daily_market_value_never_forward_filled(self):
        finance.store([self.row('2024-04-02','2024-04-02',1e10,'cap')])
        p=finance.panels(['2024-04-02','2024-04-03'],['000001.SZ'],{'cap'})['cap']
        self.assertEqual(p.iloc[0,0],1e10);self.assertTrue(pd.isna(p.iloc[1,0]))

    def test_akshare_old_report_is_only_available_after_fetch_and_a_shares_only(self):
        import sqlite3
        from data import cache
        import akshare
        directory=Path(self.temp.name)
        con=sqlite3.connect(directory/'stock_basic.db')
        con.execute('CREATE TABLE stock_basic(code TEXT)');con.execute("INSERT INTO stock_basic VALUES ('000001.SZ')")
        con.commit();con.close()
        df=pd.DataFrame([{'股票代码':'000001','每股收益':1,'最新公告日期':'2024-04-01'},
                         {'股票代码':'900001','每股收益':99,'最新公告日期':'2024-04-01'}])
        with patch.object(cache,'CACHE_DIR',directory),patch.object(akshare,'stock_yjbb_em',return_value=df):
            info=finance._fetch('20231231')
        self.assertEqual(info['stocks'],1)
        tomorrow=(date.today()+timedelta(days=1)).isoformat()
        self.assertEqual(finance.coverage()['eps']['first_date'],tomorrow)
        self.assertTrue(finance.panels([date.today().isoformat()],['000001.SZ'],{'eps'})['eps'].isna().all().all())


if __name__=='__main__': unittest.main()
