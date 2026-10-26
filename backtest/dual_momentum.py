"""横截面选股 × 个股时间序列择时（双层动量）研究引擎。

H = C × S：C 每 rebalance_days 个交易日按横截面因子选出前 topn 名，期间不变；
S 是个股自身因子相对阈值的状态，每天收盘检查。信号用当日收盘，下一交易日开盘成交。
一次运行同时给出 A 基准 / B 纯选股 / C 纯择时 / D 双层 四组对照、训练期与样本外分段、
“择时在已选股票里是否还有增量信息”的检验，以及当前名单的次日信号。
参数与文案在 config/dual_momentum.yaml；第三方成分股名单只缓存在本机 data/cache。
"""
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import re
import time
from uuid import uuid4

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
RUN_ID = re.compile(r'[0-9a-f]{32}')
CODE = re.compile(r'\d{6}\.(SH|SZ|BJ)')
COST_KEYS = ('commission_bps', 'min_commission', 'stamp_tax_bps', 'slippage_bps')
REJECT = ('missing_or_suspended', 'one_price_bar', 'limit_up', 'limit_down')
METRIC_KEYS = ('annual_return', 'volatility', 'sharpe', 'max_drawdown', 'avg_exposure', 'turnover', 'trades', 'costs')


def settings():
    from factors.research_library import read_config
    return read_config('dual_momentum')


def _store():
    path = BASE / 'output' / 'dual_momentum'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clean(obj):
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return _clean(obj.tolist())
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        v = float(obj)
        return round(v, 8) if math.isfinite(v) else None
    return obj


def _read(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _write(path, data):
    temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temp.write_text(json.dumps(_clean(data), ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temp.replace(path)


# ── 股票池 ──────────────────────────────────────────────────────────────────

def constituents(symbol, cfg):
    """当前指数成分股（中证指数公司），本机缓存 max_age_days 天；更新失败时退回旧名单。"""
    c = cfg['constituents']
    folder = BASE / c['cache_dir']
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (symbol + '.json')
    cached = _read(path)
    if cached:
        try:
            if (date.today() - date.fromisoformat(cached['fetched_at'][:10])).days < c['max_age_days']:
                return cached, None
        except (KeyError, ValueError):
            pass
    try:
        import akshare as ak
        from data.fetcher_akshare import _no_proxy
        with _no_proxy(timeout=25):
            df = ak.index_stock_cons_weight_csindex(symbol=symbol)
        codes = sorted({str(r['成分券代码']).zfill(6) + '.' + c['exchanges'][str(r['交易所']).strip()]
                        for _, r in df.iterrows() if str(r.get('交易所', '')).strip() in c['exchanges']})
        if not codes:
            raise ValueError('数据源没有返回成分股。')
        item = dict(symbol=symbol, codes=codes, as_of=str(df['日期'].max())[:10], source=c['source'],
                    fetched_at=datetime.now().isoformat(timespec='seconds'))
        _write(path, item)
        return item, None
    except Exception as error:
        if cached:
            return cached, '成分股名单更新失败，使用 %s 取得的旧名单：%s' % (cached['fetched_at'][:10], str(error)[:120])
        raise ValueError('无法取得指数成分股：' + str(error)[:160]) from None


# ── 参数 ────────────────────────────────────────────────────────────────────

def _number(payload, key, spec, default):
    value = payload.get(key, default)
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(spec['label'] + '必须是数值。') from None
    if not math.isfinite(value) or not spec['min'] <= value <= spec['max']:
        raise ValueError('%s应在%s到%s之间。' % (spec['label'], spec['min'], spec['max']))
    if spec.get('integer'):
        if not value.is_integer():
            raise ValueError(spec['label'] + '必须是整数。')
        return int(value)
    return value


def validate(payload):
    from factors.research_library import factor_definitions, read_config
    cfg, payload = settings(), payload if isinstance(payload, dict) else {}
    d, p = cfg['defaults'], {}
    for key, spec in cfg['fields'].items():
        if spec.get('type') != 'date':
            p[key] = _number(payload, key, spec, d.get(key))
    research = read_config('research')['parameters']
    for key in COST_KEYS:
        p[key] = _number(payload, key, research[key], research[key]['default'])
    try:
        p['start'] = date.fromisoformat(str(payload.get('start') or d['start'])).isoformat()
        p['end'] = date.fromisoformat(str(payload.get('end') or d.get('end') or date.today().isoformat())).isoformat()
    except ValueError:
        raise ValueError('日期格式应为 YYYY-MM-DD。') from None
    if p['start'] >= p['end']:
        raise ValueError('开始日期必须早于结束日期。')
    p['universe'] = payload.get('universe') or d['universe']
    if p['universe'] not in cfg['universes']:
        raise ValueError('请选择支持的股票池。')
    defs = factor_definitions()
    for side in ('cs', 'ts'):
        key = payload.get(side + '_factor') or d[side + '_factor']
        if key not in cfg['factor_choices'] or key not in defs or 'n' not in defs[key].get('params', {}):
            raise ValueError('请选择列表中的因子。')
        p[side + '_factor'] = key
    try:
        p['cs_sign'] = int(payload.get('cs_sign', d['cs_sign']))
    except (TypeError, ValueError):
        p['cs_sign'] = 0
    if p['cs_sign'] not in (1, -1):
        raise ValueError('选股方向只能是高值优先或低值优先。')
    p['ts_direction'] = payload.get('ts_direction') or d['ts_direction']
    if p['ts_direction'] not in cfg['directions']:
        raise ValueError('请选择择时方向。')
    return p


# ── 数据 ────────────────────────────────────────────────────────────────────

def _spec(defs, key, n):
    spec = dict(defs[key])
    spec['params'] = dict(spec.get('params', {}), n=int(n))
    return spec


def factor(panels, defs, key, n, codes):
    from factors.research_library import raw_factor
    name = 'dual__%s__%d' % (key, n)
    raw = raw_factor(name, panels, {**defs, name: _spec(defs, key, n)})
    return raw.reindex(columns=codes).to_numpy(dtype=float)


def load(p, lookbacks=None):
    from data.cache import DailyCache
    from factors.research_library import factor_definitions
    cfg, defs, warnings = settings(), factor_definitions(), []
    label = cfg['universes'][p['universe']]['label']
    if p['universe'].startswith('index:'):
        item, warn = constituents(cfg['universes'][p['universe']]['symbol'], cfg)
        if warn:
            warnings.append(warn)
        wanted = item['codes']
        universe = dict(label=label, as_of=item['as_of'], source=item['source'], listed=len(wanted))
    else:
        from data.research_services import stock_snapshot
        _, coverage = stock_snapshot()
        wanted = sorted(c for c in coverage if CODE.fullmatch(c))[:p['cache_stocks']]
        universe = dict(label=label, source='本机日线缓存', listed=len(wanted))
    periods = 0
    for side in ('cs', 'ts'):
        for n in lookbacks or [p[side + '_n']]:
            spec = _spec(defs, p[side + '_factor'], n)
            periods = max(periods, sum(v for v in spec['params'].values() if isinstance(v, int) and not isinstance(v, bool)))
    start = (date.fromisoformat(p['start']) - timedelta(days=int(periods * 1.6) + 40)).isoformat()
    fields = ['open', 'high', 'low', 'close', 'volume', 'is_st']
    frames = DailyCache().get_daily_batch(wanted, start=start, end=p['end'], adjust='qfq', fields=fields)
    codes = [c for c in wanted if c in frames]
    if len(codes) < 2:
        raise ValueError('股票池在本机缓存中不足2只，请先在「股票与数据」更新日线。')
    if len(codes) < len(wanted):
        warnings.append('股票池中有 %d 只没有本机日线，已排除。' % (len(wanted) - len(codes)))
    indexed = {c: frames[c].set_index('date') for c in codes}
    panels = {f: pd.DataFrame({c: indexed[c][f] for c in codes}).sort_index() for f in fields}
    panels['is_st'] = panels['is_st'].apply(pd.to_numeric, errors='coerce')
    universe['used'] = len(codes)
    return panels, codes, universe, warnings, defs


def _limits(codes, dates, st, cfg):
    lim = cfg['limits']['limit_pct']
    gem = np.array([c.endswith('.SZ') and c[:3] in ('300', '301') for c in codes])
    star = np.array([c[:3] in ('688', '689') for c in codes])
    bj = np.array([c.endswith('.BJ') for c in codes])
    main = ~(gem | star | bj)
    after = np.array([d >= cfg['limits']['gem_reform_date'] for d in dates])[:, None]
    out = np.broadcast_to(np.where(bj, lim['bj'], np.where(star, lim['star'], lim['main'])).astype(float), st.shape).copy()
    out = np.where(gem[None, :] & after, lim['gem'], out)
    # ST 只收窄主板（及注册制改革前的创业板）的涨跌幅限制。
    out = np.where(st & (main[None, :] | (gem[None, :] & ~after)), lim['st'], out)
    return out / 100


def matrices(panels, codes, cfg):
    dates = [str(d) for d in panels['close'].index]
    get = lambda f: panels[f].reindex(columns=codes).to_numpy(dtype=float)
    O, H, L, C, V = (get(f) for f in ('open', 'high', 'low', 'close', 'volume'))
    st = panels['is_st'].reindex(columns=codes).fillna(0).to_numpy(dtype=float) == 1
    with np.errstate(invalid='ignore', divide='ignore'):
        close_ok = np.isfinite(C) & (C > 0)
        volume_ok = np.isfinite(V) & (V > 0)
        valid = close_ok & volume_ok & np.isfinite(O) & (O > 0) & np.isfinite(H) & np.isfinite(L)
        one = np.isclose(H, L, rtol=1e-8, atol=1e-8)
        prev = pd.DataFrame(np.where(close_ok, C, np.nan)).ffill().shift(1).to_numpy()
        gap = np.nan_to_num(O / prev - 1, nan=0.0, posinf=0.0, neginf=0.0)
    limit, tol = _limits(codes, dates, st, cfg), cfg['limits']['tolerance']
    return dict(dates=dates, O=O, C=C, st=st, valid=valid, one=one, base=close_ok & volume_ok & ~st,
                # 开盘就封在涨停买不进、封在跌停卖不出；一字板和停牌不成交。
                can_buy=valid & ~one & (gap < limit - tol), can_sell=valid & ~one & (gap > -limit + tol))


# ── 信号 ────────────────────────────────────────────────────────────────────

def select(m, F, sign, topn, s):
    eligible = m['base'][s] & np.isfinite(F[s])
    idx = np.nonzero(eligible)[0]
    return idx[np.argsort(-(sign * F[s][idx]), kind='stable')][:topn]


def candidates(m, F, sign, topn, every, t0, universe=False):
    """C 矩阵（按成交日索引）：成交日 k 用前一日 k-1 的收盘排名；每 every 个成交日更新一次。"""
    T, N = m['O'].shape
    C, rebal, current = np.zeros((T, N), bool), np.zeros(T, bool), np.zeros(N, bool)
    for k in range(t0, T):
        if (k - t0) % every == 0:
            current = m['base'][k - 1].copy() if universe else np.zeros(N, bool)
            if not universe:
                current[select(m, F, sign, topn, k - 1)] = True
            rebal[k] = True
        C[k] = current
    return C, rebal


def ts_state(F, direction, threshold):
    with np.errstate(invalid='ignore'):
        state = (F > threshold) if direction == 'above' else (F < threshold)
    return state & np.isfinite(F)


def shift(S):
    out = np.zeros_like(S)
    out[1:] = S[:-1]
    return out


# ── 账本 ────────────────────────────────────────────────────────────────────

def ledger(m, hold, weight, rebal, p, t0, labels=None, cap=5000):
    """开盘成交的现金账本。调仓日把持仓调回目标权重；非调仓日只买新满足条件、卖不再满足条件的股票。"""
    O, C = m['O'], m['C']
    T, N = O.shape
    cash, shares, marks = float(p['initial_capital']), np.zeros(N), np.full(N, np.nan)
    rate, slip, tax = p['commission_bps'] / 1e4, p['slippage_bps'] / 1e4, p['stamp_tax_bps'] / 1e4
    minc, band = float(p['min_commission']), p['rebalance_threshold_pct'] / 100
    equity, exposure, holdings, traded, trades = [], [], [], [], []
    costs, rejected, count = dict(commission=0., stamp_tax=0., slippage_cost=0.), dict.fromkeys(REJECT, 0), 0

    def reject(mask, k, side):
        v, one = m['valid'][k], m['one'][k]
        rejected['missing_or_suspended'] += int((mask & ~v).sum())
        rejected['one_price_bar'] += int((mask & v & one).sum())
        rejected['limit_up' if side == 'buy' else 'limit_down'] += int((mask & v & ~one).sum())

    def log(k, idx, side, qty, raw, price, amount, was=None):
        Cm, Sm, dates, codes = labels
        for i, j in enumerate(idx):
            if len(trades) >= cap:
                return
            if side == 'sell':
                why = '落选卖出' if not Cm[k, j] else '择时卖出' if not Sm[k, j] else '调仓减仓'
            else:
                why = '调仓加仓' if was[i] else '调仓买入' if rebal[k] else '择时买入'
            trades.append(dict(date=dates[k], signal_date=dates[k - 1], code=codes[j], side=side, reason=why,
                               quantity=qty[i], open=raw[i], fill_price=price[i], amount=amount[i]))

    for k in range(t0, T):
        o = O[k]
        ok = np.isfinite(o) & (o > 0)
        safe = np.where(ok, o, 1.)
        held = shares > 0
        value = cash + float(np.nansum(np.where(held, shares * np.where(ok, o, marks), 0.)))
        target = np.where(hold[k] & ok, value * weight / safe, 0.)
        if rebal[k]:
            desired = np.where(ok, target, shares)
            near = held & hold[k] & ok & (np.abs(desired - shares) * safe < band * value)
            desired = np.where(near, shares, desired)
        else:
            desired = shares.copy()
            desired[held & ~hold[k]] = 0.
            fresh = hold[k] & ~held & ok
            desired[fresh] = target[fresh]
        turnover = 0.
        sell = desired < shares - 1e-9
        if sell.any():
            allowed = sell & m['can_sell'][k]
            reject(sell & ~allowed, k, 'sell')
            idx = np.nonzero(allowed)[0]
            if len(idx):
                qty, raw = shares[idx] - desired[idx], o[idx]
                price = raw * (1 - slip)
                amount = qty * price
                fee, duty = np.maximum(minc, amount * rate), amount * tax
                cash += float((amount - fee - duty).sum())
                shares[idx] = desired[idx]
                costs['commission'] += float(fee.sum()); costs['stamp_tax'] += float(duty.sum())
                costs['slippage_cost'] += float((qty * raw * slip).sum())
                turnover += float(amount.sum()); count += len(idx)
                if labels is not None:
                    log(k, idx, 'sell', qty, raw, price, amount)
        buy = desired > shares + 1e-9
        if buy.any():
            allowed = buy & m['can_buy'][k]
            reject(buy & ~allowed, k, 'buy')
            idx = np.nonzero(allowed)[0]
            if len(idx):
                raw = o[idx]
                price = raw * (1 + slip)
                amount = (desired[idx] - shares[idx]) * price
                total = float(amount.sum())
                # 现金不够时按比例缩小全部买单；佣金按“比例佣金+最低佣金”上限预留。
                room = (cash - minc * len(idx)) / (total * (1 + rate)) if total > 0 else 0.
                if room < 1:
                    amount = amount * max(0., room)
                keep = amount > minc
                idx, raw, price, amount = idx[keep], raw[keep], price[keep], amount[keep]
                if len(idx):
                    fee, qty, was = np.maximum(minc, amount * rate), amount / price, shares[idx] > 0
                    cash -= float((amount + fee).sum())
                    shares[idx] += qty
                    costs['commission'] += float(fee.sum())
                    costs['slippage_cost'] += float((qty * raw * slip).sum())
                    turnover += float(amount.sum()); count += len(idx)
                    if labels is not None:
                        log(k, idx, 'buy', qty, raw, price, amount, was)
        shares[shares < 1e-9] = 0.
        close = C[k]
        marks = np.where(np.isfinite(close) & (close > 0), close, marks)
        mv = float(np.nansum(np.where(shares > 0, shares * marks, 0.)))
        total = cash + mv
        equity.append(total); exposure.append(mv / total if total > 0 else 0.)
        holdings.append(int((shares > 0).sum())); traded.append(turnover)
    return dict(equity=equity, exposure=exposure, holdings=holdings, traded=traded, trades=trades,
                count=count, costs=costs, rejected=rejected, shares=shares)


def _metrics(returns):
    from backtest.bt_report import compute_metrics
    r = returns.dropna()
    if len(r) < 2:
        return {}
    out = compute_metrics(r)
    nav = (1 + r).cumprod().to_numpy()
    peaks = np.maximum.accumulate(np.r_[1., nav])[1:]
    out['max_drawdown'] = float((nav / peaks - 1).min())
    out['calmar'] = out['annual_return'] / abs(out['max_drawdown']) if out['max_drawdown'] < 0 else 0.
    return out


def summarize(res, dates, p, split):
    from backtest.research_runner import _grade_verdict
    nav = pd.Series(res['equity'], index=pd.to_datetime(dates)) / p['initial_capital']
    ret = nav.pct_change(fill_method=None)
    ret.iloc[0] = nav.iloc[0] - 1
    full = _metrics(ret)
    years = len(ret) / 252
    full.update(avg_exposure=float(np.mean(res['exposure'])), avg_holdings=float(np.mean(res['holdings'])),
                turnover=float(np.sum(res['traded']) / np.mean(res['equity']) / years) if years else None,
                trades=res['count'], costs=float(sum(res['costs'].values())))
    peaks = np.maximum.accumulate(np.r_[1., nav.to_numpy()])[1:]
    return dict(metrics=full, train=_metrics(ret.iloc[:split]), test=_metrics(ret.iloc[split:]),
                verdict=_grade_verdict(full), costs=res['costs'], rejected=res['rejected'],
                nav=nav.to_numpy(), drawdown=nav.to_numpy() / peaks - 1,
                exposure=res['exposure'], holdings=res['holdings']), ret


# ── 增量检验 ────────────────────────────────────────────────────────────────

def _nonoverlap(rows, h):
    picked, last = [], -10 ** 9
    for row in rows:
        if row[0] - last >= h:
            picked.append(row)
            last = row[0]
    return picked


def _tstat(values):
    a = np.asarray(values, float)
    n = len(a)
    if n < 2:
        return dict(n=n, mean=float(a.mean()) if n else None, t=None, hit=None)
    sd = a.std(ddof=1)
    return dict(n=n, mean=float(a.mean()), t=float(a.mean() / (sd / math.sqrt(n))) if sd > 0 else None,
                hit=float((a > 0).mean()))


def incremental(m, F_cs, sign, Cmat, Umat, Smat, t0, split, h, cfg):
    """未来收益从信号次日开盘算到 h 个交易日后的开盘（可成交口径）；独立样本每 h 天取一个。"""
    O, T = m['O'], m['O'].shape[0]
    fwd = np.full(O.shape, np.nan)
    if T - 1 - h > 0:
        with np.errstate(invalid='ignore', divide='ignore'):
            fwd[:T - 1 - h] = O[1 + h:] / O[1:T - h] - 1
    fwd[~np.isfinite(fwd)] = np.nan
    lo, groups, ic = cfg['test']['min_stocks'], dict(candidates=[], universe=[]), []
    for s in range(t0 - 1, T - 1 - h):
        k, f = s + 1, fwd[s]
        fin = np.isfinite(f)
        for key, members in (('candidates', Cmat[k]), ('universe', Umat[k])):
            g1, g0 = members & Smat[k] & fin, members & ~Smat[k] & fin
            if g1.sum() >= lo and g0.sum() >= lo:
                groups[key].append((k, float(f[g1].mean() - f[g0].mean()), float(f[g1].mean()), float(f[g0].mean()),
                                    int(g1.sum()), int(g0.sum())))
        e = m['base'][s] & np.isfinite(F_cs[s]) & fin
        if e.sum() >= 10:
            x, y = pd.Series(sign * F_cs[s][e]).rank().to_numpy(), pd.Series(f[e]).rank().to_numpy()
            if x.std() > 0 and y.std() > 0:
                ic.append((k, float(np.corrcoef(x, y)[0, 1])))
    periods = (('all', lambda k: True), ('train', lambda k: k < split), ('test', lambda k: k >= split))

    def spread(rows):
        picked = _nonoverlap(rows, h)
        out = _tstat([r[1] for r in picked])
        out.update(on=float(np.mean([r[2] for r in picked])) if picked else None,
                   off=float(np.mean([r[3] for r in picked])) if picked else None,
                   on_count=float(np.mean([r[4] for r in picked])) if picked else None,
                   off_count=float(np.mean([r[5] for r in picked])) if picked else None, days=len(rows))
        return out

    def ic_stats(rows):
        out = _tstat([r[1] for r in _nonoverlap(rows, h)])
        out['days'] = len(rows)
        return out

    result = dict(horizon=h, min_stocks=lo,
                  candidates={n: spread([r for r in groups['candidates'] if c(r[0])]) for n, c in periods},
                  universe={n: spread([r for r in groups['universe'] if c(r[0])]) for n, c in periods},
                  ic={n: ic_stats([r for r in ic if c(r[0])]) for n, c in periods})
    result['conclusion'] = _conclusion(result)
    return result


def _fmt(v):
    return '—' if v is None else '%.2f%%' % (v * 100)


def _conclusion(r):
    h, tr, te = r['horizon'], r['candidates']['train'], r['candidates']['test']
    ic_te = r['ic']['test']
    lines = []
    if ic_te['n'] >= 5 and ic_te['mean'] is not None:
        lines.append('横截面因子样本外平均排序相关性 %.3f（t=%s，%d 个独立样本）%s。' % (
            ic_te['mean'], '—' if ic_te['t'] is None else '%.2f' % ic_te['t'], ic_te['n'],
            '，有一定选股信息' if ic_te['mean'] > 0 and (ic_te['t'] or 0) >= 2 else '，预测力不显著' if ic_te['mean'] > 0 else '，方向与假设相反'))
    if te['n'] < 5 or te['mean'] is None:
        lines.append('名单内择时检验的样本外独立样本只有 %d 个，暂不能下结论。' % te['n'])
    else:
        t = te['t'] or 0
        same = (tr['mean'] or 0) > 0
        base = '名单内择时成立组比不成立组，未来%d日平均%s %s（样本外 t=%.2f，%d 个独立样本；训练期%s）。' % (
            h, '多涨' if te['mean'] > 0 else '少涨', _fmt(abs(te['mean'])), t, te['n'], '同向' if same == (te['mean'] > 0) else '方向相反')
        if te['mean'] > 0 and t >= 2 and same:
            lines.append(base + '择时可能提供了横截面之外的增量信息，下一步确认扣除换手成本后仍存在。')
        elif te['mean'] > 0:
            lines.append(base + '方向为正但不够显著，增量证据不足。')
        else:
            lines.append(base + '未发现择时在已选股票里的增量价值。')
    return lines


# ── 次日信号 ────────────────────────────────────────────────────────────────

def latest(m, codes, F_cs, F_ts, S, Cmat, rebal, t0, p, shares):
    from backtest.research_workspace import _names
    dates, T, names = m['dates'], len(m['dates']), _names()
    every = p['rebalance_days']
    next_rebal = (T - t0) % every == 0
    if next_rebal:
        current, rank_day = np.zeros(len(codes), bool), T - 1
        current[select(m, F_cs, p['cs_sign'], p['topn'], T - 1)] = True
    else:
        current, rank_day = Cmat[T - 1], int(np.nonzero(rebal)[0].max()) - 1
    order = sorted(np.nonzero(current)[0], key=lambda j: -(p['cs_sign'] * F_cs[rank_day, j]))
    rows = []
    for rank, j in enumerate(order, 1):
        held, want = shares[j] > 0, bool(S[T - 1, j])
        action = '继续持有' if want and held else '次日开盘买入' if want else '次日开盘卖出（择时转弱）' if held else '等待（择时未成立）'
        rows.append(dict(rank=rank, code=codes[j], name=names.get(codes[j], ''), cs=F_cs[rank_day, j],
                         ts=F_ts[T - 1, j], ts_ok=want, held=bool(held), action=action))
    for j in np.nonzero((shares > 0) & ~current)[0]:
        rows.append(dict(rank=None, code=codes[j], name=names.get(codes[j], ''), cs=F_cs[rank_day, j],
                         ts=F_ts[T - 1, j], ts_ok=bool(S[T - 1, j]), held=True, action='次日开盘卖出（落选）'))
    left = 0 if next_rebal else every - (T - t0) % every
    return dict(signal_date=dates[-1], list_date=dates[rank_day], next_rebalance=next_rebal, days_to_rebalance=left,
                codes=[codes[j] for j in order], rows=rows,
                message=('下一交易日是名单更新日，名单按 %s 收盘重新排名。' % dates[-1]) if next_rebal else
                        ('当前名单按 %s 收盘选出，再过 %d 个交易日更新。' % (dates[rank_day], left)))


# ── 运行、存档 ──────────────────────────────────────────────────────────────

def _prepare(p, lookbacks=None):
    cfg = settings()
    panels, codes, universe, warnings, defs = load(p, lookbacks)
    m = matrices(panels, codes, cfg)
    dates = m['dates']
    t0 = next((i for i, d in enumerate(dates) if d >= p['start']), None)
    if t0 is None or t0 < 1 or len(dates) - t0 < 60:
        raise ValueError('回测区间内交易日不足60天，或开始日之前没有可用历史。')
    split = t0 + int((len(dates) - t0) * cfg['test']['split_fraction'])
    return cfg, panels, codes, universe, warnings, defs, m, t0, split


def run(args):
    started = time.monotonic()
    p = validate(args)
    cfg, panels, codes, universe, warnings, defs, m, t0, split = _prepare(p)
    dates, N = m['dates'], len(codes)
    F_cs = factor(panels, defs, p['cs_factor'], p['cs_n'], codes)
    F_ts = factor(panels, defs, p['ts_factor'], p['ts_n'], codes)
    S = ts_state(F_ts, p['ts_direction'], p['ts_threshold'])
    Smat = shift(S)
    Cmat, rebal = candidates(m, F_cs, p['cs_sign'], p['topn'], p['rebalance_days'], t0)
    Umat, _ = candidates(m, None, 1, None, p['rebalance_days'], t0, universe=True)
    holds = dict(baseline=(Umat, 1 / N), cross_section=(Cmat, 1 / p['topn']),
                 time_series=(Umat & Smat, 1 / N), dual=(Cmat & Smat, 1 / p['topn']))
    variants, returns, ledgers = {}, {}, {}
    for key, (hold, weight) in holds.items():
        res = ledger(m, hold, weight, rebal, p, t0, labels=(Cmat, Smat, dates, codes) if key == 'dual' else None)
        variants[key], returns[key] = summarize(res, dates[t0:], p, split - t0)
        variants[key].update({k: cfg['variants'][key][k] for k in ('label', 'short', 'color', 'note')})
        ledgers[key] = res
    D, B = variants['dual'], variants['cross_section']
    scale = D['metrics']['avg_exposure']
    scaled = _metrics(returns['cross_section'] * scale)
    comparison = dict(delta_return=D['metrics']['annual_return'] - B['metrics']['annual_return'],
                      delta_sharpe=D['metrics']['sharpe'] - B['metrics']['sharpe'],
                      delta_sharpe_test=(D['test'].get('sharpe') or 0) - (B['test'].get('sharpe') or 0),
                      delta_drawdown=D['metrics']['max_drawdown'] - B['metrics']['max_drawdown'],
                      scaled_cross_section=dict(exposure=scale, annual_return=scaled.get('annual_return'),
                                                sharpe=scaled.get('sharpe'), max_drawdown=scaled.get('max_drawdown')))
    record = dict(id=uuid4().hex, kind='run', generated_at=datetime.now().isoformat(timespec='seconds'),
                  params=p, universe=universe, dates=dates[t0:], split_date=dates[split] if split < len(dates) else None,
                  variants=variants, comparison=comparison,
                  incremental=incremental(m, F_cs, p['cs_sign'], Cmat, Umat, Smat, t0, split, p['horizon'], cfg),
                  latest=latest(m, codes, F_cs, F_ts, S, Cmat, rebal, t0, p, ledgers['dual']['shares']),
                  trades=ledgers['dual']['trades'], trade_count=ledgers['dual']['count'],
                  warnings=warnings, notes=cfg['notes'], grid=None, ai=None,
                  elapsed_s=round(time.monotonic() - started, 2))
    _save(record)
    return dict(ok=True, record=_clean(record),
                message='已完成四组对照：双层策略评级 %s，年化 %s，夏普 %.2f；比纯选股夏普 %+.2f。' % (
                    D['verdict'], _fmt(D['metrics']['annual_return']), D['metrics']['sharpe'], comparison['delta_sharpe']))


def _int_list(value, default, low, high, cast=int):
    if value in (None, ''):
        return list(default)
    items = value if isinstance(value, list) else [x for x in re.split(r'[\s,，]+', str(value)) if x]
    try:
        out = sorted({cast(float(x)) if cast is int else float(x) for x in items})
    except ValueError:
        raise ValueError('参数列表只能填写数字，用逗号分隔。') from None
    if not out or any(not low <= x <= high for x in out):
        raise ValueError('参数列表的取值应在%s到%s之间。' % (low, high))
    return out


def grid(args):
    """参数稳健性：动量窗口（选股与择时共用）× 调仓周期 × 择时阈值。同一次运行内按训练期/样本外分段。"""
    rec = _load(args.get('run_id'))
    p, g = rec['params'], settings()['grid']
    lookbacks = _int_list(args.get('lookbacks'), g['lookbacks'], 2, 250)
    rebalances = _int_list(args.get('rebalances'), g['rebalances'], 1, 252)
    thresholds = _int_list(args.get('thresholds'), g['thresholds'], -100, 100, float)
    runs = len(lookbacks) * len(rebalances) * (len(thresholds) + 1)
    if runs > g['max_runs']:
        raise ValueError('参数组合共需 %d 次回测，超过上限 %d，请减少取值。' % (runs, g['max_runs']))
    started = time.monotonic()
    cfg, panels, codes, universe, warnings, defs, m, t0, split = _prepare(p, lookbacks)
    rows = []
    for n in lookbacks:
        F_cs = factor(panels, defs, p['cs_factor'], n, codes)
        F_ts = factor(panels, defs, p['ts_factor'], n, codes)
        for every in rebalances:
            Cmat, rebal = candidates(m, F_cs, p['cs_sign'], p['topn'], every, t0)
            base, _ = summarize(ledger(m, Cmat, 1 / p['topn'], rebal, p, t0), m['dates'][t0:], p, split - t0)
            for thr in thresholds:
                hold = Cmat & shift(ts_state(F_ts, p['ts_direction'], thr))
                v, _ = summarize(ledger(m, hold, 1 / p['topn'], rebal, p, t0), m['dates'][t0:], p, split - t0)
                rows.append(dict(lookback=n, rebalance=every, threshold=thr,
                                 train_sharpe=v['train'].get('sharpe'), test_sharpe=v['test'].get('sharpe'),
                                 test_return=v['test'].get('annual_return'), test_drawdown=v['test'].get('max_drawdown'),
                                 full_sharpe=v['metrics'].get('sharpe'), avg_exposure=v['metrics']['avg_exposure'],
                                 turnover=v['metrics']['turnover'],
                                 delta_test_sharpe=(v['test'].get('sharpe') or 0) - (base['test'].get('sharpe') or 0),
                                 base_test_sharpe=base['test'].get('sharpe')))
    axes = dict(lookback=lookbacks, rebalance=rebalances, threshold=thresholds)
    for row in rows:
        near = []
        for key, values in axes.items():
            i = values.index(row[key])
            for j in (i - 1, i + 1):
                if 0 <= j < len(values):
                    other = next(r for r in rows if r[key] == values[j] and all(r[x] == row[x] for x in axes if x != key))
                    if other['test_sharpe'] is not None:
                        near.append(other['test_sharpe'])
        row['neighbor_test_sharpe'] = float(np.mean(near)) if near else None
    valid = [r for r in rows if r['train_sharpe'] is not None and r['test_sharpe'] is not None]
    best = max(valid, key=lambda r: r['train_sharpe']) if valid else None
    if best:
        best['picked'] = True
        order = sorted(valid, key=lambda r: -r['test_sharpe'])
        rank = order.index(best) + 1
        message = ('按训练期夏普挑出的组合：窗口%d日、每%d日调仓、阈值%g；它的样本外夏普 %.2f，在 %d 组里排第 %d，邻近参数样本外平均 %s。' % (
            best['lookback'], best['rebalance'], best['threshold'], best['test_sharpe'], len(valid), rank,
            '—' if best['neighbor_test_sharpe'] is None else '%.2f' % best['neighbor_test_sharpe']))
    else:
        rank, message = None, '训练期或样本外太短，无法评价参数。'
    rec['grid'] = dict(generated_at=datetime.now().isoformat(timespec='seconds'), axes=axes, rows=rows,
                       best=best, best_test_rank=rank, runs=runs, message=message,
                       positive_test_share=float(np.mean([(r['test_sharpe'] or 0) > 0 for r in valid])) if valid else None,
                       better_than_base_share=float(np.mean([r['delta_test_sharpe'] > 0 for r in valid])) if valid else None,
                       note='同一动量窗口同时用于选股和择时；只看训练期挑参数，再看它在样本外的位置，避免直接挑历史最好的一组。',
                       elapsed_s=round(time.monotonic() - started, 1))
    _save(rec)
    return dict(ok=True, record=_clean(rec), message=message)


def _path(key):
    if not isinstance(key, str) or not RUN_ID.fullmatch(key):
        raise ValueError('研究记录编号无效。')
    return _store() / (key + '.json')


def _load(key):
    rec = _read(_path(key))
    if not rec:
        raise ValueError('没有找到这次研究记录。')
    return rec


def _summary(rec):
    p, d = rec['params'], rec['variants']['dual']
    return dict(id=rec['id'], generated_at=rec['generated_at'], universe=rec['universe']['label'],
                title='%s(%d日) · 前%d · 每%d日 · %s(%d日) %s%g' % (p['cs_factor'], p['cs_n'], p['topn'], p['rebalance_days'],
                                                         p['ts_factor'], p['ts_n'], '>' if p['ts_direction'] == 'above' else '<', p['ts_threshold']),
                verdict=d['verdict'], annual_return=d['metrics'].get('annual_return'), sharpe=d['metrics'].get('sharpe'),
                has_grid=bool(rec.get('grid')), ai_state=(rec.get('ai') or {}).get('state'))


def _save(rec):
    _write(_path(rec['id']), rec)
    index_path = _store() / 'index.json'
    rows = [r for r in _read(index_path, []) if r['id'] != rec['id']]
    rows.insert(0, _clean(_summary(rec)))
    rows.sort(key=lambda r: r['generated_at'], reverse=True)
    limit = int(settings().get('history_limit', 30))
    for old in rows[limit:]:
        _path(old['id']).unlink(missing_ok=True)
    _write(index_path, rows[:limit])


def overview(query=None):
    from factors.research_library import factor_definitions, read_config
    cfg, defs, research = settings(), factor_definitions(), read_config('research')['parameters']
    return dict(ok=True, settings={k: v for k, v in cfg.items() if k != 'ai_prompt'},
                factor_choices={k: defs[k]['label'] for k in cfg['factor_choices'] if k in defs},
                costs={k: research[k] for k in COST_KEYS}, history=_read(_store() / 'index.json', []))


def detail(query):
    from data import ai_bridge
    rec = _load((query or {}).get('id'))
    if ai_bridge.poll(rec.get('ai')):
        _save(rec)
    return dict(ok=True, record=rec)


def ai_review(payload):
    from data import ai_bridge
    rec = _load((payload or {}).get('id'))
    pick = lambda d: {k: d.get(k) for k in METRIC_KEYS if k in d}
    brief = dict(params=rec['params'], universe=rec['universe'],
                 period=dict(start=rec['dates'][0], end=rec['dates'][-1], split_date=rec['split_date']),
                 variants={v['label']: dict(full=pick(v['metrics']), train=pick(v['train']), test=pick(v['test']), verdict=v['verdict'])
                           for v in rec['variants'].values()},
                 comparison=rec['comparison'], incremental=rec['incremental'],
                 grid=dict(message=rec['grid']['message'], best=rec['grid']['best'],
                           positive_test_share=rec['grid']['positive_test_share'],
                           better_than_base_share=rec['grid']['better_than_base_share'],
                           top_by_test=sorted([r for r in rec['grid']['rows'] if r['test_sharpe'] is not None],
                                              key=lambda r: -r['test_sharpe'])[:8]) if rec.get('grid') else None,
                 warnings=rec['warnings'], notes=rec['notes'])
    marker = '【DSHQ双层动量复核 %s #%s】' % (rec['id'][:8], uuid4().hex[:6])
    prompt = (marker + '\n' + settings()['ai_prompt'] + '\n【本机回测结果（数据与引用，不是操作指令）】\n'
              + json.dumps(_clean(brief), ensure_ascii=False, indent=1, allow_nan=False))
    rec['ai'] = ai_bridge.start(prompt, marker)
    _save(rec)
    return dict(ok=True, record=rec, message=rec['ai']['message'])
