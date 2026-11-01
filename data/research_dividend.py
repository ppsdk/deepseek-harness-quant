"""Shared cached dividend-research inputs and metrics, with no downloads by default.

Research scripts re-export these helpers for compatibility. Services consume the
same raw bars and corporate-action adapters as historical research.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from data.cache import DailyCache
from backtest.bt_report import compute_metrics

ROOT = Path(__file__).resolve().parents[1]


def _date_value(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    value = str(value).replace('T00:00:00.000', '')[:10]
    return value if len(value) == 10 and value[4] == '-' else None


def _number(value, default=0.0):
    try:
        value = float(value)
        return value if np.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def load_cash_actions(cfg, code, group):
    """Load locally acquired, point-in-time company/fund actions.

    Raw prices are deliberately separated from vendor qfq prices.  The source
    files live under data/private and are never committed; this loader only
    turns acquired source rows into the stable CashActions contract.
    """
    root = ROOT / cfg.get('cash_actions_dir', 'data/private/dividend_timing')
    pure = code.split('.')[0]
    # Optional normalized input allows new stocks/funds without another
    # ticker-specific parser. Existing acquired formats remain the fallback.
    normalized = root / f'{pure}_cash_actions.json'
    if normalized.exists():
        events = json.loads(normalized.read_text(encoding='utf-8'))
        if not isinstance(events, list) or any(not isinstance(e, dict) or e.get('code') != code for e in events):
            raise ValueError('标准公司行为必须是同一证券的事件列表：' + str(normalized))
        return sorted(events, key=lambda e: (e['record_date'], e['id']))
    events = []
    if group == 'stock':
        source = root / f'{pure}_dividends_cninfo.json'
        if not source.exists():
            source = root / f'{pure}_dividends_sina.json'
        if not source.exists():
            raise FileNotFoundError(f'缺少公司行为文件：{source}')
        rows = json.loads(source.read_text(encoding='utf-8'))
        for row in rows:
            status = str(row.get('进度', '实施'))
            record = _date_value(row.get('股权登记日'))
            ex = _date_value(row.get('除权日') or row.get('除权除息日'))
            pay = _date_value(row.get('派息日')) or ex
            available = _date_value(row.get('实施方案公告日期') or row.get('公告日期')) or record
            if status not in ('实施', 'nan', 'None') or not (record and ex and pay):
                continue
            cash = _number(row.get('派息比例') if '派息比例' in row else row.get('派息')) / 10.0
            split = 1.0 + (_number(row.get('送股比例')) + _number(row.get('转增比例'))) / 10.0
            events.append(dict(id=f'{code}-div-{record}', code=code,
                               record_date=record, ex_date=ex, pay_date=pay,
                               available_date=available, cash_per_share=cash,
                               split_ratio=split, tax_kind='personal_stock'))
    else:
        if pure == '515450':
            source = root / '515450_events_1.json'
            for row in json.loads(source.read_text(encoding='utf-8')) if source.exists() else []:
                record = _date_value(row.get('权益登记日')); ex = _date_value(row.get('除息日'))
                pay = _date_value(row.get('分红发放日'))
                if not (record and ex and pay):
                    continue
                text_value = str(row.get('每10份分红', ''))
                import re
                matches = re.findall(r'([0-9]+(?:\.[0-9]+)?)', text_value)
                cash = _number(matches[-1] if matches else 0.0) / 10.0
                events.append(dict(id=f'{code}-div-{record}', code=code,
                                   record_date=record, ex_date=ex, pay_date=pay,
                                   available_date=record, cash_per_share=cash,
                                   split_ratio=1.0, tax_kind='fund_exempt'))
        if pure == '512890':
            source = root / '512890_events_2.json'
            rows = json.loads(source.read_text(encoding='utf-8')) if source.exists() else []
            for row in rows:
                ex = _date_value(row.get('拆分折算日'))
                if not ex:
                    continue
                # The fund notice gives the effective split day only.  The
                # prior trading day is a conservative point-in-time record day.
                prior = (pd.Timestamp(ex) - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
                events.append(dict(id=f'{code}-split-{ex}', code=code,
                                   record_date=prior, ex_date=ex, pay_date=ex,
                                   available_date=prior, cash_per_share=0.0,
                                   split_ratio=2.0, tax_kind='fund_exempt'))
    return sorted(events, key=lambda event: (event['record_date'], event['id']))


def load_data(cfg, download=False, price_basis='qfq'):
    from data.fetcher_akshare import fetch_daily
    stock_cache=DailyCache(); etf_cache=DailyCache(ROOT/cfg['etf_database'])
    frames, audit = {}, {}
    for group, spec in cfg['groups'].items():
        cache=etf_cache if group=='etf' else stock_cache
        for code in spec['assets']:
            frame=cache.get_daily(code,start=cfg['data_start'],end=cfg['data_end'],adjust=price_basis)
            if group=='etf' and (frame is None or frame.empty) and download:
                net=cfg['network']
                fetched=fetch_daily(code,cfg['data_start'],cfg['data_end'],price_basis,**net)
                if fetched.empty:
                    raise ValueError('ETF没有行情：'+code)
                cache.put_daily(code,fetched,adjust=price_basis,source='akshare')
                frame=cache.get_daily(code,start=cfg['data_start'],end=cfg['data_end'],adjust=price_basis)
            if (frame is None or frame.empty) and download:
                net=cfg['network']
                fetched=fetch_daily(code,cfg['data_start'],cfg['data_end'],price_basis,**net)
                if fetched.empty:
                    raise ValueError('行情没有数据：'+code)
                cache.put_daily(code,fetched,adjust=price_basis,source='akshare')
                frame=cache.get_daily(code,start=cfg['data_start'],end=cfg['data_end'],adjust=price_basis)
            if frame is None or frame.empty:
                raise ValueError('没有已缓存行情：'+code+'；ETF首次运行可加 --download。')
            frame=frame.sort_values('date').drop_duplicates('date').set_index('date')
            if not frame.index.is_monotonic_increasing:
                raise ValueError('日期不按序。')
            # Preserve missing bars and reject invalid OHLC, never invent fills.
            numeric=frame[['open','high','low','close']]
            invalid=((numeric.le(0).any(axis=1)) | frame['high'].lt(numeric[['open','low','close']].max(axis=1)-1e-6)
                     | frame['low'].gt(numeric[['open','high','close']].min(axis=1)+1e-6))
            if invalid.any():
                raise ValueError('行情OHLC不一致：'+code)
            frames[code]=frame
            audit[code]=dict(start=str(frame.index[0]),end=str(frame.index[-1]),rows=len(frame),
                             missing_prices=int(numeric.isna().any(axis=1).sum()),
                             hash=hashlib.sha256(pd.util.hash_pandas_object(frame,index=True).values.tobytes()).hexdigest(),
                             sources=sorted(frame['source'].dropna().unique()) if 'source' in frame else [],
                             price_basis=('raw_with_explicit_cash_actions' if price_basis == 'none'
                                          else 'vendor_qfq_proxy_not_explicit_dividend_cashflow'))
    return frames,audit


def panels_for(frame, code):
    return {field:pd.DataFrame({code:frame[field] if field in frame else np.nan},index=frame.index)
            for field in ('open','high','low','close','volume','amount','is_st')}


def summarize(sim, initial):
    ret=sim['returns']; nav=sim['nav_series']
    metrics=compute_metrics(ret)
    if not metrics:
        raise ValueError('回测没有有效交易日。')
    metrics['max_drawdown']=float((nav/np.maximum.accumulate(np.r_[1.,nav.to_numpy()])[1:]-1).min())
    metrics['calmar']=metrics['annual_return']/abs(metrics['max_drawdown']) if metrics['max_drawdown'] < 0 else 0.
    metrics['average_exposure']=float(np.mean([r['exposure'] or 0. for r in sim['daily_account']]))
    metrics['trades']=len(sim['trades'])
    metrics['annual_one_way_turnover']=sum(t['amount'] for t in sim['trades'])/initial/2/(len(ret)/242.)
    metrics['yearly_returns']={str(y):float((1+v).prod()-1) for y,v in ret.groupby(ret.index.year)}
    return metrics
