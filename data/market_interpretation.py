"""Conditional forward outcomes, fixed score bins, and dated dividend archives.

These are descriptive observations, not a strategy backtest. Signals are known
at the close; returns start at the next actual opening and end h sessions later.
"""
from datetime import date
from io import StringIO
import threading

import numpy as np
import pandas as pd

_archive_lock = threading.Lock()


def rank_history(series, window, minimum):
    valid = series.dropna()
    def rank(a):
        return 100 * (np.count_nonzero(a < a[-1]) + .5 * np.count_nonzero(a == a[-1])) / len(a)
    return valid.rolling(window, min_periods=minimum).apply(rank, raw=True).reindex(series.index)


def price_history(frame, spec, annual_days):
    if frame is None or frame.empty:
        return pd.DataFrame()
    close = frame.sort_values('date').set_index('date')['close']
    vol = close.pct_change(fill_method=None).rolling(spec['volatility_days']).std(ddof=1) * np.sqrt(annual_days)
    dd = close / close.rolling(spec['drawdown_days']).max() - 1
    return pd.DataFrame({'close': close, 'volatility': vol, 'drawdown': dd})


def pressure_history(frames, cfg):
    spec = cfg['temperature']; lines = []
    for key in cfg['sectors']:
        f = frames.get('sector:' + key)
        p = price_history(f, spec, spec['trading_days_per_year'])
        if p.empty:
            continue
        m = p['close'].pct_change(spec['momentum_days'], fill_method=None)
        raw = dict(momentum=m, drawdown=p['drawdown'], volatility=-p['volatility'])
        ranked = pd.DataFrame({k: rank_history(v, spec['history_window'], spec['min_observations']) for k, v in raw.items()})
        weighted = ranked.mul(pd.Series(spec['weights'])).sum(axis=1, min_count=len(spec['weights']))
        lines.append(weighted.rename(key))
    if not lines:
        return pd.Series(dtype=float)
    all_sectors = pd.concat(lines, axis=1)
    score = 100 - all_sectors.mean(axis=1)
    return score.where(all_sectors.notna().sum(axis=1) >= len(cfg['sectors']) * spec['min_sector_coverage'])


def archive_dividends(records):
    """A fetched historical row is only proven available from today's retrieval.

    Retain old versions; never turn a newly downloaded month into years of
    supposedly known dividend yields.
    """
    from data import market_dashboard as market
    with _archive_lock:
        archive = market._read('dividend_history.json', [])
        for row in records:
            if not any(x['date'] == row['date'] and x['value'] == row['value'] for x in archive):
                archive.append(dict(row, available_date=date.today().isoformat(), source='本机首次取得'))
        market._write('dividend_history.json', archive)
    return archive


def import_dividends(payload):
    from data import market_dashboard as market
    if not isinstance(payload, dict) or payload.get('confirmed_dates') is not True:
        raise ValueError('请先确认历史可用日期是真实的。')
    text = payload.get('csv'); spec = market.settings()['interpretation']
    if not isinstance(text, str) or len(text.encode('utf-8')) > spec['import_max_bytes']:
        raise ValueError('CSV内容为空或超过大小限制。')
    try:
        frame = pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)
    except Exception:
        raise ValueError('无法读取CSV，请使用UTF-8编码。') from None
    if not {'date', 'dividend_yield', 'available_date'}.issubset(frame.columns) or not 0 < len(frame) <= spec['import_max_rows']:
        raise ValueError('CSV需要date、dividend_yield、available_date三列，且行数在限制内。')
    rows = []
    for i, row in frame.iterrows():
        try:
            day = date.fromisoformat(row['date']); available = date.fromisoformat(row['available_date']); value = float(row['dividend_yield'])
            if available < day or available > date.today() or not np.isfinite(value) or not 0 <= value <= 100:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError(f'第{i + 2}行的日期或股息率无效；股息率5表示5%，可用日期不能早于数据日期或晚于今天。') from None
        rows.append(dict(date=day.isoformat(), available_date=available.isoformat(), value=value, source='用户确认历史可用日期'))
    with _archive_lock:
        archive = market._read('dividend_history.json', []); merged = {(x['date'], x['available_date']): x for x in archive}
        for row in rows:
            key = (row['date'], row['available_date'])
            if key in merged and merged[key]['value'] != row['value']:
                raise ValueError('同一数据日期和可用日期存在不同股息率，请核对后再导入。')
            merged[key] = row
        market._write('dividend_history.json', sorted(merged.values(), key=lambda x: (x['available_date'], x['date'])))
    market.refresh('history_import')
    return dict(ok=True, count=len(rows), message=f'已导入{len(rows)}条历史股息率；正在重新计算历史区间统计。')


def dividend_history(frame, financial, archive, cfg):
    spec = cfg['dividend_score']; annual = cfg['temperature']['trading_days_per_year']
    p = price_history(frame, spec, annual)
    if p.empty or not archive or not financial.get('bond'):
        return pd.Series(dtype=float)
    observations = pd.DataFrame(archive).sort_values(['available_date', 'date'])
    bonds = pd.DataFrame(financial['bond']).sort_values('date')
    # A bond date has no intraday publication timestamp: wait one calendar day.
    bonds['known'] = pd.to_datetime(bonds['date']) + pd.Timedelta(days=1)
    yield_values = []
    # A later revision of an old row must not replace a more recent observation.
    for day in p.index:
        known = observations[(observations['available_date'] <= day) & (observations['date'] <= day)]
        if known.empty:
            yield_values.append(np.nan); continue
        row = known.sort_values(['date', 'available_date']).iloc[-1]
        age = (pd.Timestamp(day) - pd.Timestamp(row['date'])).days
        yield_values.append(float(row['value']) if age <= spec['alignment_days'] else np.nan)
    left = pd.DataFrame({'known': pd.to_datetime(p.index), 'yield': yield_values, 'date': p.index})
    right = bonds.rename(columns={'value': 'bond'})[['known', 'bond']]
    joined = pd.merge_asof(left, right, on='known', direction='backward', tolerance=pd.Timedelta(days=spec['alignment_days'])).set_index('date')
    gap = joined['yield'] - joined['bond']
    a, b = spec['yield_gap_range_pp']; da, db = spec['drawdown_range']
    gap_score = ((gap - a) / (b - a) * 100).clip(0, 100)
    dd_score = ((-p['drawdown'] - da) / (db - da) * 100).clip(0, 100)
    vol_score = rank_history(-p['volatility'], spec['history_window'], spec['min_observations'])
    parts = pd.DataFrame(dict(yield_gap=gap_score, drawdown=dd_score, volatility=vol_score))
    return parts.mul(pd.Series(spec['weights'])).sum(axis=1, min_count=len(spec['weights']))


def future_outcomes(scores, prices, horizon):
    """No fill: both opens and every in-window low must actually be present."""
    columns = ['date', 'score', 'entry', 'exit', 'entry_index', 'exit_index', 'return', 'adverse']
    if scores.empty or prices is None or prices.empty:
        return pd.DataFrame(columns=columns)
    p = prices.sort_values('date').reset_index(drop=True)
    days = p['date'].to_numpy(); rows = []
    for day, score in scores.dropna().sort_index().items():
        entry = int(np.searchsorted(days, day, side='right')); exit_at = entry + horizon
        if exit_at >= len(p):
            continue
        op = p['open'].iloc[entry]; end = p['open'].iloc[exit_at]; lows = p['low'].iloc[entry:exit_at]
        if not np.isfinite(op) or not np.isfinite(end) or op <= 0 or end <= 0 or not np.isfinite(lows).all() or (lows <= 0).any():
            continue
        rows.append(dict(date=day, score=float(score), entry=days[entry], exit=days[exit_at], entry_index=entry, exit_index=exit_at,
                         **{'return': float(end / op - 1)}, adverse=float(min(lows.min(), end) / op - 1)))
    return pd.DataFrame(rows, columns=columns)


def spaced(frame):
    keep = []; last_exit = -1
    for i, row in frame.sort_values('date').iterrows():
        if row['entry_index'] >= last_exit:
            keep.append(i); last_exit = row['exit_index']
    return frame.loc[keep]


def statistics(frame, threshold):
    sample = spaced(frame); n = len(sample)
    if not n:
        return dict(n=0, days=len(frame), mean=None, median=None, big_up=None, big_down=None, path_down=None, up_count=0, down_count=0)
    up = int((sample['return'] >= threshold).sum()); down = int((sample['return'] <= -threshold).sum())
    return dict(n=n, days=len(frame), mean=float(sample['return'].mean()), median=float(sample['return'].median()),
                big_up=up / n, big_down=down / n, path_down=float((sample['adverse'] <= -threshold).mean()), up_count=up, down_count=down)


def in_bin(values, low, high):
    return (True if low is None else values >= low) & (True if high is None else values < high)


def bin_label(low, high, unit):
    if low is None:
        return f'低于{high:g}{unit}'
    if high is None:
        return f'{low:g}{unit}及以上'
    return f'{low:g}至低于{high:g}{unit}'


def build(frames, financial, current, cfg):
    spec = cfg['interpretation']; archive = archive_dividends(financial.get('dividend', []))
    scores = {'pressure': pressure_history(frames, cfg),
              'dividend': dividend_history(frames.get(cfg['dividend_score']['index']), financial, archive, cfg)}
    for key, item in cfg['fear_indices'].items():
        f = frames.get('fear:' + key)
        scores[key] = f.set_index('date')['close'] if f is not None and not f.empty else pd.Series(dtype=float)
    today_values = {'pressure': current['pressure'].get('score'), 'dividend': current['dividend'].get('score')}
    today_values.update({f['id']: None if f['stale'] else f['value'] for f in current['fear']})
    items = []
    for key, meta in spec['metrics'].items():
        history = scores.get(key, pd.Series(dtype=float)).dropna().sort_index()
        if len(history):
            history = history[history.index >= cfg['refresh']['history_start']]
        cutoff = str(history.index[min(len(history) - 1, int(len(history) * (1 - spec['validation_fraction'])))]) if len(history) else None
        value = today_values.get(key); current_bin = None
        bins = []
        for i, (low, high) in enumerate(zip(meta['bins'][:-1], meta['bins'][1:])):
            if value is not None and (low is None or value >= low) and (high is None or value < high):
                current_bin = i
            bins.append(dict(id=i, low=low, high=high, label=bin_label(low, high, meta['unit'])))
        outcome_sets = {}
        for h, rule in spec['horizons'].items():
            outcomes = future_outcomes(history, frames.get(meta['target']), int(h))
            recent = outcomes[outcomes['date'] >= cutoff] if cutoff else outcomes
            base = statistics(outcomes, rule['large_move']); validation_base = statistics(recent, rule['large_move']); groups = []
            for b in bins:
                part = outcomes[in_bin(outcomes['score'], b['low'], b['high'])]
                tail = recent[in_bin(recent['score'], b['low'], b['high'])]
                full_stats = statistics(part, rule['large_move']); tail_stats = statistics(tail, rule['large_move'])
                enough = full_stats['n'] >= spec['min_samples'] and tail_stats['n'] >= spec['min_validation_samples'] and validation_base['n'] >= spec['min_validation_samples']
                verdict = '次数不足，暂不判断'
                if enough:
                    up = full_stats['big_up'] - base['big_up'] >= spec['minimum_difference'] and tail_stats['big_up'] - validation_base['big_up'] >= spec['minimum_difference']
                    down = full_stats['big_down'] - base['big_down'] >= spec['minimum_difference'] and tail_stats['big_down'] - validation_base['big_down'] >= spec['minimum_difference']
                    verdict = '历史上双向大波动偏多' if up and down else '历史上大涨偏多' if up else '历史上大跌偏多' if down else '未见稳定的涨跌优势'
                groups.append(dict(**b, full=full_stats, recent=tail_stats, enough=enough, verdict=verdict, current=b['id'] == current_bin))
            outcome_sets[h] = dict(baseline=base, recent_baseline=validation_base, bins=groups, large_move=rule['large_move'],
                                   observed_from=str(outcomes['date'].min()) if len(outcomes) else None,
                                   observed_to=str(outcomes['date'].max()) if len(outcomes) else None)
        items.append(dict(id=key, name=meta['name'], unit=meta['unit'], target_name=cfg['indices'][meta['target']]['name'],
                          description=meta['description'], value=value, current_bin=current_bin,
                          history_days=len(history), history_from=str(history.index[0]) if len(history) else None,
                          validation_from=cutoff, outcomes=outcome_sets,
                          data_note=('红利评分只使用本机已留存或导入、且在当时已经可用的股息率。历史不足时不借用今天的数值补算。' if key == 'dividend' else '历史行情可能被数据商修订；统计描述当前缓存中的历史关系。')))
    return dict(items=items, horizons=spec['horizons'], default_horizon=spec['default_horizon'], min_samples=spec['min_samples'],
                min_validation_samples=spec['min_validation_samples'], validation_fraction=spec['validation_fraction'],
                method='分数按当时及之前的数据计算；从下一实际交易日开盘观察到第N个交易日后的开盘。大涨/大跌指期末涨跌达到所选门槛，期间下探指相对进入价曾跌到门槛。每个分组内跳过重叠的持有区间，次数不等于信号天数。',
                caution='区间是预先配置的，没有按收益挑选最佳分界。所有区间为各自市场的基准，最近30%日期再单独检查。未扣费用、不含分红，属于历史条件统计；“偏多”只表示前后两段比例均高于基准，不是显著性检验或未来涨跌概率。')
