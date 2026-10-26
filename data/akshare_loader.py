"""Download AKShare A-share daily bars into DailyCache, with restartable audit records.

python -m data.akshare_loader --limit 20
python -m data.akshare_loader --all --start 2019-01-01
python -m data.akshare_loader --codes 600519.SH,000001.SZ --start 2021-01-01
python -m data.akshare_loader --status
"""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

from .cache import CACHE_DIR, DailyCache
from .fetcher_akshare import _no_proxy, fetch_daily

BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / "config/akshare.yaml"
STATUS = BASE / "output/akshare_download.json"


@contextmanager
def download_lock():
    """One writer per workspace; the OS releases the lock even after a crash."""
    filename = STATUS.with_suffix(".lock")
    filename.parent.mkdir(parents=True, exist_ok=True)
    with filename.open("a+b") as handle:
        if filename.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def save_json(file, payload):
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    temp = file.with_name(file.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    # Windows readers and virus scanners can briefly deny replacing an open file.
    # Keep the old checkpoint intact and retry the same fully written snapshot.
    for attempt in range(12):
        try:
            os.replace(temp,file)
            break
        except PermissionError:
            if attempt==11:raise
            time.sleep(.05*(attempt+1))


def read_status(summary=False):
    if not STATUS.exists():
        return {"state": "not_started", "message": "尚未运行 AKShare 下载", "stocks": []}
    report = json.loads(STATUS.read_text(encoding="utf-8"))
    if summary:
        report["stocks"] = report.get("stocks", [])[-100:]
        request = report.get("request", {})
        if "codes" in request:
            request["code_count"] = len(request.pop("codes"))
    return report


def load_config(path=CONFIG):
    path = Path(path)
    source = path if path.exists() else Path(str(path) + ".example")
    if not source.exists():
        raise ValueError(f"下载配置缺失: {path} 或对应 .example")
    config = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise ValueError("AKShare 配置必须是 mapping")
    return config


def canonical_code(code):
    code = str(code).strip().upper()
    if "." in code:
        symbol, market = code.split(".", 1)
        if len(symbol) != 6 or not symbol.isdigit() or market not in ("SH", "SZ", "BJ"):
            raise ValueError(f"非法股票代码: {code}")
        return code
    code = code.zfill(6)
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"非法股票代码: {code}")
    if code.startswith("6"):
        return code + ".SH"
    if code.startswith(("0", "3")):
        return code + ".SZ"
    if code.startswith(("4", "8", "9")):
        return code + ".BJ"
    raise ValueError(f"无法识别 A 股市场: {code}")


def load_universe(config):
    import akshare as ak
    kind = config["universe"]
    if kind == "explicit":
        return pd.DataFrame([{"code": canonical_code(c), "name": ""} for c in config.get("codes", [])])
    with _no_proxy(timeout=config["request_timeout"]):
        if kind == "all_a":
            rows = ak.stock_info_a_code_name()[["code", "name"]].copy()
        elif kind == "csi300":
            rows = ak.index_stock_cons_csindex(symbol="000300").rename(
                columns={"成分券代码": "code", "成分券名称": "name"})[["code", "name"]].copy()
        else:
            raise ValueError(f"未知股票池: {kind}")
    rows["code"] = rows["code"].map(canonical_code)
    return rows.drop_duplicates("code").sort_values("code").reset_index(drop=True)


def persist_universe(rows, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows.to_csv(cache_dir / "akshare_universe_latest.csv", index=False, encoding="utf-8-sig")
    with closing(sqlite3.connect(cache_dir / "stock_basic.db")) as con, con:
        con.execute("CREATE TABLE IF NOT EXISTS stock_basic (code TEXT PRIMARY KEY, name TEXT, industry TEXT, ipo_date TEXT, out_date TEXT, status TEXT)")
        # A current name is not historical ST history; retain existing IPO/industry data.
        con.executemany("INSERT INTO stock_basic (code,name,status) VALUES (?,?,'1') "
                        "ON CONFLICT(code) DO UPDATE SET name=CASE WHEN excluded.name!='' THEN excluded.name ELSE stock_basic.name END,status='1'",
                        list(rows[["code", "name"]].itertuples(index=False, name=None)))


def audit_bars(df):
    if df.empty:
        raise ValueError("返回空数据")
    prices = df[["open", "high", "low", "close"]]
    if not np.isfinite(prices.to_numpy()).all():
        raise ValueError("OHLC 含缺失或非有限数值")
    if (prices <= 0).any().any():
        raise ValueError("OHLC 非正（可能是前复权负价）；不能直接用于当前收益计算")
    if (df["high"] + 1e-6 < prices.max(axis=1)).any() or (df["low"] - 1e-6 > prices.min(axis=1)).any():
        raise ValueError("OHLC 高低价关系不一致")
    if df["date"].duplicated().any() or not df["date"].is_monotonic_increasing:
        raise ValueError("日期重复或未递增")
    if (df["volume"].dropna() < 0).any() or (df["amount"].dropna() < 0).any():
        raise ValueError("成交量/成交额为负")
    fingerprint=df.reindex(columns=['date','open','high','low','close','volume','amount','turn']).copy()
    for field in fingerprint.columns[1:]:fingerprint[field]=pd.to_numeric(fingerprint[field],errors='coerce').astype(float)
    digest=hashlib.sha256(pd.util.hash_pandas_object(fingerprint,index=False).values.tobytes()).hexdigest()
    return {"rows": len(df), "start": str(df["date"].min()), "end": str(df["date"].max()),"bar_hash":digest,
            "turn_coverage": float(df["turn"].notna().mean()),
            "amount_coverage": float(df["amount"].notna().mean()),
            "historical_st_coverage": float(df["is_st"].notna().mean()),
            "suspended_rows": int((df["volume"] == 0).sum())}


def run(config):
    import akshare
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    start = pd.Timestamp(config["start"]).strftime("%Y-%m-%d")
    # Exclude an in-progress current trading day. Weekends are trimmed by the provider.
    cutoff = now.date() if now.hour >= 16 else now.date() - timedelta(days=1)
    end = min(pd.Timestamp(config["end"]).date(), cutoff) if config.get("end") else cutoff
    end = end.isoformat()
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    adjust = config["adjust"]
    backends = config["backends"]
    if adjust not in ("qfq", "hfq", "none") or not backends or set(backends) - {"eastmoney", "sina", "tencent"}:
        raise ValueError("复权方式或数据通道配置无效")
    if int(config.get("limit", 0)) < 0 or float(config["request_timeout"]) <= 0 or int(config["retries"]) < 0 or float(config["pause_seconds"]) < 0:
        raise ValueError("limit/retries/pause 不可为负，request_timeout 必须为正")
    save_json(STATUS, {"state": "running", "stage": "loading_universe", "pid": os.getpid(), "started_at": now.isoformat(),
                       "request": {"start": start, "end": end, "adjust": adjust}, "stocks": []})
    rows = load_universe(config)
    if rows.empty:
        raise ValueError("股票池为空")
    universe_count = len(rows)
    persist_universe(rows, CACHE_DIR)
    limit = int(config.get("limit", 0))
    if 0 < limit < len(rows):
        rows = rows.iloc[np.linspace(0, len(rows) - 1, limit).round().astype(int)]
    identity = {"codes": rows["code"].tolist(), "start": start, "end": end,
                "adjust": adjust, "backends": backends, "akshare_version": akshare.__version__}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    checkpoint = CACHE_DIR / f"akshare_checkpoint_{fingerprint}.json"
    old = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
    # A larger universe may reuse the previously verified sample when the exact
    # date window, adjustment, endpoint order, and library version match.
    if config.get("resume") and not config.get("refresh"):
        shared = {k: v for k, v in identity.items() if k != "codes"}
        for archive in sorted((BASE / "output").glob("akshare_download_*.json"), reverse=True):
            try:
                previous = json.loads(archive.read_text(encoding="utf-8"))
                previous_request = {k: v for k, v in previous.get("request", {}).items() if k != "codes"}
                if previous_request == shared:
                    for item in previous.get("stocks", []):
                        if item.get("state") == "ok":
                            old.setdefault(item["code"], item)
            except (OSError, ValueError, KeyError):
                continue
    cache = DailyCache()
    report = {"state": "running", "pid": os.getpid(), "started_at": now.isoformat(), "request": identity,
              "cache_dir": str(CACHE_DIR.resolve()), "universe": config["universe"],
              "universe_size": universe_count, "requested_stocks": len(rows), "completed": 0,
              "successful": 0, "failed": 0, "total_rows": 0, "resumed": 0,
              "stocks": [], "limitations": [
                  "当前股票名单回溯历史，未覆盖全部退市股票，存在幸存者偏差。",
                  "历史 ST 标记未知；不能根据当前简称推断历史 ST 状态。",
                  "前复权历史可能随除权除息变化；更新时重新拉取整个请求窗口，不拼接不同复权快照。",
                  "2019 年以前换手字段不用于因子结论；缺失换手率不填造数。",
                  "非交易日/缺失行情不向前填充；停牌与缺失需要回测执行层处理。",
              ]}
    save_json(STATUS, report)
    backend_failures = {backend: 0 for backend in backends}
    backend_until = {backend: 0.0 for backend in backends}
    for row in rows.itertuples(index=False):
        code = row.code
        item = {"code": code, "name": row.name, "state": "failed", "errors": []}
        try:
            cached = None
            prior = old.get(code, {})
            if config.get("resume") and not config.get("refresh") and prior.get("state") == "ok":
                cached = cache.get_daily(code, start=start, end=end, adjust=adjust)
                if cached is None or len(cached) != prior.get("rows") or str(cached["date"].min()) != prior.get("start") or str(cached["date"].max()) != prior.get("end"):
                    cached = None
                if cached is not None:
                    quality=audit_bars(cached)
                    if quality['bar_hash']!=prior.get('bar_hash'):cached=None
            if cached is not None:
                item = {**prior, "resumed": True}
            else:
                for backend in backends:
                    if time.monotonic() < backend_until[backend]:
                        item["errors"].append({"backend": backend, "error": "连续失败冷却中，本股未请求该通道"})
                        continue
                    try:
                        df = fetch_daily(code, start=start, end=end, adjust=adjust, backend=backend,
                                         timeout=float(config["request_timeout"]), retries=int(config["retries"]))
                        quality = audit_bars(df)
                        written = cache.put_daily(code, df, adjust=adjust, source="akshare")
                        actual = cache.get_daily(code, start=start, end=end, adjust=adjust)
                        if actual is None or not set(df["date"]).issubset(set(actual["date"])):
                            raise RuntimeError("写入后读回行数不一致")
                        item.update(quality, state="ok", backend=backend, resumed=False)
                        backend_failures[backend] = 0
                        break
                    except Exception as error:
                        item["errors"].append({"backend": backend, "error": str(error)[:350]})
                        backend_failures[backend] += 1
                        if backend_failures[backend] >= int(config.get("backend_failure_threshold", 3)):
                            backend_until[backend] = time.monotonic() + float(config.get("backend_cooldown_seconds", 120))
                            backend_failures[backend] = 0
        except Exception as error:
            item["errors"].append({"error": str(error)[:350]})
        if item["state"] == "ok":
            item["backtest_eligible_by_length"] = item["rows"] >= int(config["minimum_rows"])
            if not item["backtest_eligible_by_length"]:
                item["warning"] = "已保留真实短历史，尚不满足配置的回测样本长度"
        old[code] = item
        save_json(checkpoint, old)
        report["stocks"].append(item)
        report["completed"] += 1
        report["successful"] = sum(r["state"] == "ok" for r in report["stocks"])
        report["failed"] = report["completed"] - report["successful"]
        report["total_rows"] += int(item.get("rows", 0)) if item["state"] == "ok" else 0
        report["resumed"] += int(bool(item.get("resumed")))
        report["updated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        save_json(STATUS, report)
        print(f"[{report['completed']}/{len(rows)}] {code} {item['state']} rows={item.get('rows', 0)} backend={item.get('backend', '-')} errors={len(item.get('errors', []))}", flush=True)
        if not item.get("resumed"):
            time.sleep(float(config["pause_seconds"]))
    report["state"] = "completed" if not report["failed"] else "partial" if report["successful"] else "failed"
    report["finished_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    save_json(STATUS, report)
    save_json(BASE / "output" / f"akshare_download_{now:%Y%m%d_%H%M%S}.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--codes", help="comma separated canonical A-share codes")
    parser.add_argument("--universe", choices=["all_a", "csi300", "explicit"])
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--verify-gaps", action="store_true",help="下载后按交易日和字段检查缺口，再尝试补齐")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.status:
        print(json.dumps(read_status(summary=True), ensure_ascii=False, indent=2))
        return 0
    config = load_config(args.config)
    for key in ("start", "end", "limit", "universe"):
        if getattr(args, key) is not None:
            config[key] = getattr(args, key)
    if args.codes:
        config.update(universe="explicit", codes=args.codes.split(","))
    if args.all:
        config["limit"] = 0
    if args.refresh:
        config["refresh"] = True
    try:
        with download_lock():
            report = run(config)
            if args.verify_gaps:
                from data.research_quality import repair
                report.update(state="running",stage="filling_gaps")
                save_json(STATUS,report)
                from data.research_services import update_listing_dates
                report['listing_dates']=update_listing_dates()
                quality=repair(dict(start=report['request']['start'],end=report['request']['end'],codes=report['request']['codes']),lock_held=True)
                report['gap_check']={k:quality[k] for k in ('summary','repair','generated_at','calendar_source','calendar_days')}
                report['state']='completed' if not report['failed'] else 'partial'
                report['stage']='completed'
                if quality['summary']['affected'] or not quality['calendar_days'] or any(d['unverified_old_dates'] for d in quality['repair']['details']):
                    report['state']='partial'
                save_json(STATUS,report)
    except (BlockingIOError, PermissionError):
        print("已有下载任务运行，或文件不可写。未修改现有任务状态。")
        return 2
    except KeyboardInterrupt:
        report = read_status()
        report.update(state="interrupted", error="用户中断；可使用同一配置续传")
        save_json(STATUS, report)
        return 130
    except Exception as error:
        report = read_status()
        report.update(state="failed", error=str(error)[:500])
        save_json(STATUS, report)
        print(f"下载失败: {error}")
        return 1
    print(f"state={report['state']} successful={report['successful']} failed={report['failed']} -> {STATUS}")
    return 0 if report["state"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
