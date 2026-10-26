"""基本面 + 技术面筛选器 → 一键保存候选池

流程：
  1. 从缓存加载截面数据（日线 + 财报）
  2. 按用户指定的因子条件过滤（支持 fund: 和 technical 两类）
  3. 按综合得分降序排名
  4. 可选：直接保存为命名股票池（调用 research_pools.save_pool）

端点：GET /api/studio/pools/screen
  参数：
    asof          – 截面日期，默认最新交易日 (YYYY-MM-DD)
    factors       – 逗号分隔的因子 key 列表（来自 factor_library）
    min_score     – 综合得分下限（0-100，默认 0）
    top_n         – 最多返回 N 只（默认 50）
    pool          – 宇宙股票池：all_a / csi300 / csi500，或逗号分隔的代码列表
    profit_ok     – 1=只保留单季盈利股票（默认 1）
    exclude_st    – 1=排除 ST（默认 1）
    save          – 1=同时保存为候选池
    pool_name     – 保存的候选池名称（save=1 时必填）
"""
from datetime import date, timedelta
from pathlib import Path
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent


# ── 宇宙加载 ────────────────────────────────────────────────────────────────

def _load_universe(pool: str, asof: str, cache) -> list:
    """返回候选代码列表（6位.交易所 格式）"""
    from data.cache import DailyCache
    import sqlite3, re
    from contextlib import closing

    # 用户指定代码列表
    codes_raw = [c.strip().upper() for c in re.split(r'[\s,，;；]+', pool) if c.strip()]
    if codes_raw and all(re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', c) for c in codes_raw):
        return codes_raw

    # 从缓存读全部代码
    all_codes = []
    for db_path in [cache.db_path, *cache._inc_paths()]:
        if not Path(db_path).exists():
            continue
        with closing(sqlite3.connect(str(db_path))) as con:
            rows = con.execute(
                "SELECT DISTINCT code FROM daily_bar WHERE date=?", (asof,)
            ).fetchall()
            all_codes.extend(r[0] for r in rows)
    all_codes = list(dict.fromkeys(all_codes))  # 去重保序

    if pool in ('csi300', 'csi500'):
        # 优先读配置文件；无配置则降级全 A
        cfg_path = BASE / 'config' / f'{pool}.txt'
        if cfg_path.exists():
            pool_codes = [l.strip().upper() for l in cfg_path.read_text(encoding='utf-8').splitlines() if l.strip()]
            return [c for c in all_codes if c in set(pool_codes)]

    return all_codes  # all_a fallback


# ── 截面因子得分 ─────────────────────────────────────────────────────────────

def _cross_section_score(codes: list, asof: str, factor_keys: list, cache) -> pd.DataFrame:
    """计算截面因子得分，返回 DataFrame(code, factor_key..., score)"""
    from factors.research_library import factor_definitions, raw_factor
    from data.research_fundamentals import panels as fin_panels

    # 加载 OHLCV 窗口（250 日预热）
    start = (date.fromisoformat(asof) - timedelta(days=400)).isoformat()
    raw = cache.get_daily_batch(codes, start=start, end=asof,
                                adjust='qfq', fields=['open', 'high', 'low', 'close', 'volume', 'amount', 'turn', 'is_st'])
    if not raw:
        return pd.DataFrame()

    panels: dict = {}
    for field in ('open', 'high', 'low', 'close', 'volume', 'amount', 'turn'):
        panels[field] = pd.DataFrame(
            {code: df.set_index('date')[field] for code, df in raw.items() if field in df.columns}
        ).sort_index()

    # 注入财报面板
    defs = factor_definitions()
    fund_keys = set()
    for fk in factor_keys:
        spec = defs.get(fk, {})
        for req in spec.get('requires', []):
            if req.startswith('fund:'):
                fund_keys.add(req[5:])
    if fund_keys:
        try:
            fp = fin_panels(panels['close'].index, list(panels['close'].columns), fund_keys)
            for metric, panel in fp.items():
                panels['fund:' + metric] = panel
        except Exception:
            pass

    # 最新截面行
    last_date = panels['close'].index[-1]
    rows = {}
    for fk in factor_keys:
        try:
            mat = raw_factor(fk, panels, defs)
            row = mat.loc[last_date] if last_date in mat.index else mat.iloc[-1]
            rows[fk] = row
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df.index.name = 'code'
    df = df.reset_index()

    # 跨截面排名归一化（0-100），各因子取 default_sign
    score = pd.Series(0.0, index=df.index)
    valid_factors = []
    for fk in factor_keys:
        if fk not in df.columns:
            continue
        sign = defs.get(fk, {}).get('default_sign', 1)
        col = df[fk] * sign
        rk = col.rank(pct=True, na_option='keep') * 100
        score = score.add(rk.fillna(50), fill_value=0)
        valid_factors.append(fk)

    df['score'] = (score / max(len(valid_factors), 1)).round(1)
    return df


# ── ST 过滤 ──────────────────────────────────────────────────────────────────

def _exclude_st(codes: list, asof: str, cache) -> list:
    import sqlite3
    from contextlib import closing
    st_set = set()
    for db_path in [cache.db_path, *cache._inc_paths()]:
        if not Path(db_path).exists():
            continue
        with closing(sqlite3.connect(str(db_path))) as con:
            rows = con.execute(
                "SELECT code FROM daily_bar WHERE date=? AND is_st=1", (asof,)
            ).fetchall()
            st_set.update(r[0] for r in rows)
    return [c for c in codes if c not in st_set]


# ── 盈利过滤 ─────────────────────────────────────────────────────────────────

def _profit_ok_codes(codes: list, asof: str) -> list:
    """返回最新单季净利 > 0 的代码（基于 fundamental.py）"""
    try:
        from factors.fundamental import load_finance_panel, profit_ok_at
        fin = load_finance_panel()
        ok = []
        for code in codes:
            code6 = code.split('.')[0]
            if code6 in fin and profit_ok_at(fin[code6], asof):
                ok.append(code)
        return ok if ok else codes  # 无财报数据时不过滤
    except Exception:
        return codes


# ── 最新交易日 ───────────────────────────────────────────────────────────────

def _latest_trading_date(cache) -> str:
    import sqlite3
    from contextlib import closing
    for db_path in [cache.db_path, *cache._inc_paths()]:
        if not Path(db_path).exists():
            continue
        with closing(sqlite3.connect(str(db_path))) as con:
            row = con.execute("SELECT MAX(date) FROM daily_bar").fetchone()
            if row and row[0]:
                return row[0]
    return date.today().isoformat()


# ── 主入口 ───────────────────────────────────────────────────────────────────

def screen(query: dict) -> dict:
    """
    筛选器主入口，供 GET /api/studio/pools/screen 调用。

    返回：
      ok, asof, codes, scores([{code, score, ...factors}]),
      total, saved(bool), pool_id(若 save=1)
    """
    from data.cache import DailyCache
    from factors.research_library import factor_definitions

    cache = DailyCache()
    defs = factor_definitions()

    # ── 参数解析 ────────────────────────────────────────────────────────────
    asof = (query.get('asof') or '').strip() or _latest_trading_date(cache)
    raw_factors = [f.strip() for f in (query.get('factors') or '').split(',') if f.strip()]
    min_score = float(query.get('min_score', 0))
    top_n = min(int(query.get('top_n', 50)), 500)
    pool_id_param = (query.get('pool') or 'all_a').strip()
    profit_filter = str(query.get('profit_ok', '1')) == '1'
    st_filter = str(query.get('exclude_st', '1')) == '1'
    do_save = str(query.get('save', '0')) == '1'
    pool_name = (query.get('pool_name') or '').strip()

    # 因子合法性检查
    unknown = [f for f in raw_factors if f not in defs]
    if unknown:
        raise ValueError('以下因子不存在，请检查因子 key：' + '、'.join(unknown))
    if not raw_factors:
        raise ValueError('请至少指定一个筛选因子（factors 参数）。')

    # ── 宇宙 ────────────────────────────────────────────────────────────────
    codes = _load_universe(pool_id_param, asof, cache)
    if not codes:
        raise ValueError('未找到可用股票，请检查日期或宇宙参数。')

    # ── 过滤 ────────────────────────────────────────────────────────────────
    if st_filter:
        codes = _exclude_st(codes, asof, cache)
    if profit_filter:
        codes = _profit_ok_codes(codes, asof)

    if not codes:
        return dict(ok=True, asof=asof, codes=[], scores=[], total=0,
                    saved=False, message='过滤后无剩余股票。')

    # ── 因子得分 ─────────────────────────────────────────────────────────────
    df = _cross_section_score(codes, asof, raw_factors, cache)
    if df.empty:
        return dict(ok=True, asof=asof, codes=[], scores=[], total=0,
                    saved=False, message='因子计算未返回数据，请检查因子和日期。')

    df = df[df['score'] >= min_score].sort_values('score', ascending=False).head(top_n)
    out_codes = df['code'].tolist()

    scores = df.replace({np.nan: None}).to_dict(orient='records')

    result = dict(ok=True, asof=asof, codes=out_codes,
                  scores=scores, total=len(out_codes), saved=False)

    # ── 保存候选池 ───────────────────────────────────────────────────────────
    if do_save:
        if not pool_name:
            raise ValueError('保存候选池时请填写 pool_name。')
        from backtest.research_pools import save_pool
        saved = save_pool(dict(
            name=pool_name,
            codes=out_codes,
            signal_date=asof,
            source_strategy='screener',
            source_params=dict(
                factors=raw_factors,
                min_score=min_score,
                top_n=top_n,
                pool=pool_id_param,
                profit_ok=profit_filter,
                exclude_st=st_filter,
            ),
        ))
        result.update(saved=True, pool_id=saved['id'],
                      message=f'已保存候选池「{pool_name}」，共 {len(out_codes)} 只。')
    else:
        result['message'] = f'筛选完成，共 {len(out_codes)} 只满足条件。'

    return result
