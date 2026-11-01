"""Independent math, causality, dated exposures and executable risk constraints."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from factors.signal_blend import combine, settings
from backtest.research_strategy_risk import compare_records
from backtest.research_diversification import constrain_targets, buy_room, snapshot
from validation.test_research_library import sample


class SignalTests(unittest.TestCase):
    def test_ordinary_and_registered_blends_share_automatic_filter(self):
        from backtest.research_runner import factor_score
        from factors.research_library import raw_factor
        p=sample(80)
        duplicate=p['close'].rank(axis=1,pct=True)
        p['_factor_cache']={'roc_20':duplicate, 'bias_20':duplicate}
        p['_signal_policy']=dict(window=20,min_days=5,min_samples=20)
        out=factor_score(p,[dict(name='roc_20'),dict(name='bias_20')])
        self.assertEqual(p['_signal_correlation_audit'][-1]['changes'][-1]['selected'], ['roc_20'])
        self.assertTrue(out.iloc[-1].notna().all())
        defs={'test_composite':dict(kind='composite',operation='weighted_rank_composite',dependencies=['roc_20','bias_20'])}
        result=raw_factor('test_composite',p,defs)
        pd.testing.assert_frame_equal(result,out)

    def test_correlated_and_opposite_signals_are_removed_causally(self):
        p = sample(80)
        a = p['close'].rank(axis=1, pct=True)
        b = -a
        cfg = dict(window=20, min_days=5, min_samples=20, max_abs_correlation=.8)
        result = combine([a, b], [2, 1], ['a', 'opposite'], p, policy=cfg)
        pd.testing.assert_frame_equal(result.iloc[5:], a.iloc[5:])
        self.assertIn('opposite', p['_signal_correlation_audit'][0]['changes'][-1]['dropped'])
        future = deepcopy(p)
        aa = a.copy(); aa.iloc[50:] *= 100
        changed = combine([aa, b], [2, 1], ['a', 'opposite'], future, policy=cfg)
        pd.testing.assert_frame_equal(result.iloc[:50], changed.iloc[:50])
        prefix = combine([a.iloc[:50], b.iloc[:50]], [2, 1], ['a', 'opposite'], {}, policy=cfg)
        pd.testing.assert_frame_equal(result.iloc[:50], prefix)

    def test_disabled_mode_missingness_and_bound_filter(self):
        from backtest.research_runner import factor_score
        p = sample(80)
        a = p['close'].rank(axis=1, pct=True); b = a.copy(); b.iloc[-1, 0] = np.nan
        out = combine([a, b], [2, 1], ['a', 'b'], {}, policy={'enabled': False})
        pd.testing.assert_frame_equal(out, (a*2+b)/3)
        p['_signal_policy'] = dict(window=20, min_days=5, min_samples=20)
        # Duplicate removal must not delete an explicit screening condition.
        with patch('factors.research_library.raw_factor', return_value=p['close']):
            out = factor_score(p, [dict(name='roc_20'), dict(name='bias_20', max=0)])
        self.assertTrue(out.isna().all().all())

    def test_validation_and_insufficient_history_audit(self):
        for v in ({'enabled': 'yes'}, {'window': 1.5}, {'max_abs_correlation': float('nan')}, {'unknown_policy': 'oops'}):
            with self.assertRaises(ValueError):
                settings('signal', v)
        a = pd.DataFrame([[1., 2.], [1., 2.]])
        p = {}; combine([a, a], [1, 1], ['a', 'b'], p)
        self.assertIsNone(p['_signal_correlation_audit'][0]['latest_pairs'][0]['correlation'])


class ExposureAndExecutionTests(unittest.TestCase):
    def test_caps_affect_actual_fills_and_daily_exposure_report(self):
        from backtest.research_portfolio import simulate
        p=sample(90); day=p['close'].index[70]
        p['_risk_exposures']={'industry':pd.DataFrame('A',index=p['close'].index,columns=p['close'].columns)}
        params=dict(start=day,end=p['close'].index[71],initial_capital=1000.,topn=2,rebalance_days=20,
                    commission_bps=0.,stamp_tax_bps=0.,slippage_bps=0.,min_commission=0.,
                    diversification={'portfolio':dict(enabled=True,window=60,min_observations=30,
                                                      max_abs_correlation=1.,industry_cap=.3)})
        result=simulate(p,p['close'],params)
        self.assertTrue(result['trades'])
        # Reconstruct open NAV after first fill, independently of risk report.
        trade=result['trades'][0]
        self.assertLessEqual(trade['quantity']*trade['open']/1000.,.3+1e-10)
        self.assertIn('exposures',result['daily_account'][0])
        self.assertTrue(result['diversification_targets'])

    def test_industry_history_no_backfill_and_overlap_rejection(self):
        from data.research_exposures import attach
        from factors.research_library import read_config
        cfg = read_config('research_diversification')
        p = sample(5); code = p['close'].columns[0]; days = list(p['close'].index)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'exposure.csv'
            path.write_text('code,dimension,bucket,valid_from,valid_to,available_date\n'
                            f'{code},industry,A,{days[0]},,{days[2]}\n', encoding='utf-8')
            cfg['exposures']['history_file'] = str(path)
            with patch('factors.research_library.read_config', return_value=cfg):
                out = attach(p)['industry']
                self.assertTrue(out.iloc[:2, 0].isna().all())
                self.assertEqual(out.iloc[2, 0], 'A')
                self.assertTrue(out.iloc[:, 1].isna().all())
                with path.open('a', encoding='utf-8') as f:
                    f.write(f'{code},industry,B,{days[0]},,{days[2]}\n')
                with self.assertRaises(ValueError):
                    attach({k: v for k, v in p.items() if not k.startswith('_')})

    def test_target_group_caps_and_missing_labels(self):
        p = sample(80); day = p['close'].index[-1]; a, b, c, _ = p['close'].columns
        labels = pd.DataFrame([['A','A','B',None]]*80, index=p['close'].index, columns=p['close'].columns)
        p['_risk_exposures'] = {'industry': labels}
        p['_portfolio_policy'] = dict(enabled=True, industry_cap=.4, max_abs_correlation=1., missing_policy='allow')
        result = constrain_targets(p, {a: .3, b: .3, c: .3}, day, {})
        self.assertAlmostEqual(result[a]+result[b], .4)
        self.assertAlmostEqual(result[c], .3)
        cfg = settings('portfolio', dict(enabled=True, industry_cap=.4, max_abs_correlation=1.))
        room, reason = buy_room(p, p['close'].columns[3], {}, day, cfg)
        self.assertEqual(room, 0.); self.assertIn('missing', reason)
        summary = snapshot(p, {a:.3,b:.3}, day, cfg)
        self.assertIn('concentration_limit:industry', summary['violations'])

    def test_high_correlation_cannot_enter_and_history_is_trailing(self):
        p = sample(80); a, b, *_ = p['close'].columns
        p['close'][b] = p['close'][a]*2
        day = p['close'].index[65]
        cfg = settings('portfolio', dict(enabled=True, window=60, min_observations=30))
        self.assertEqual(buy_room(p, b, {a:.3}, day, cfg)[1], 'portfolio_correlation_limit')
        p['close'].iloc[66:, 0] *= 100
        p.pop('_risk_pair_cache', None)
        self.assertEqual(buy_room(p, b, {a:.3}, day, cfg)[1], 'portfolio_correlation_limit')

    def test_failed_sell_blocks_new_correlated_buy_and_preserves_t1(self):
        from backtest.research_portfolio import simulate
        p = sample(90); a,b,*_ = p['close'].columns
        for field in ('open','close','high','low'):
            p[field][b] = p[field][a]*2
        score = p['close']*0; score[a]=2.; score[b]=1.
        score.iloc[71:, 0]=0.; score.iloc[71:, 1]=3.
        # Old holding cannot be sold when the new target is due.
        p['volume'].loc[p['close'].index[72:], a]=0.
        params = dict(start=p['close'].index[70], end=p['close'].index[-1], initial_capital=1000., topn=1,
                      rebalance_days=1, min_trade_interval_days=1, commission_bps=0., min_commission=0.,
                      stamp_tax_bps=0., slippage_bps=0., diversification={'portfolio':dict(enabled=True, window=60,min_observations=30)})
        result=simulate(p,score,params)
        self.assertTrue(result['trades'])
        self.assertFalse(any(t['side']=='buy' and t['code']==b for t in result['trades']))
        self.assertTrue(all(t['signal_date']<t['date'] for t in result['trades']))
        self.assertTrue(all(r['equity']>=0 for r in result['daily_account']))


def record(values, codes=None):
    days = pd.bdate_range('2024-01-01', periods=len(values)).strftime('%Y-%m-%d')
    codes = codes or ['x']
    return dict(returns=[dict(date=d,ret=float(v)) for d,v in zip(days,values)],
                signal_candidates=[dict(date=d,codes=codes) for d in days],
                daily_account=[dict(date=d,position_weights={c:.5/len(codes) for c in codes},
                                    exposures={'industry':dict(weights={'A':.5},unknown_weight=0)}) for d in days])


class StrategyRiskTests(unittest.TestCase):
    def test_saved_strategy_preserves_and_validates_diversification(self):
        from backtest import research_workspace as workspace
        from backtest.research_runner import research_config, validate_params
        with tempfile.TemporaryDirectory() as temp, patch.object(workspace,'CUSTOM',Path(temp)/'strategy.yaml'):
            value=dict(id='risk_test',name='risk',factor_list=[dict(name='roc_20')],
                       diversification={'portfolio':{'enabled':True,'industry_cap':.3}},revision=workspace.revision())
            workspace.save_strategy(value)
            cfg=research_config(); params=validate_params({'strategy':'risk_test'},cfg)
            self.assertTrue(params['diversification']['portfolio']['enabled'])
            value.pop('diversification'); value['revision']=workspace.revision()
            workspace.save_strategy(value)
            self.assertEqual(workspace.custom_strategies()['risk_test']['diversification']['portfolio']['industry_cap'],.3)

    def test_return_correlation_and_independent_overlap_math(self):
        a=record([.01,-.02,.03,-.04]); b=record([.02,-.04,.06,-.08],['x','y'])
        result=compare_records({'a':a,'b':b},'2024-12-31',dict(min_observations=2,max_strategy_weight=1.))
        pair=result['pairwise'][0]
        self.assertAlmostEqual(pair['return_correlation'],1.)
        self.assertEqual(pair['signal']['mean_jaccard'],.5)
        self.assertEqual(pair['holdings']['mean_shared_nav_weight'],.25)
        self.assertEqual(len(result['constrained_weights']),1)
        self.assertAlmostEqual(result['cash_weight'],.5)
        json.dumps(result,allow_nan=False)

    def test_constraints_missing_history_and_group_budget(self):
        a=record([.01,-.02,.03,-.04],['x']); b=record([-.02,.01,.02,-.01],['y'])
        cfg=dict(min_observations=2,max_strategy_weight=1.,max_abs_return_correlation=1.,industry_cap=.3)
        out=compare_records({'a':a,'b':b},'2024-12-31',cfg)
        self.assertAlmostEqual(out['combined_exposure_weights']['industry']['A'],.3)
        self.assertAlmostEqual(sum(out['constrained_weights'].values()),.6)
        b.pop('signal_candidates')
        out=compare_records({'a':a,'b':b},'2024-12-31',cfg)
        self.assertEqual(out['pairwise'][0]['signal']['state'],'unavailable')
        self.assertNotIn('b',out['constrained_weights'])

    def test_cutoff_no_future_and_no_missing_return_imputation(self):
        a=record([.01,-.02,.03,-.04]); b=record([-.02,.01,.02,-.01],['y'])
        cfg=dict(min_observations=2,max_abs_return_correlation=1.)
        old=compare_records({'a':a,'b':b},'2024-01-03',cfg)
        b['returns'][-1]['ret']=100.
        new=compare_records({'a':a,'b':b},'2024-01-03',cfg)
        self.assertEqual(old,new)
        b['returns']=b['returns'][1:]
        out=compare_records({'a':a,'b':b},'2024-01-03',cfg)
        self.assertEqual(out['pairwise'][0]['return_days'],2)

    def test_safe_archive_ids_and_duplicate_inputs(self):
        from backtest.research_strategy_risk import compare
        for ids in (['../secret','b'], ['a','a'], 'a,b'):
            with self.assertRaises(ValueError):
                compare({'archive_ids':ids})


if __name__=='__main__':
    unittest.main()
