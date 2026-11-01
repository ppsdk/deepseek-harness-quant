"""Signal interpretation, causality, data gates and shared-ledger contracts."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtest import research_dividend_signals as service
from factors.research_library import read_config
from validation import test_dividend_timing as timing_tests


class DividendSignalTests(unittest.TestCase):
    def setUp(self):
        self.cfg = read_config('dividend_signals')
        self.base = read_config(self.cfg['base_config'])
        self.code = self.cfg['default_code']
        self.frame = timing_tests.DividendTimingTests().frame(420)
        # Deterministic noisy paths exercise entries, trend exits and risk costs.
        close = 10*np.exp(np.random.default_rng(42).normal(0, .025, len(self.frame)).cumsum())
        for key, mult in [('open', .998), ('close', 1.), ('high', 1.01), ('low', .99)]:
            self.frame[key] = close*mult
        self.start = self.frame.index[65]

    def run_service(self, end=None, *, frame=None, calendar_missing=False, coverage=False, **query):
        frame = self.frame if frame is None else frame
        end = end or frame.index[-1]
        def config(name):
            return deepcopy(self.cfg if name == 'dividend_signals' else self.base)
        def calendar(start, stop):
            if calendar_missing: return [], '缺少交易日历'
            return pd.bdate_range(start, stop).strftime('%Y-%m-%d').tolist(), 'synthetic verified calendar'
        audit = {self.code: {'hash': 'synthetic', 'price_basis': 'raw', 'end': end}}
        with patch.object(service, 'read_config', side_effect=config), \
             patch.object(service, 'load_data', return_value=({self.code: frame}, audit)), \
             patch.object(service, 'load_cash_actions', return_value=[]), \
             patch.object(service, '_coverage', return_value=dict(complete=coverage, verified_through=end if coverage else None, sources=[])), \
             patch('data.research_quality.closed_day', return_value='2026-11-02'), \
             patch('data.research_quality.local_calendar', side_effect=calendar):
            return service.evaluate({**dict(code=self.code, start=self.start, end=end, history=True), **query})

    def test_prefix_and_future_perturbation_do_not_change_prior_decisions_or_equity(self):
        full = self.run_service()
        cutoff = self.frame.index[260]
        prefix = self.run_service(cutoff)
        a = [r for r in full['history'] if r['date'] <= cutoff]
        self.assertEqual(a, prefix['history'])
        altered = self.frame.copy()
        altered.loc[altered.index > cutoff, ['open', 'high', 'low', 'close']] *= 2
        changed = self.run_service(frame=altered)
        self.assertEqual(a, [r for r in changed['history'] if r['date'] <= cutoff])
        self.assertGreater(len(full['trades']), 0)
        for trade in full['trades']:
            self.assertLess(trade['signal_date'], trade['date'])
        for row in full['history']:
            self.assertAlmostEqual(row['equity'], row['cash'] + row['market_value'] + row['dividend_receivable'])
        json.dumps(full, allow_nan=False)
        self.assertLessEqual(full['stress_metrics']['final_nav'], full['metrics']['final_nav'])

    def test_filtered_profile_and_plain_profile_use_the_shared_runner(self):
        for ident in self.cfg['profiles']:
            with self.subTest(profile=ident):
                result = self.run_service(profile=ident)
                self.assertEqual(result['effective']['profile']['id'], ident)
                self.assertEqual(result['effective']['fill_price_basis'], 'raw')
                self.assertIn('buy_hold_metrics', result)
                self.assertTrue(all(t['signal_date'] < t['date'] for t in result['trades']))
                self.assertEqual(result['history'][-1], result['latest'])
                for key, metric in [('buy_hold', 'buy_hold_metrics'), ('stress', 'stress_metrics')]:
                    comparison = result['comparison_history'][key]
                    self.assertEqual([r['date'] for r in comparison], [r['date'] for r in result['history']])
                    self.assertAlmostEqual(comparison[-1]['equity']/result['effective']['execution']['initial_capital'],
                                           result[metric]['final_nav'])

    def test_stale_unknown_calendar_missing_action_audit_are_never_data_ready(self):
        known = self.run_service(coverage=True)
        self.assertFalse(known['quality']['stale'])
        self.assertTrue(known['quality']['data_ready'])
        stale = self.run_service(end=(pd.Timestamp(self.frame.index[-1]) + pd.offsets.BDay(2)).strftime('%Y-%m-%d'), coverage=True)
        self.assertTrue(stale['quality']['stale'])
        self.assertFalse(stale['quality']['data_ready'])
        unknown = self.run_service(calendar_missing=True, coverage=True)
        self.assertIsNone(unknown['quality']['stale'])
        self.assertFalse(unknown['quality']['data_ready'])
        incomplete = self.run_service()
        self.assertFalse(incomplete['quality']['cash_actions']['complete'])
        self.assertFalse(incomplete['quality']['data_ready'])

    def test_missing_bar_is_reported_without_filling_or_fabricating_a_price(self):
        day = self.frame.index[120]
        result = self.run_service(frame=self.frame.drop(day), coverage=True)
        self.assertIn(day, result['quality']['missing_sessions'])
        self.assertNotIn(day, [r['date'] for r in result['bars']])
        self.assertFalse(result['quality']['data_ready'])

    def test_state_depends_on_actual_ledger_position_and_exit_has_priority(self):
        code, day = self.code, '2026-10-29'
        def panel(value): return pd.DataFrame({code: [value]}, index=[day])
        panels = {'close': panel(9.)}
        indicators = dict(k=panel(30.), d=panel(25.), trend=panel(10.), recent_low=panel(True),
                          high_exit=panel(False), trend_exit=panel(True))
        adapters = {'_entry': panel(1.), '_exit': panel(0.)}
        def describe(held):
            return service.describe_day(code, day, panels, adapters, indicators, {'market_value': held}, self.cfg['states'])
        self.assertEqual(describe(0)['state'], 'buy')
        self.assertEqual(describe(10)['state'], 'weakening')
        adapters['_exit'] = panel(1.)
        self.assertEqual(describe(0)['state'], 'wait')
        self.assertEqual(describe(10)['state'], 'sell')
        self.assertEqual(describe(0)['if_held'], 'sell')
        self.assertIn('ma_trend_exit', describe(10)['reasons'])
        adapters['_entry'] = panel(np.nan)
        self.assertEqual(describe(0)['state'], 'insufficient')

    def test_invalid_inputs_and_unknown_algorithms_do_not_fall_back(self):
        for values in [dict(code='../secret'), dict(profile='not_implemented'),
                       dict(end='2035-01-01'), dict(end='2026-13-01'),
                       dict(history='maybe'), dict(typo='ignored')]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.run_service(**values)
        for key, value in [('kdj_period', True), ('oversold', 90), ('variant', 'future_model')]:
            cfg = deepcopy(self.cfg)
            profile = cfg['profiles'][cfg['default_profile']]
            if key == 'variant': profile[key] = value
            else: profile['settings'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                service._profile(cfg, cfg['default_profile'])

    def test_coverage_requires_explicit_verified_through_and_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = dict(action_coverage_file=str(Path(folder)/'coverage.json'))
            self.assertFalse(service._coverage(cfg, self.code, '2026-10-29')['complete'])
            Path(cfg['action_coverage_file']).write_text(json.dumps({'assets': {self.code: dict(verified_through='2026-10-01', sources=['official'])}}))
            self.assertFalse(service._coverage(cfg, self.code, '2026-10-29')['complete'])
            self.assertTrue(service._coverage(cfg, self.code, '2026-09-01')['complete'])

    def test_normalized_corporate_actions_extend_assets_without_silent_substitution(self):
        from data import research_dividend as inputs
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'512890_cash_actions.json'
            event = dict(id='synthetic', code=self.code, available_date='2020-01-06',
                         record_date='2020-01-07', ex_date='2020-01-08', pay_date='2020-01-09',
                         cash_per_share=.1, split_ratio=1., tax_kind='fund_exempt')
            path.write_text(json.dumps([event]))
            self.assertEqual(inputs.load_cash_actions({'cash_actions_dir': folder}, self.code, 'etf'), [event])
            event['code'] = 'OTHER.SH'
            path.write_text(json.dumps([event]))
            with self.assertRaises(ValueError):
                inputs.load_cash_actions({'cash_actions_dir': folder}, self.code, 'etf')

    def test_zero_volume_last_bar_is_not_ready_and_does_not_invent_a_fill(self):
        frame = self.frame.copy()
        frame.loc[frame.index[-1], 'volume'] = 0.
        result = self.run_service(frame=frame, coverage=True)
        self.assertFalse(result['quality']['last_bar_traded'])
        self.assertFalse(result['quality']['data_ready'])
        self.assertFalse(result['latest']['fills'])

    def test_http_route_delegates_to_the_same_service(self):
        from deck.research_api import get
        expected = dict(ok=True, latest={'state': 'wait'})
        query = dict(code=self.code)
        with patch.object(service, 'evaluate', return_value=expected) as call:
            self.assertEqual(get('/api/studio/signals/dividend', query), expected)
            call.assert_called_once_with(query)


if __name__ == '__main__':
    unittest.main()
