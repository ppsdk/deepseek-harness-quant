import re
import unittest
from pathlib import Path


class ExplorationReportTests(unittest.TestCase):
    def test_report_has_thresholds_and_five_stable_candidates(self):
        text = (Path(__file__).resolve().parents[1] / 'docs' / '因子与策略探索报告_20261029.md').read_text(encoding='utf-8')
        self.assertIn('绝对平均 Rank IC ≥ 0.05', text)
        self.assertIn('压力成本', text)
        self.assertEqual(text.count('| 通过 |'), 5)
        self.assertGreaterEqual(text.count('`'), 20)


if __name__ == '__main__':
    unittest.main()
