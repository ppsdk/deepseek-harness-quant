"""Research workflow contracts: persistence, signal cutoff and shared ranking."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtest import research_runner as runner, research_workspace as workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        config = base / 'research.yaml'
        config.with_suffix('.yaml.example').write_text(
            runner.CONFIG.with_suffix('.yaml.example').read_text(encoding='utf-8'), encoding='utf-8')
        for p in (patch.object(runner, 'CONFIG', config),
                  patch.object(workspace, 'CUSTOM', base / 'custom.yaml')):
            p.start()
            self.addCleanup(p.stop)

    def payload(self):
        return {'id': 'test_combo', 'name': 'Research combo', 'desc': 'test',
                'factor_list': [{'name': 'mom_20', 'sign': -1, 'weight': 2}],
                'defaults': {'topn': 2, 'stocks': 3, 'rebalance_days': 5},
                'revision': workspace.revision()}

    def test_saved_strategy_roundtrip_and_edit_revision(self):
        payload = self.payload()
        workspace.save_strategy(payload)
        cfg = workspace.workspace_config()
        self.assertTrue(cfg['strategies']['test_combo']['editable'])
        self.assertFalse(cfg['strategies']['tech3']['editable'])
        p = runner.validate_params({'strategy': 'test_combo'}, cfg)
        self.assertEqual((p['topn'], p['stocks'], p['rebalance_days']), (2, 3, 5))
        before = workspace.CUSTOM.read_bytes()
        with self.assertRaises(workspace.StrategyConflict):
            workspace.save_strategy(payload)
        self.assertEqual(workspace.CUSTOM.read_bytes(), before)
        payload.update(revision=workspace.revision(), name='Edited')
        workspace.save_strategy(payload)
        self.assertEqual(runner.list_strategies()['test_combo']['name'], 'Edited')

    def test_invalid_strategy_never_writes(self):
        bad = [dict(id='../unsafe'), dict(id='tech3'), dict(factor_list=[]),
               dict(factor_list=[{'name': 'os.system', 'sign': 1}]),
               dict(factor_list=[{'name': 'mom_20', 'weight': 0}]),
               dict(factor_list=[{'name': 'mom_20', 'weight': float('nan')}]),
               dict(defaults={'topn': 10, 'stocks': 2}), dict(defaults={'scorer': 'exec'})]
        for override in bad:
            with self.subTest(override=override), self.assertRaises(ValueError):
                workspace.save_strategy({**self.payload(), **override})
        self.assertFalse(workspace.CUSTOM.exists())

    def panels(self):
        days = pd.date_range('2024-01-01', periods=100).strftime('%Y-%m-%d')
        close = pd.DataFrame({'000001.SZ': np.linspace(10, 20, 100),
                              '000002.SZ': np.linspace(20, 10, 100)}, index=days)
        return {'close': close, 'open': close.copy(), 'high': close * 1.01,
                'low': close * .99, 'volume': close * 1000,
                'turn': close * .1, 'is_st': close * np.nan}

    def test_screen_uses_cutoff_and_same_ranker_as_backtest(self):
        panels = self.panels()
        values = {'strategy': 'reversal20', 'end': '2024-03-01', 'topn': 1, 'stocks': 2}
        with patch.object(runner, 'load_panels', return_value=(panels, {})), patch.object(workspace, '_names', return_value={}):
            a = workspace.screen(values)
            panels['close'].loc['2024-03-02':] *= 100
            b = workspace.screen(values)
        self.assertEqual(a['candidates'], b['candidates'])
        self.assertEqual(a['signal_date'], values['end'])
        score = runner.factor_score(panels, runner.list_strategies()['reversal20']['factor_list'])
        expected = runner.rank_candidates(panels, score, values['end'], 1).index[0]
        self.assertEqual(a['candidates'][0]['code'], expected)
        self.assertEqual(expected, '000002.SZ')

    def test_weights_change_rank_and_missing_factors_propagate(self):
        panels = self.panels()
        first = panels['close'].copy()
        first.loc[:] = [1, 2]
        second = first.iloc[:, ::-1].copy()
        second.columns = first.columns
        funcs = {'mom_20': lambda c: first, 'rps_120': lambda c: second}
        with patch.object(runner, 'FACTOR_FUNCS', funcs):
            score = runner.factor_score(panels, [{'name': 'mom_20', 'weight': 3}, {'name': 'rps_120'}])
            self.assertGreater(score.iloc[-1, 1], score.iloc[-1, 0])
            first.iloc[-1, 1] = np.nan
            score = runner.factor_score(panels, [{'name': 'mom_20', 'weight': 3}, {'name': 'rps_120'}])
            self.assertTrue(pd.isna(score.iloc[-1, 1]))

    def test_new_high_needs_history_and_ranker_excludes_st_suspensions(self):
        panels = self.panels()
        self.assertTrue(runner.factor_score(panels, [{'name': 'new_high_250'}]).isna().all().all())
        score = panels['close'].copy()
        day = score.index[-1]
        panels['is_st'].loc[day, '000001.SZ'] = 1
        panels['volume'].loc[day, '000002.SZ'] = 0
        self.assertTrue(runner.rank_candidates(panels, score, day, 2).empty)

    def test_chart_returns_only_cached_rows_and_rejects_bad_code(self):
        with self.assertRaises(ValueError):
            workspace.chart({'code': '../bars.db'})
        df = pd.DataFrame({'date': ['2024-01-02', '2024-01-04'], 'open': [10, 12],
                          'high': [11, 13], 'low': [9, 11], 'close': [10, 12],
                          'volume': [100, 200], 'turn': [np.nan, 1]})
        with patch('data.research_charts.DailyCache') as cache, patch.object(workspace, '_names', return_value={}):
            cache.return_value.get_daily.return_value = df
            result = workspace.chart({'code': '000001.SZ'})
        self.assertEqual([x['date'] for x in result['bars']], df['date'].tolist())
        self.assertIsNone(result['bars'][0]['turn'])


if __name__ == '__main__':
    unittest.main()
