"""Offline checks for AKShare units, nullable ST state, and download auditing."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from data.cache import DailyCache
from data.fetcher_akshare import _no_proxy, normalize_daily
from data.akshare_loader import audit_bars, canonical_code, load_config
from data import akshare_loader


class AksharePipelineTest(unittest.TestCase):
    def sina(self):
        return pd.DataFrame({"date": ["2024-01-03", "2024-01-02"],
                             "open": [10.5, 10.0], "close": [11.0, 10.0],
                             "high": [12.0, 11.0], "low": [10.0, 9.0],
                             "volume": [10000., 20000.], "amount": [110000., 200000.],
                             "turnover": [.01, .02]})

    def normalize(self, frame, backend):
        return normalize_daily(frame, "600519.SH", backend, "2018-01-01", "2024-12-31")

    def test_sina_sorts_before_returns_and_normalizes_percentage_units(self):
        result = self.normalize(self.sina(), "sina")
        self.assertEqual(result["date"].tolist(), ["2024-01-02", "2024-01-03"])
        self.assertAlmostEqual(result.iloc[1]["pct_chg"], 10.)
        self.assertEqual(result["turn"].tolist(), [2., 1.])
        self.assertEqual(result["volume"].tolist(), [20000., 10000.])
        self.assertTrue(result["is_st"].isna().all())

    def test_eastmoney_converts_hands_but_retains_turn_percentage(self):
        frame = self.sina().rename(columns={"date": "日期", "open": "开盘", "close": "收盘",
                    "high": "最高", "low": "最低", "volume": "成交量", "amount": "成交额", "turnover": "换手率"})
        frame["换手率"] = [1., 2.]
        result = self.normalize(frame, "eastmoney")
        self.assertEqual(result["volume"].tolist(), [2000000., 1000000.])
        self.assertEqual(result["turn"].tolist(), [2., 1.])

    def test_current_tencent_already_reports_shares(self):
        result = self.normalize(self.sina(), "tencent")
        self.assertEqual(result["volume"].tolist(), [20000., 10000.])
        self.assertEqual(result["turn"].tolist(), [2., 1.])

    def test_pre2019_turnover_mask_and_missing_amount(self):
        frame = self.sina().drop(columns=["amount", "turnover"])
        frame["date"] = ["2018-12-28", "2018-12-27"]
        result = self.normalize(frame, "sina")
        self.assertTrue(result["turn"].isna().all())
        self.assertEqual(audit_bars(result)["amount_coverage"], 0.)

    def test_unknown_st_roundtrips_as_null_not_false(self):
        result = self.normalize(self.sina(), "sina")
        with tempfile.TemporaryDirectory() as home:
            cache = DailyCache(Path(home) / "bars.db")
            self.assertEqual(cache.put_daily("600519.SH", result, source="akshare"), 2)
            read = cache.get_daily("600519.SH")
            self.assertEqual(read["date"].tolist(), result["date"].tolist())
            self.assertTrue(read["is_st"].isna().all())
            self.assertAlmostEqual(read.iloc[1]["pct_chg"], 10.)
            result["code"] = "600519.SH"
            self.assertEqual(cache.put_daily_batch(result, source="akshare"), 2)
            self.assertTrue(cache.get_daily("600519.SH")["is_st"].isna().all())

    def test_price_audit_rejects_broken_bars(self):
        result = self.normalize(self.sina(), "sina")
        self.assertEqual(audit_bars(result)["rows"], 2)
        for column, value in [("close", -1.), ("high", 1.), ("volume", -100.)]:
            broken = result.copy()
            broken.loc[0, column] = value
            with self.assertRaises(ValueError):
                audit_bars(broken)

    def test_proxy_environment_restored_after_failure(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://local.invalid", "NO_PROXY": "localhost"}):
            original = dict(os.environ)
            with self.assertRaises(RuntimeError):
                with _no_proxy(timeout=2):
                    self.assertNotIn("HTTP_PROXY", os.environ)
                    self.assertEqual(os.environ["NO_PROXY"], "*")
                    raise RuntimeError("fixture")
            self.assertEqual(dict(os.environ), original)

    def test_codes_and_example_fallback(self):
        self.assertEqual(canonical_code("600519"), "600519.SH")
        self.assertEqual(canonical_code("000001"), "000001.SZ")
        self.assertEqual(canonical_code("920001"), "920001.BJ")
        with self.assertRaises(ValueError):
            canonical_code("not-a-code")
        with tempfile.TemporaryDirectory() as home:
            path = Path(home) / "config.yaml"
            Path(str(path) + ".example").write_text("start: '2019-01-01'\n", encoding="utf-8")
            self.assertEqual(load_config(path)["start"], "2019-01-01")

    def test_download_fallback_and_verified_checkpoint_resume(self):
        frame = self.normalize(self.sina(), "sina")
        config = load_config()
        config.update(start="2024-01-01", end="2024-01-31", minimum_rows=60,
                      backends=["eastmoney", "sina"], pause_seconds=0, retries=0)
        universe = pd.DataFrame([{"code": "600519.SH", "name": "fixture"}])
        with tempfile.TemporaryDirectory() as home:
            base = Path(home)
            cache = DailyCache(base / "cache/bars.db")
            with patch.object(akshare_loader, "BASE", base), patch.object(akshare_loader, "CACHE_DIR", base / "cache"), \
                 patch.object(akshare_loader, "STATUS", base / "status.json"), \
                 patch.object(akshare_loader, "DailyCache", return_value=cache), \
                 patch.object(akshare_loader, "load_universe", return_value=universe):
                with patch.object(akshare_loader, "fetch_daily", side_effect=[ConnectionError("fixture unavailable"), frame]) as fetch:
                    first = akshare_loader.run(config)
                    self.assertEqual(fetch.call_count, 2)
                self.assertEqual(first["successful"], 1)
                self.assertEqual(first["stocks"][0]["backend"], "sina")
                self.assertEqual(len(first["stocks"][0]["errors"]), 1)
                self.assertFalse(first["stocks"][0]["backtest_eligible_by_length"])
                with patch.object(akshare_loader, "fetch_daily", side_effect=AssertionError("unexpected network")) as fetch:
                    second = akshare_loader.run(config)
                    fetch.assert_not_called()
                self.assertTrue(second["stocks"][0]["resumed"])

    def test_download_lock_prevents_duplicate_writer_and_releases(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.object(akshare_loader, "STATUS", Path(home) / "status.json"):
                with akshare_loader.download_lock():
                    with self.assertRaises(OSError):
                        with akshare_loader.download_lock():
                            self.fail("duplicate writer was admitted")
                with akshare_loader.download_lock():
                    pass

    def test_checkpoint_retries_transient_windows_read_lock(self):
        real_replace=os.replace
        with tempfile.TemporaryDirectory() as home:
            path=Path(home)/'checkpoint.json'
            with patch.object(akshare_loader.os,'replace',side_effect=[PermissionError('busy'),None]) as replace,patch.object(akshare_loader.time,'sleep'):
                akshare_loader.save_json(path,{'safe':True})
                self.assertEqual(replace.call_count,2)
                temp=replace.call_args.args[0]
            real_replace(temp,path)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')),{'safe':True})

    def test_failure_isolated_and_recorded_without_fake_bars(self):
        frame = self.normalize(self.sina(), "sina")
        config = load_config()
        config.update(start="2024-01-01", end="2024-01-31", minimum_rows=2,
                      backends=["sina"], pause_seconds=0, retries=0)
        universe = pd.DataFrame([{"code": "000001.SZ", "name": "fixture-fail"},
                                {"code": "600519.SH", "name": "fixture-pass"}])
        with tempfile.TemporaryDirectory() as home:
            base = Path(home)
            cache = DailyCache(base / "cache/bars.db")
            with patch.object(akshare_loader, "BASE", base), patch.object(akshare_loader, "CACHE_DIR", base / "cache"), \
                 patch.object(akshare_loader, "STATUS", base / "status.json"), \
                 patch.object(akshare_loader, "DailyCache", return_value=cache), \
                 patch.object(akshare_loader, "load_universe", return_value=universe), \
                 patch.object(akshare_loader, "fetch_daily", side_effect=[ConnectionError("fixture unavailable"), frame]):
                report = akshare_loader.run(config)
            self.assertEqual(report["state"], "partial")
            self.assertEqual(report["successful"], 1)
            self.assertEqual(report["failed"], 1)
            self.assertIsNone(cache.get_daily("000001.SZ"))


if __name__ == "__main__":
    unittest.main()
