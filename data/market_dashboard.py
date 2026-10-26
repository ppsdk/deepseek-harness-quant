"""Workbench market data: dated observations, transparent scores, startup refresh.

Quotes use DailyCache in a separate local database so startup requests do not
compete with the full A-share downloader. No network request runs in GET handlers.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from io import BytesIO, StringIO
import json
from pathlib import Path
import subprocess
import sys
import threading
from uuid import uuid4

import numpy as np
import pandas as pd

from data.cache import DailyCache, normalize_units
from factors.research_library import read_config

ROOT = Path(__file__).resolve().parent.parent / 'output' / 'market_dashboard'
_lock = threading.Lock()
_state = dict(state='idle', message='尚未更新', completed=0, total=0)


def settings():
    cfg = read_config('market_dashboard')
    for section in ('temperature', 'dividend_score'):
        weights = cfg[section]['weights']
        if any(not np.isfinite(v) or v < 0 for v in weights.values()) or not np.isclose(sum(weights.values()), 1):
            raise ValueError(section + '的分项权重应为非负数且合计为1。')
    return cfg


def _read(name, default):
    try:
        return json.loads((ROOT / name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _write(name, data):
    ROOT.mkdir(parents=True, exist_ok=True)
    temp = ROOT / (name + '.' + uuid4().hex + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temp.replace(ROOT / name)


def cache():
    return DailyCache(str(ROOT / 'prices.db'))


def _request(url, cfg, **kwargs):
    import requests
    # No mutation of proxy environment or global requests methods in a web worker.
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(url, timeout=cfg['refresh']['timeout_seconds'], **kwargs)
        response.raise_for_status()
        return response


def normalize_quotes(frame):
    frame = frame.copy()
    frame['date'] = pd.to_datetime(frame['date'].astype(str), errors='coerce', format='mixed').dt.strftime('%Y-%m-%d')
    for field in ['open', 'high', 'low', 'close', 'volume', 'amount']:
        frame[field] = pd.to_numeric(frame[field], errors='coerce') if field in frame else np.nan
    frame = frame[frame['date'].notna() & (frame['date'] <= date.today().isoformat())]
    frame = frame.dropna(subset=['open', 'high', 'low', 'close'])
    frame = frame[(frame[['open', 'high', 'low', 'close']] > 0).all(axis=1)]
    frame = frame.sort_values('date').drop_duplicates('date', keep='last').reset_index(drop=True)
    if frame.empty:
        raise ValueError('数据源没有返回有效日线。')
    return frame


def decode_sina(payload, timeout):
    process = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).with_name('market_decode.py'))],
                             input=json.dumps(dict(payload=payload)), capture_output=True,
                             text=True, encoding='utf-8', timeout=timeout,
                             creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    if process.returncode:
        raise ValueError('美股行情解码进程失败，保留已有缓存。')
    return json.loads(process.stdout)


def fetch_quotes(item, cfg):
    provider = item['provider']; source = cfg['sources'][provider]
    if provider == 'csindex':
        response = _request(source['url'], cfg, params=dict(indexCode=item['symbol'],
                            startDate=cfg['refresh']['history_start'].replace('-', ''), endDate=date.today().strftime('%Y%m%d')))
        frame = pd.DataFrame(response.json().get('data') or []).rename(columns={
            'tradeDate': 'date', 'tradingVol': 'volume', 'tradingValue': 'amount'})
        # The raw unit is deliberately retained; do not mix this with stock shares.
    elif provider == 'sina_us':
        response = _request(source['url'].format(symbol=item['symbol']), cfg)
        payload = response.text.split('=', 1)[1].split(';', 1)[0].strip().strip('"')
        frame = pd.DataFrame(decode_sina(payload, cfg['refresh'].get('decode_timeout_seconds', 30)))
    elif provider == 'cboe':
        response = _request(source['url'], cfg)
        frame = pd.read_csv(StringIO(response.text)).rename(columns=str.lower)
    elif provider == 'optbbs':
        response = _request(source['url'], cfg)
        frame = pd.read_csv(BytesIO(response.content), encoding='gbk').iloc[:, item['columns']]
        frame.columns = ['date', 'open', 'high', 'low', 'close']
    else:
        raise ValueError('没有配置这种行情数据源。')
    return normalize_quotes(frame)


def fetch_financial(key, cfg):
    source = cfg['sources'][key]
    if key == 'dividend':
        symbol = cfg['indices'][cfg['dividend_score']['index']]['symbol']
        response = _request(source['url'].format(symbol=symbol), cfg)
        raw = pd.read_excel(BytesIO(response.content))
        frame = pd.DataFrame({'date': pd.to_datetime(raw.iloc[:, source['date_column']].astype(str), format='%Y%m%d', errors='coerce'),
                              'value': pd.to_numeric(raw.iloc[:, source['yield_column']], errors='coerce')})
    else:
        raw = _request(source['url'], cfg, params=source['params']).json()
        rows = (raw.get('result') or {}).get('data') or []
        frame = pd.DataFrame(rows).rename(columns={'SOLAR_DATE': 'date', 'EMM00166466': 'value'})[['date', 'value']]
        frame['date'] = pd.to_datetime(frame['date'], errors='coerce')
        frame['value'] = pd.to_numeric(frame['value'], errors='coerce')
    frame = frame.dropna().sort_values('date').drop_duplicates('date')
    frame['date'] = frame['date'].dt.strftime('%Y-%m-%d')
    frame = frame[frame['date'] <= date.today().isoformat()]
    if frame.empty:
        raise ValueError('没有有效的股息率或国债收益率。')
    return json.loads(frame.to_json(orient='records'))


def percentile(series, window, minimum):
    """Midrank empirical percentile using only the observations available so far."""
    values = pd.to_numeric(series, errors='coerce').dropna().tail(window)
    if len(values) < minimum:
        return None
    current = values.iloc[-1]
    return float(100 * ((values < current).sum() + .5 * (values == current).sum()) / len(values))


def linear_score(value, limits):
    return float(np.clip((value - limits[0]) / (limits[1] - limits[0]) * 100, 0, 100))


def price_metrics(frame, cfg):
    if frame is None or frame.empty:
        return None
    close = frame.sort_values('date').set_index('date')['close']
    ret = close.pct_change(fill_method=None)
    m = cfg['momentum_days']; v = cfg['volatility_days']; d = cfg['drawdown_days']
    momentum = close.pct_change(m, fill_method=None)
    vol = ret.rolling(v, min_periods=v).std(ddof=1) * np.sqrt(cfg['trading_days_per_year'])
    drawdown = close / close.rolling(d, min_periods=d).max() - 1
    scores = {'momentum': percentile(momentum, cfg['history_window'], cfg['min_observations']),
              'drawdown': percentile(drawdown, cfg['history_window'], cfg['min_observations']),
              'volatility': percentile(-vol, cfg['history_window'], cfg['min_observations'])}
    ready = all(x is not None for x in scores.values())
    return dict(date=str(close.index[-1]), temperature=sum(scores[k] * w for k, w in cfg['weights'].items()) if ready else None,
                daily_return=float(ret.iloc[-1]) if len(close) > 1 else None,
                return_20=float(momentum.iloc[-1]) if pd.notna(momentum.iloc[-1]) else None,
                volatility=float(vol.iloc[-1]) if pd.notna(vol.iloc[-1]) else None,
                drawdown=float(drawdown.iloc[-1]) if pd.notna(drawdown.iloc[-1]) else None, components=scores)


def _fresh(day, cfg):
    return bool(day) and 0 <= (date.today() - date.fromisoformat(day)).days <= cfg['refresh']['stale_days']


def dividend_score(frame, financial, cfg):
    spec = cfg['dividend_score']; weights = spec['weights']
    result = dict(label=spec['label'], name=cfg['indices'][spec['index']]['name'], score=None, date=None,
                  weights=weights, components={}, description=spec['description'], message='缺少红利指数、股息率或国债收益率。')
    div = financial.get('dividend', []); bond = financial.get('bond', [])
    if frame is None or frame.empty or not div or not bond:
        return result
    # One common valuation date; every input is known on or before it. Never join
    # today's dividend yield onto historical prices or future bond observations.
    end = min(str(frame['date'].max()), max(x['date'] for x in div), max(x['date'] for x in bond))
    frame = frame[frame['date'] <= end].sort_values('date')
    if frame.empty:
        return result
    div = max((x for x in div if x['date'] <= end), key=lambda x: x['date'], default=None)
    bond = max((x for x in bond if x['date'] <= end), key=lambda x: x['date'], default=None)
    result['date'] = end
    if not div or not bond:
        return result
    dates = dict(dividend=div['date'], bond=bond['date'], price=str(frame['date'].iloc[-1]))
    result['input_dates'] = dates
    if any((date.fromisoformat(end) - date.fromisoformat(d)).days > spec['alignment_days'] for d in dates.values()):
        result['message'] = '数据日期相差过大，暂不合成总分。'
        return result
    close = frame['close']; n = spec['drawdown_days']
    vol = close.pct_change(fill_method=None).rolling(spec['volatility_days']).std(ddof=1) * np.sqrt(cfg['temperature']['trading_days_per_year'])
    vol_score = percentile(-vol, spec['history_window'], spec['min_observations'])
    dd = float(1 - close.iloc[-1] / close.tail(n).max()) if len(close) >= n else None
    gap = float(div['value'] - bond['value'])
    parts = {'yield_gap': dict(label='股债利差', value=gap, unit='个百分点', score=linear_score(gap, spec['yield_gap_range_pp']),
                              formula='股息率减中国10年国债收益率；从'+str(spec['yield_gap_range_pp'][0])+'到'+str(spec['yield_gap_range_pp'][1])+'个百分点线性映射为0到100分'),
             'drawdown': dict(label='回撤位置', value=dd, unit='ratio', score=linear_score(dd, spec['drawdown_range']) if dd is not None else None,
                              formula='离近'+str(n)+'个交易日最高收盘越远，价格位置得分越高；回撤'+str(spec['drawdown_range'][1]*100)+'%记100分'),
             'volatility': dict(label='低波动', value=float(vol.iloc[-1]) if pd.notna(vol.iloc[-1]) else None, unit='ratio', score=vol_score,
                                formula='20日年化历史波动率在自身近三年的反向百分位；波动越低，得分越高')}
    result.update(components=parts, dividend_yield=float(div['value']), bond_yield=float(bond['value']), stale=not _fresh(end, cfg))
    if result['stale']:
        result['message'] = '输入数据已过旧，保留分项原值，暂停计算总分。'
    elif any(p['score'] is None for p in parts.values()):
        result['message'] = '历史长度不足，暂不合成总分。'
    else:
        result.update(score=sum(parts[k]['score'] * w for k, w in weights.items()), message='按所示权重计算；回撤更深也可能意味着风险，不能单凭高分买入。')
    return result


def comparison(frames, cfg, days):
    selected = cfg['comparison']['indices']; end = date.today().isoformat()
    start = (date.today() - timedelta(days=days)).isoformat()
    result = dict(series=[], start=None, end=None, points=0, description=cfg['comparison']['description'])
    if any(frames.get(k) is None or frames[k].empty for k in selected):
        result['message'] = '等待三个指数的日线，缺数据时不拼凑对比。'
        return result
    joined = pd.concat([frames[k].set_index('date')['close'].rename(k) for k in selected], axis=1, join='inner').sort_index()
    joined = joined.loc[start:end].dropna(); joined = joined[(joined > 0).all(axis=1)]
    if len(joined) < 2:
        result['message'] = '这一区间共同交易日不足。'
        return result
    normalized = joined / joined.iloc[0] * 100
    for key in selected:
        item = cfg['indices'][key]
        result['series'].append(dict(id=key, name=item['name'], color=item['color'],
                                     values=[dict(date=str(d), value=float(v)) for d, v in normalized[key].items()]))
    result.update(start=str(joined.index[0]), end=str(joined.index[-1]), points=len(joined), message='共同交易日起点=100；价格表现，不是含分红的总回报。')
    return result


def volume_check():
    from data.research_services import stock_list
    result = dict(available=False, unit='股', stocks=[], message='尚无可抽查的个股日线。')
    local = DailyCache()
    for stock in stock_list({'cached': '1', 'limit': 3})['stocks']:
        frame = local.get_daily(stock['code'], start=(date.today() - timedelta(days=45)).isoformat())
        if frame is None or frame.empty:
            continue
        frame = normalize_units(frame); volume = frame['volume']
        result['stocks'].append(dict(code=stock['code'], date=str(frame['date'].iloc[-1]), rows=len(frame),
                                     valid_rows=int(volume.notna().sum()), latest=float(volume.iloc[-1]) if pd.notna(volume.iloc[-1]) else None))
    result['available'] = any(s['valid_rows'] for s in result['stocks'])
    if result['stocks']:
        result['message'] = '本机个股日线包含成交量（股）和成交额（元），K线下方显示成交量柱。以下为最近45日的抽查，不代表所有股票都无缺失。'
    return result


def build_snapshot(cfg, feeds, financial):
    local = cache(); frames = {k: local.get_daily(v['code'], adjust='none') for k, v in cfg['indices'].items()}
    sectors = []
    for key, item in cfg['sectors'].items():
        metrics = price_metrics(local.get_daily(item['code'], adjust='none'), cfg['temperature'])
        row = dict(id=key, name=item['name'], code=item['code'], **(metrics or dict(date=None, temperature=None)))
        row['stale'] = not _fresh(row['date'], cfg)
        if row['stale']:
            row['temperature'] = None
        sectors.append(row)
    valid = [s for s in sectors if s['temperature'] is not None]
    common = max((s['date'] for s in valid), default=None)
    same = [s for s in valid if s['date'] == common]
    coverage = len(same) / len(sectors) if sectors else 0
    pressure = dict(score=100 - float(np.mean([s['temperature'] for s in same])) if coverage >= cfg['temperature']['min_sector_coverage'] else None,
                    date=common, coverage=len(same), total=len(sectors), label='A股市场压力（自定义百分制）',
                    description='同一数据日行业温度均值的反向分数；越高表示走势越弱、波动或回撤压力越大，非期权隐含恐慌指数。')
    fear = []
    for key, item in cfg['fear_indices'].items():
        frame = local.get_daily(item['code'], adjust='none'); values = []; last = None; day = None; rank = None
        if frame is not None and not frame.empty:
            last = float(frame['close'].iloc[-1]); day = str(frame['date'].iloc[-1])
            rank = percentile(frame['close'], cfg['temperature']['history_window'], cfg['temperature']['min_observations'])
            values = frame.tail(252)[['date', 'close']].rename(columns={'close': 'value'}).to_dict('records')
        fear.append(dict(id=key, **item, value=last, date=day, percentile=rank, stale=not _fresh(day, cfg), values=values))
    result = dict(ok=True, generated_at=datetime.now().isoformat(timespec='seconds'), sectors=sectors, pressure=pressure,
                fear=fear, dividend=dividend_score(frames.get(cfg['dividend_score']['index']), financial, cfg),
                volume=volume_check(), feeds=feeds, temperature_description=cfg['temperature']['description'])
    from data.market_interpretation import build
    histories = dict(frames)
    for prefix, group in [('sector:', cfg['sectors']), ('fear:', cfg['fear_indices'])]:
        histories.update({prefix+k: local.get_daily(v['code'], adjust='none') for k, v in group.items()})
    result['interpretation'] = build(histories, financial, result, cfg)
    return result


def refresh(reason='manual'):
    with _lock:
        if _state['state'] == 'running':
            return dict(ok=True, existing=True, **_state)
        cfg = settings()
        tasks = [('index:' + k, v) for k, v in cfg['indices'].items()] + [('sector:' + k, v) for k, v in cfg['sectors'].items()] + [('fear:' + k, v) for k, v in cfg['fear_indices'].items()]
        _state.update(state='running', message='正在更新指数、板块、恐慌指标和股债数据…', completed=0, total=len(tasks) + 2,
                      started_at=datetime.now().isoformat(timespec='seconds'), reason=reason)
    threading.Thread(target=_worker, args=(cfg, tasks), name='workbench-market-refresh', daemon=True).start()
    return dict(ok=True, existing=False, **_state)


def _worker(cfg, tasks):
    feeds = _read('feeds.json', {}); financial = _read('financial.json', {}); failed = 0
    try:
        local = cache()
        with ThreadPoolExecutor(max_workers=cfg['refresh']['workers']) as pool:
            futures = {pool.submit(fetch_quotes, item, cfg): (key, item) for key, item in tasks}
            futures.update({pool.submit(fetch_financial, key, cfg): (key, None) for key in ('dividend', 'bond')})
            for future in as_completed(futures):
                key, item = futures[future]; stamp = datetime.now().isoformat(timespec='seconds')
                source = cfg['sources'][item['provider'] if item else key]
                feed = dict(feeds.get(key, {}), name=item['name'] if item else source['label'], source=source['label'], url=source['reference'], attempted_at=stamp)
                try:
                    data = future.result()
                    if item:
                        local.put_daily(item['code'], data, adjust='none', source='dashboard:' + item['provider'])
                        day = str(data['date'].max())
                    else:
                        financial[key] = data; day = max(x['date'] for x in data)
                    feed.update(state='ready', date=day, updated_at=stamp, error=None, stale=not _fresh(day, cfg))
                except Exception as error:
                    failed += 1
                    feed.update(state='error', error=str(error)[:240])
                feeds[key] = feed
                with _lock:
                    _state['completed'] += 1
        _write('feeds.json', feeds); _write('financial.json', financial)
        _write('snapshot.json', build_snapshot(cfg, feeds, financial))
        with _lock:
            _state.update(state='partial' if failed else 'done', failed=failed, finished_at=datetime.now().isoformat(timespec='seconds'),
                          message='更新完成。' if not failed else f'{failed}项更新失败；保留已有缓存，查看各数据源状态。')
        _write('last_refresh.json', dict(_state))
    except Exception as error:
        with _lock:
            _state.update(state='failed', message='更新未完成：' + str(error)[:240], finished_at=datetime.now().isoformat(timespec='seconds'))


def dashboard(query=None):
    cfg = settings(); query = query or {}
    try:
        days = int(query.get('days', cfg['comparison']['default_days']))
    except (TypeError, ValueError):
        raise ValueError('请选择支持的图表区间。') from None
    if str(days) not in cfg['comparison']['periods']:
        raise ValueError('请选择支持的图表区间。')
    data = _read('snapshot.json', dict(ok=True, sectors=[], fear=[], pressure={}, dividend={}, volume={}, feeds={}))
    # Recheck age when displaying persisted results after a long offline period.
    for item in data.get('fear', []) + data.get('sectors', []):
        item['stale'] = not _fresh(item.get('date'), cfg)
        if item['stale'] and 'temperature' in item:
            item['temperature'] = None
    for key in ('pressure', 'dividend'):
        if not _fresh(data.get(key, {}).get('date'), cfg):
            data.setdefault(key, {}).update(score=None, stale=True)
    current_values = {key: data.get(key, {}).get('score') for key in ('pressure', 'dividend')}
    current_values.update({f['id']: None if f.get('stale') else f['value'] for f in data.get('fear', [])})
    for item in data.get('interpretation', {}).get('items', []):
        if current_values.get(item['id']) is None:
            item.update(value=None, current_bin=None)
    with _lock:
        data['update'] = dict(_state) if _state['state'] != 'idle' else _read('last_refresh.json', dict(_state))
    local = cache(); frames = {k: local.get_daily(cfg['indices'][k]['code'], adjust='none') for k in cfg['comparison']['indices']}
    data['comparison'] = comparison(frames, cfg, days)
    data['periods'] = cfg['comparison']['periods']; data['days'] = days
    data['poll_seconds'] = cfg['refresh']['poll_seconds']
    return data


def startup():
    if settings()['refresh']['on_startup']:
        return refresh('startup')
    return dict(ok=True, state='disabled')
