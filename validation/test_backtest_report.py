"""Report accounting and historical compatibility, using synthetic ledgers."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backtest.research_report import build_report
from factors.research_manager import experiment_detail


class BacktestReportTest(unittest.TestCase):
    def test_named_benchmark_updates_labels_only_on_its_own_report(self):
        value=dict(dates=['2024-01-02'],nav=[1.],metrics=dict(annual_excess_pp=20.),
                   benchmark=dict(series_labels=dict(bench_nav='合成指数基准')))
        report=build_report(value)
        self.assertEqual(report['settings']['series']['bench_nav']['label'],'合成指数基准')
        self.assertIn('annual_excess_pp',report['settings']['metrics'])
        plain=build_report(dict(dates=['2024-01-02'],nav=[1.]))
        self.assertNotEqual(plain['settings']['series']['bench_nav']['label'],'合成指数基准')
        self.assertNotIn('annual_excess_pp',plain['settings']['metrics'])

    def test_month_and_year_compound_instead_of_sum(self):
        report = build_report(dict(dates=['2024-01-02', '2024-01-31', '2024-02-01', '2025-01-02'],
                                   nav=[.9, .99, 1.089, 1.1979]))
        self.assertAlmostEqual(report['months'][0]['months']['01'], -.01)
        self.assertAlmostEqual(report['months'][0]['months']['02'], .1)
        self.assertAlmostEqual(report['years'][0]['ret'], .089)
        self.assertAlmostEqual(report['years'][1]['ret'], .1)
        self.assertEqual(sum(h['count'] for h in report['histogram']), 4)

    def test_drawdown_includes_initial_capital_and_recovery(self):
        report = build_report(dict(dates=['2024-01-02', '2024-01-03', '2024-01-04'], nav=[.8, .9, 1.]))
        self.assertAlmostEqual(report['drawdown'][0], -.2)
        dd = report['max_drawdown']
        self.assertIsNone(dd['peak_date'])
        self.assertEqual(dd['trough_date'], '2024-01-02')
        self.assertEqual(dd['recovery_date'], '2024-01-04')

    def test_unrecovered_new_worst_resets_recovery(self):
        report = build_report(dict(dates=['2024-01-02', '2024-01-03', '2024-01-04'], nav=[.9, 1., .7]))
        self.assertIsNone(report['max_drawdown']['recovery_date'])
        self.assertEqual(report['max_drawdown']['peak_date'], '2024-01-03')

    def test_cash_fees_exclude_slippage_already_in_fill(self):
        report = build_report(dict(dates=[], nav=[], costs=dict(commission=10, stamp_tax=4,
                                                               slippage_cost=5, total=19)))
        self.assertEqual(report['cash_fees'], 14)
        self.assertEqual(sum(x['value'] for x in report['costs']), 19)

    def test_constant_returns_and_old_archive_missing_account(self):
        report = build_report(dict(dates=['2024-01-02', '2024-01-03'], nav=[1., 1.]))
        self.assertEqual(report['histogram'], [dict(low=0., high=0., count=2)])
        self.assertFalse(report['account_available'])
        self.assertEqual(report['account'], [])

    def test_invalid_dates_or_values_never_draw_a_misleading_line(self):
        for dates, nav in [(['2024-01-03', '2024-01-02'], [1., 2.]),
                           (['2024-01-02'] * 2, [1., 2.]), (['2024-01-02'], []),
                           (['2024-01-02'], [float('nan')]), (['2024-01-02'], [float('inf')])]:
            with self.subTest(dates=dates, nav=nav), self.assertRaises(ValueError):
                build_report(dict(dates=dates, nav=nav))

    def test_rejection_totals_and_new_snapshots(self):
        account = [dict(date='2024-01-02', cash=50, market_value=50, equity=100, exposure=.5)]
        report = build_report(dict(dates=['2024-01-02'], nav=[1.], daily_account=account,
                                   rejections=[dict(reason='one_price_bar')] * 3))
        self.assertEqual(report['rejections'][0]['count'], 3)
        self.assertEqual(report['account'], account)
        self.assertTrue(report['account_available'])

    def test_opening_old_record_builds_report_without_rewriting_archive(self):
        key = 'research_example_' + 'a' * 32
        raw = json.dumps(dict(dates=['2024-01-02'], nav=[.9]))
        with tempfile.TemporaryDirectory() as directory, patch('backtest.research_runner.ARCHIVES', Path(directory)):
            path = Path(directory) / (key + '.json')
            path.write_text(raw, encoding='utf-8')
            result = experiment_detail(key)
            self.assertIn('report', result)
            self.assertEqual(path.read_text(encoding='utf-8'), raw)


if __name__ == '__main__':
    unittest.main()
