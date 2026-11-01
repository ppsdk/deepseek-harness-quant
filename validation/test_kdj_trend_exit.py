"""Independent trend exits, confirmation windows and causal signal contracts."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtest.research_kdj_trend_exit import signals, validate, weakening_exit
from factors.research_library import read_config
from data.research_dividend import panels_for
from factors.research_indicators import calculate
from validation import test_dividend_timing as timing_tests


class KDJTrendExitTests(unittest.TestCase):
    def setUp(self):
        self.settings = deepcopy(read_config('dividend_signals')['profiles']['kdj_21_5_dmi_ma60']['settings'])
        self.frame = timing_tests.DividendTimingTests().frame(240)
        values = np.r_[np.linspace(10, 15, 110), np.linspace(15, 8, 130)]
        for key, mult in [('open', .998), ('close', 1.), ('high', 1.01), ('low', .99)]:
            self.frame[key] = values * mult
        self.panels = panels_for(self.frame, 'TEST')

    def test_exit_is_independent_of_kdj_values_missingness_and_parameters(self):
        profiles = read_config('dividend_signals')['profiles']
        for ident in ('kdj_21_5_dmi', 'kdj_21_5_ma60', 'kdj_21_5_dmi_ma60', 'kdj_21_5_dmi_weakening'):
            st = profiles[ident]['settings']
            normal, _ = signals(self.panels, st)
            def altered(spec, panels):
                if spec['operation'].startswith('kdj_'):
                    return panels['close'] * np.nan
                return calculate(spec, panels)
            with patch('factors.research_indicators.calculate', side_effect=altered):
                changed, _ = signals(self.panels, {**st, 'kdj_period': 50, 'kdj_smooth': 9, 'oversold': 5})
            pd.testing.assert_frame_equal(normal['_exit'], changed['_exit'])
            if st['exit_mode'] != 'dmi_weakening':
                self.assertTrue(normal['_exit'].eq(1).any().any())
            self.assertTrue(changed['_entry'].isna().all().all())

    def test_dmi_requires_consecutive_days_and_unknown_breaks_confirmation(self):
        st = {**self.settings, 'exit_mode': 'dmi'}
        pdi = self.panels['close'] * 0 + 20
        mdi = pdi * 0 + 10
        mdi.iloc[80:84] = 30
        pdi.iloc[82] = np.nan
        def indicator(spec, panels):
            return {'dmi_plus': pdi, 'dmi_minus': mdi}.get(spec['operation'], calculate(spec, panels))
        with patch('factors.research_indicators.calculate', side_effect=indicator):
            adapters, _ = signals(self.panels, st)
        out = adapters['_exit'].iloc[:, 0]
        self.assertEqual(out.iloc[80], 0)
        self.assertEqual(out.iloc[81], 1)
        self.assertTrue(pd.isna(out.iloc[82]))
        self.assertEqual(out.iloc[83], 0)
        self.assertEqual(out.iloc[84], 0)

    def test_ma_and_combined_exit_match_declared_conditions(self):
        dmi, diag = signals(self.panels, {**self.settings, 'exit_mode': 'dmi'})
        ma, _ = signals(self.panels, {**self.settings, 'exit_mode': 'ma'})
        combo, _ = signals(self.panels, self.settings)
        average = diag['exit_ma']
        below = self.panels['close'].lt(average)
        expected = below.astype(float).rolling(self.settings['exit_confirm']).sum().eq(self.settings['exit_confirm']) & average.lt(average.shift(self.settings['exit_slope_lookback']))
        valid = ma['_exit'].notna()
        pd.testing.assert_frame_equal(ma['_exit'].where(valid), expected.astype(float).where(valid))
        valid = combo['_exit'].notna()
        pd.testing.assert_frame_equal(combo['_exit'], (dmi['_exit'].eq(1) & ma['_exit'].eq(1)).astype(float).where(valid))

    def test_entry_keeps_low_reversal_and_exit_wins_conflicts(self):
        st = {**self.settings, 'exit_mode': 'ma', 'exit_require_falling': False}
        k, d = self.panels['close']*0+10, self.panels['close']*0+15
        k.iloc[81:] = 25
        def indicator(spec, panels):
            return {'kdj_k': k, 'kdj_d': d}.get(spec['operation'], calculate(spec, panels))
        with patch('factors.research_indicators.calculate', side_effect=indicator):
            adapters, _ = signals(self.panels, st)
        self.assertEqual(adapters['_entry'].iloc[80, 0], 0)
        self.assertEqual(adapters['_entry'].iloc[81, 0], 1)
        self.assertEqual(adapters['_entry'].iloc[82, 0], 1)
        self.assertFalse((adapters['_entry'].eq(1) & adapters['_exit'].eq(1)).any().any())

    def test_prefix_invariance_for_all_modes_and_invalid_parameters(self):
        cutoff = self.frame.index[170]
        for mode in ('dmi', 'ma', 'dmi_and_ma'):
            st = {**self.settings, 'exit_mode': mode}
            full, _ = signals(self.panels, st)
            part, _ = signals({key: value.loc[:cutoff] for key, value in self.panels.items()}, st)
            for key in full:
                pd.testing.assert_frame_equal(full[key].loc[:cutoff], part[key])
        for key, value in [('exit_mode', 'or_typo'), ('overbought', 80), ('dmi_period', True),
                           ('exit_require_falling', 1), ('oversold', float('nan'))]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate({**self.settings, key: value})

    def peak_fixture(self):
        st = deepcopy(read_config('dividend_signals')['profiles']['kdj_21_5_dmi_weakening']['settings'])
        st['dmi_peak_lookback'] = 3
        def frame(values): return pd.DataFrame({'TEST': values})
        return st, frame([38,40,42,41,38,34,30,25]), frame([10,9,8,9,12,14,18,20]), frame([30,32,34,36,35,33,30,27])

    def test_peak_exit_precedes_bearish_di_cross_and_is_not_backdated(self):
        st, pdi, mdi, adx = self.peak_fixture()
        leave, known, warning, armed = weakening_exit(pdi, mdi, adx, st)
        self.assertFalse(leave.iloc[4, 0])
        self.assertTrue(leave.iloc[5, 0])
        self.assertGreater(pdi.iloc[5, 0], mdi.iloc[5, 0])
        self.assertTrue(armed.iloc[5, 0])
        self.assertTrue(warning.iloc[4, 0])
        self.assertTrue(known.iloc[5, 0])
        for cutoff in (4, 5, 6):
            prefix = weakening_exit(pdi.iloc[:cutoff], mdi.iloc[:cutoff], adx.iloc[:cutoff], st)
            for full, part in zip((leave, known, warning, armed), prefix):
                pd.testing.assert_frame_equal(full.iloc[:cutoff], part)

    def test_peak_exit_requires_bullish_context_and_all_three_turns(self):
        st, pdi, mdi, adx = self.peak_fixture()
        for altered in [(pdi*0+5, mdi, adx), (pdi, mdi, adx*0+30),
                        (pdi, mdi*0+10, adx), (pdi*0+40, mdi, adx)]:
            leave, _, _, _ = weakening_exit(*altered, st)
            self.assertFalse(leave.any().any())

    def test_peak_exit_missing_inputs_break_confirmation_and_reject_invalid_parameters(self):
        st, pdi, mdi, adx = self.peak_fixture()
        adx.iloc[4] = np.nan
        leave, known, _, _ = weakening_exit(pdi, mdi, adx, st)
        self.assertFalse(known.iloc[4, 0])
        self.assertFalse(leave.iloc[5, 0])
        for key, value in [('dmi_peak_confirm', 0), ('dmi_peak_lookback', True),
                           ('dmi_peak_adx_min', np.nan), ('dmi_peak_pdi_min', -1)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate({**st, key: value})


if __name__ == '__main__':
    unittest.main()
