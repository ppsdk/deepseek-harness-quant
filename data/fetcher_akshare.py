# -*- coding: utf-8 -*-
"""AkShare 覆盖源 fetcher（M1 数据管道 · 覆盖主力/备源）

分工（主文档 4.5②）：
- 日线**备源**（Baostock 失败时兜底）：新浪 `stock_zh_a_daily`（快/稳）
- 财报快照：同花顺 `stock_financial_abstract_ths`（净利润/增速 → 与 finance_calc 反向校验）
- 业绩预告/快报（PEAD 提前量）：东财 `stock_yjyg_em`
- 机构调研（I 因子）：东财 `stock_jgdy_tj_em`

实测结论（2026-08-06）：
1. **东财行情域名 push2his.eastmoney.com 按 TLS 指纹封锁 python 客户端**（requests/http.client 直连
   均 RemoteDisconnected，curl 可通）→ `stock_zh_a_hist` 弃用，日线备源改用新浪/腾讯（CS-21 东财限流实证）
2. 东财 datacenter-web.eastmoney.com（业绩预告/机构调研）python 可通 ✅
3. 本机系统代理（127.0.0.1:7890）对 python 连接不稳定 → 本模块所有请求**绕过系统代理直连**
   （国内数据源本就不需要代理；用 `_no_proxy` 上下文临时剥离代理环境变量）

新浪日线列名：date, open, high, low, close, volume, amount, outstanding_share, turnover
→ 自算 preclose/pct_chg（百分数）；turn 统一为百分数，历史 is_st 保留未知。
批量下载由 data.akshare_loader 根据配置选择东财/新浪/腾讯，并记录实际成功来源。
"""
import os
import time
import threading
from contextlib import contextmanager
from unittest.mock import patch

import pandas as pd

from .cache import DailyCache

# 统一代码 '600519.SH' → 新浪 'sh600519'；北交所 'bj430047'（baostock 不支持 → 本模块兜底）
_MKT_PREFIX = {"SH": "sh", "SZ": "sz", "BJ": "bj"}

_NUM_COLS = ["open", "high", "low", "close", "preclose",
             "volume", "amount", "turn", "pct_chg"]

# 新浪/同花顺接口偶发风控 → 重试指数退避
_RETRY_SLEEP = (1.5, 3.0)
_PROXY_LOCK = threading.RLock()


@contextmanager
def _no_proxy(timeout=None):
    """临时剥离系统代理环境变量（调用国内数据源用直连，规避本机代理不稳）"""
    # Requests also reads Windows registry proxies; NO_PROXY is needed in addition
    # to removing upper/lower-case environment proxy variables. Keep this scoped.
    with _PROXY_LOCK:
        keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "all_proxy", "no_proxy")
        saved = {k: os.environ.get(k) for k in keys}
        for k in keys:
            os.environ.pop(k, None)
        os.environ["NO_PROXY"] = "*"
        try:
            if timeout is None:
                yield
            else:
                import requests
                original = requests.sessions.Session.request
                def bounded_request(session, method, url, **kwargs):
                    if kwargs.get("timeout") is None:
                        kwargs["timeout"] = timeout
                    return original(session, method, url, **kwargs)
                with patch.object(requests.sessions.Session, "request", bounded_request):
                    yield
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def to_ak_symbol(code):
    """统一代码 '600519.SH' → 新浪格式 'sh600519'（支持 SH/SZ/BJ）"""
    try:
        sym, mkt = str(code).strip().upper().split(".")
    except ValueError:
        raise ValueError(f"代码格式应为 '600519.SH'，收到: {code!r}")
    prefix = _MKT_PREFIX.get(mkt)
    if prefix is None:
        raise ValueError(f"AkShare 不支持市场 {mkt}: {code}")
    return f"{prefix}{sym}"


# ---------------- 日线（备源）----------------

def normalize_daily(df, code, backend, start, end):
    """Normalize documented AKShare units to the cache contract.

    Prices keep the requested adjustment; volume=shares, amount=CNY,
    turn/pct_chg=percentage points. Historical ST status stays unknown.
    """
    cols = ["date", "code", "open", "high", "low", "close", "preclose",
            "volume", "amount", "turn", "pct_chg", "is_st"]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    df = df.rename(columns={"日期": "date", "开盘": "open", "最高": "high",
                            "最低": "low", "收盘": "close", "成交量": "volume",
                            "成交额": "amount", "换手率": "turn", "turnover": "turn"}).copy()
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        raise ValueError(f"{backend} missing fields: {sorted(required - set(df.columns))}")
    df["date"] = pd.to_datetime(df["date"], errors="raise").dt.strftime("%Y-%m-%d")
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume", "amount", "turn"]:
        df[col] = pd.to_numeric(df[col], errors="coerce") if col in df else float("nan")
    if backend == "eastmoney":
        df["volume"] *= 100.0  # hand -> shares; Sina already reports shares
    if backend in ("sina", "tencent"):
        df["turn"] *= 100.0  # ratio -> percent, matching Baostock/cache
    df.loc[df["date"] < "2019-01-01", "turn"] = float("nan")
    df["code"] = code.upper()
    df["preclose"] = df["close"].shift(1)
    df["pct_chg"] = (df["close"] / df["preclose"] - 1.0) * 100.0
    df["is_st"] = None  # neither current names nor these endpoints prove historical ST
    return df.loc[df["date"].between(start, end), cols].reset_index(drop=True)


def fetch_daily(code, start="2010-01-01", end=None, adjust="qfq", retries=2,
                backend="sina", timeout=20):
    """直接网络拉取单只日线（新浪源，不读写缓存）。失败重试 retries 次。

    返回标准列 DataFrame：date,code,open,high,low,close,preclose,volume,
    amount,turn,pct_chg,is_st（已排序，可空 df）。
    注：preclose/pct_chg 根据有序复权价格自算；历史 is_st 未知。
    """
    import akshare as ak

    if adjust not in ("qfq", "hfq", "none"):
        raise ValueError("adjust must be qfq, hfq, or none")
    if backend not in ("sina", "eastmoney", "tencent"):
        raise ValueError("unknown AKShare daily backend: " + backend)
    start = pd.Timestamp(start).strftime("%Y-%m-%d")
    if end is None:
        end = time.strftime("%Y-%m-%d")
    end = pd.Timestamp(end).strftime("%Y-%m-%d")
    if start > end:
        raise ValueError("start must not be after end")
    symbol = to_ak_symbol(code)
    start_yyyymmdd = start.replace("-", "")
    end_yyyymmdd = end.replace("-", "")

    last_err = None
    for i in range(retries + 1):
        try:
            with _no_proxy(timeout=timeout):
                kw = dict(start_date=start_yyyymmdd, end_date=end_yyyymmdd,
                          adjust="" if adjust == "none" else adjust)
                if backend == "eastmoney":
                    df = ak.stock_zh_a_hist(symbol=code.split(".")[0], period="daily", timeout=timeout, **kw)
                elif backend == "tencent":
                    df = ak.stock_zh_a_hist_tx(symbol=symbol, timeout=timeout, **kw)
                else:
                    df = ak.stock_zh_a_daily(symbol=symbol, **kw)
            result = normalize_daily(df, code, backend, start, end)
            result.attrs["backend"] = backend
            return result
        except Exception as e:
            last_err = e
            if i < retries:
                time.sleep(_RETRY_SLEEP[min(i, len(_RETRY_SLEEP) - 1)])
    raise ConnectionError(f"AkShare({backend}) 拉取失败（重试 {retries} 次后）: {code} {last_err}")


def ensure_daily(code, start="2010-01-01", end=None, adjust="qfq",
                 cache=None, retries=2):
    """缓存优先的唯一取数入口（与 baostock.ensure_daily 同语义）。

    缓存已覆盖直接读；缺失则从新浪拉取补齐写缓存；返回 [start, end] 过滤后的标准日线。
    """
    cache = cache or DailyCache()
    if end is None:
        end = time.strftime("%Y-%m-%d")

    if cache.covers(code, start, end, adjust):
        return cache.get_daily(code, start=start, end=end, adjust=adjust)

    df = fetch_daily(code, start, end, adjust, retries=retries)
    if df is not None and not df.empty:
        cache.put_daily(code, df, adjust=adjust, source="akshare")

    full = cache.get_daily(code, adjust=adjust)
    if full is None or full.empty:
        return df if df is not None else pd.DataFrame()
    return full[(full["date"] >= start) & (full["date"] <= end)].reset_index(drop=True)


# ---------------- 财报快照（同花顺）----------------

def fetch_financial_abstract(code, indicator="按报告期", retries=2):
    """同花顺财务摘要（报告期维度）：净利润/增速/营收/ROE 等，供 finance_calc 反向校验。

    返回原始 df（列：报告期/净利润/净利润同比增长率/扣非净利润/营业总收入/...）。
    """
    import akshare as ak

    symbol = str(code).strip().split(".")[0]  # 同花顺仅需纯代码
    last_err = None
    for i in range(retries + 1):
        try:
            with _no_proxy():
                return ak.stock_financial_abstract_ths(symbol=symbol, indicator=indicator)
        except Exception as e:
            last_err = e
            if i < retries:
                time.sleep(_RETRY_SLEEP[min(i, len(_RETRY_SLEEP) - 1)])
    raise ConnectionError(f"AkShare(同花顺财务) 拉取失败: {code} {last_err}")


# ---------------- 业绩预告 / 机构调研（东财 datacenter）----------------

def fetch_yjyg(report_date, retries=2):
    """东财业绩预告（按报告期，如 '20251231'）：全市场预告清单 → PEAD 提前量。

    列：序号/股票代码/股票简称/预测指标/业绩变动/预测数值/业绩变动幅度/
        业绩变动原因/预告类型/上年同期值/公告日期/报告日期
    """
    import akshare as ak

    last_err = None
    for i in range(retries + 1):
        try:
            with _no_proxy():
                return ak.stock_yjyg_em(date=report_date)
        except Exception as e:
            last_err = e
            if i < retries:
                time.sleep(_RETRY_SLEEP[min(i, len(_RETRY_SLEEP) - 1)])
    raise ConnectionError(f"AkShare(业绩预告) 拉取失败: {report_date} {last_err}")


def fetch_jgdy(date, retries=2):
    """东财机构调研统计（按日期）：机构调研明细 → I 因子（近 60 日调研次数）。

    列：序号/代码/名称/最新价/涨跌幅/接待机构数量/接待方式/接待人员/接待地点/接待日期
    """
    import akshare as ak

    last_err = None
    for i in range(retries + 1):
        try:
            with _no_proxy():
                return ak.stock_jgdy_tj_em(date=date)
        except Exception as e:
            last_err = e
            if i < retries:
                time.sleep(_RETRY_SLEEP[min(i, len(_RETRY_SLEEP) - 1)])
    raise ConnectionError(f"AkShare(机构调研) 拉取失败: {date} {last_err}")
