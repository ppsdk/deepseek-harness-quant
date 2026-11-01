"""Delayed bullish entry and failure exits without future/pivot knowledge."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtest.research_kdj_regime import signals, validate
from factors.research_indicators import calculate
from factors.research_library import read_config
from data.research_dividend import panels_for
from validation import test_dividend_timing as timing_tests


class KDJRegimeTests(unittest.TestCase):
    def setUp(self):
        self.settings = deepcopy(read_config('dividend_signals')['profiles']['kdj_21_5_regime']['settings'])
        frame = timing_tests.DividendTimingTests().frame(240)
        close = np.r_[np.linspace(10, 15, 110), np.linspace(15, 8, 130)]
        for key, mult in [('open', .998), ('close', 1.), ('high', 1.01), ('low', .99)]:
            frame[key] = close*mult
        self.panels = panels_for(frame, 'TEST')

    def test_kdj_setup_waits_for_sustained_bullish_direction(self):
        pdi = self.panels['close']*0+10
        mdi = pdi*0+20
        pdi.iloc[80:82] = 30  # A two-day bounce must not confirm a new trend.
        pdi.iloc[90:96] = 30
        k, d = pdi*0+10, pdi*0+15
        k.iloc[79:] = 25
        def indicator(spec, panels):
            return {'kdj_k': k, 'kdj_d': d, 'dmi_plus': pdi, 'dmi_minus': mdi,
                    'adx': pdi*0+10}.get(spec['operation'], calculate(spec, panels))
        with patch('factors.research_indicators.calculate', side_effect=indicator):
            adapters, diag = signals(self.panels, self.settings)
        self.assertEqual(adapters['_entry'].iloc[81, 0], 0)
        self.assertEqual(adapters['_entry'].iloc[91, 0], 0)
        self.assertEqual(adapters['_entry'].iloc[92, 0], 1)
        self.assertTrue(diag['recent_low'].iloc[92, 0])
        self.assertTrue(diag['entry_trend_confirmation'].iloc[92, 0])

    def test_weak_trend_exits_without_a_prior_strong_bullish_adx_peak(self):
        pdi = self.panels['close']*0+30
        mdi = pdi*0+10
        pdi.iloc[130:] = 10
        mdi.iloc[130:] = 30
        def indicator(spec, panels):
            return {'dmi_plus': pdi, 'dmi_minus': mdi, 'adx': pdi*0+10}.get(spec['operation'], calculate(spec, panels))
        with patch('factors.research_indicators.calculate', side_effect=indicator):
            adapters, diag = signals(self.panels, self.settings)
        self.assertTrue(diag['weak_rebound_exit'].iloc[150, 0])
        self.assertEqual(adapters['_exit'].iloc[150, 0], 1)
        self.assertFalse(diag['dmi_peak_exit'].any().any())
        self.assertFalse((adapters['_entry'].eq(1) & adapters['_exit'].eq(1)).any().any())
        self.assertFalse(diag['weak_rebound_exit'].iloc[130, 0])

    def test_kdj_cannot_change_exits_and_future_prices_cannot_change_the_past(self):
        original, _ = signals(self.panels, self.settings)
        def unknown_kdj(spec, panels):
            return panels['close']*np.nan if spec['operation'].startswith('kdj_') else calculate(spec, panels)
        with patch('factors.research_indicators.calculate', side_effect=unknown_kdj):
            unknown, _ = signals(self.panels, self.settings)
        pd.testing.assert_frame_equal(original['_exit'], unknown['_exit'])
        self.assertTrue(original['_exit'].eq(1).any().any())
        cutoff = self.panels['close'].index[170]
        prefix = {key: value.loc[:cutoff] for key, value in self.panels.items()}
        partial, _ = signals(prefix, self.settings)
        changed = {key: value.copy() for key, value in self.panels.items()}
        for key in ('open', 'high', 'low', 'close'):
            changed[key].loc[changed[key].index > cutoff] *= 3
        perturbed, _ = signals(changed, self.settings)
        for key in original:
            pd.testing.assert_frame_equal(original[key].loc[:cutoff], partial[key])
            pd.testing.assert_frame_equal(original[key].loc[:cutoff], perturbed[key].loc[:cutoff])

    def test_invalid_configuration_does_not_silently_fall_back(self):
        for key, value in [('entry_dmi_confirm', True), ('weak_exit_period', 0),
                           ('exit_mode', 'dmi'), ('unknown_gate', 1)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate({**self.settings, key: value})


if __name__ == '__main__':
    unittest.main()
