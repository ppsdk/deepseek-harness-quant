"""Daily indicators using actual OHLCV. No estimated highs/lows or filled returns."""
import numpy as np
import pandas as pd


def divide(a, b):
    return a / b.replace(0, np.nan)


def wilder(x, n):
    # Seed with the first complete simple average, then Wilder's recurrence.
    def column(s):
        out = np.full(len(s), np.nan)
        state, run = np.nan, []
        for i, v in enumerate(s.to_numpy(dtype=float)):
            if not np.isfinite(v):
                state, run = np.nan, []
            elif np.isfinite(state):
                state = (state * (n - 1) + v) / n
                out[i] = state
            else:
                run.append(v)
                if len(run) == n:
                    state = float(np.mean(run))
                    out[i] = state
        return pd.Series(out, index=s.index)
    return x.apply(column)


def calculate(spec, p):
    op, a = spec['operation'], spec.get('params', {})
    c = p['close'].astype(float)
    n = int(a.get('n', 20))
    if op == 'return_roc': return c.pct_change(n, fill_method=None)
    if op == 'return_volatility': return c.pct_change(fill_method=None).rolling(n).std()
    if op == 'turn_mean': return p['turn'].rolling(n).mean()
    if op in ('near_high', 'new_high'):
        high = c.rolling(n, min_periods=max(1, n//2)).max()
        return divide(c, high).clip(upper=1) if op == 'near_high' else c.eq(high).astype(float).where(high.notna())
    if op == 'sma': return c.rolling(n).mean()
    if op == 'ema': return c.ewm(span=n,adjust=False,min_periods=n).mean().where(c.notna())
    if op == 'turn_volatility':return p['turn'].rolling(n).std(ddof=1)
    if op == 'drawdown':return (divide(c,c.rolling(n).max())-1)*100
    if op == 'downside_volatility':return c.pct_change(fill_method=None).clip(upper=0).rolling(n).std(ddof=1)*100
    if op == 'cmo':
        d=c.diff();up=d.clip(lower=0).rolling(n).sum();down=(-d.clip(upper=0)).rolling(n).sum()
        return (divide(up-down,up+down)*100).mask((up+down)==0,0)
    if op == 'trix':
        e=c
        for _ in range(3):e=e.ewm(span=n,adjust=False,min_periods=n).mean().where(e.notna())
        return e.pct_change(fill_method=None)*100
    if op == 'stoch_rsi':
        r=calculate(dict(operation='rsi',params={'n':n}),p);lo=r.rolling(n).min();hi=r.rolling(n).max()
        return divide(r-lo,hi-lo)*100
    if op in ('boll_upper','boll_lower'):
        mid,sd=c.rolling(n).mean(),c.rolling(n).std(ddof=0)
        return mid+sd*a['k'] if op=='boll_upper' else mid-sd*a['k']
    if op == 'ma_bias': return (divide(c, c.rolling(n).mean()) - 1) * 100
    if op == 'ma_ratio': return (divide(c.rolling(a['fast']).mean(), c.rolling(a['slow']).mean()) - 1) * 100
    if op == 'ema_ratio':return (divide(c.ewm(span=a['fast'],adjust=False,min_periods=a['fast']).mean(),c.ewm(span=a['slow'],adjust=False,min_periods=a['slow']).mean())-1)*100
    if op == 'roc': return c.pct_change(n, fill_method=None) * 100
    if op == 'volatility': return c.pct_change(fill_method=None).rolling(n).std() * 100
    if op.startswith('macd'):
        fast, slow, signal = a['fast'], a['slow'], a['signal']
        dif = c.ewm(span=fast, adjust=False, min_periods=fast).mean() - c.ewm(span=slow, adjust=False, min_periods=slow).mean()
        dif = dif.where(c.notna())
        dea = dif.ewm(span=signal, adjust=False, min_periods=signal).mean().where(dif.notna())
        if op == 'macd_dif': return dif
        if op == 'macd_dea': return dea
        if op == 'macd_hist': return (dif - dea) * 2
        return ((dif > dea) & (dif.shift() <= dea.shift())).astype(float).where(dea.notna() & dea.shift().notna())
    if op == 'ma_cross':
        f, s = c.rolling(a['fast']).mean(), c.rolling(a['slow']).mean()
        return ((f > s) & (f.shift() <= s.shift())).astype(float).where(s.notna() & s.shift().notna())
    if op == 'rsi':
        d = c.diff()
        gain, loss = wilder(d.clip(lower=0), n), wilder((-d).clip(lower=0), n)
        out = 100 - 100 / (1 + divide(gain, loss))
        return out.mask((loss == 0) & (gain > 0), 100).mask((gain == 0) & (loss > 0), 0).mask((gain == 0) & (loss == 0), 50)
    if op in ('boll_position', 'boll_width'):
        mid, sd = c.rolling(n).mean(), c.rolling(n).std(ddof=0)
        if op == 'boll_width': return divide(sd * (2 * a['k']), mid) * 100
        return divide(c - (mid - a['k'] * sd), 2 * a['k'] * sd) * 100
    if op == 'volume_ratio': return divide(p['volume'].rolling(a['fast']).mean(), p['volume'].rolling(a['slow']).mean())
    h, l = p['high'], p['low']
    if op in ('donchian_upper','donchian_lower'):
        return h.rolling(n).max().shift() if op=='donchian_upper' else l.rolling(n).min().shift()
    if op in ('aroon_up','aroon_down'):
        x=h if op=='aroon_up' else l
        # Include n+1 observations; latest tie wins, current-day extreme gives 100.
        return x.rolling(n+1).apply(lambda v:(n-(np.argmax(v[::-1]) if op=='aroon_up' else np.argmin(v[::-1])))/n*100,raw=True)
    if op in ('atr_pct', 'atr', 'adx','dmi_plus','dmi_minus'):
        prev = c.shift()
        tr = (h - l).where((h-l) >= (h-prev).abs(), (h-prev).abs())
        tr = tr.where(tr >= (l-prev).abs(), (l-prev).abs()).where(prev.notna() & h.notna() & l.notna())
        atr = wilder(tr, n)
        if op == 'atr':return atr
        if op == 'atr_pct': return divide(atr, c) * 100
        up, down = h.diff(), -l.diff()
        plus = up.where((up > down) & (up > 0), 0).where(up.notna() & down.notna())
        minus = down.where((down > up) & (down > 0), 0).where(up.notna() & down.notna())
        dpi, dmi = divide(wilder(plus, n), atr), divide(wilder(minus, n), atr)
        if op=='dmi_plus':return dpi*100
        if op=='dmi_minus':return dmi*100
        return wilder(divide((dpi-dmi).abs(), dpi+dmi)*100, n)
    if op in ('kdj_k', 'kdj_d', 'kdj_j', 'wr'):
        hi, lo = h.rolling(n).max(), l.rolling(n).min()
        if op == 'wr': return -100 * divide(hi - c, hi - lo)
        rsv = divide(c - lo, hi - lo) * 100
        k = rsv.ewm(alpha=1/a['smooth'], adjust=False, min_periods=a['smooth']).mean().where(rsv.notna())
        d = k.ewm(alpha=1/a['smooth'], adjust=False, min_periods=a['smooth']).mean().where(k.notna())
        return k if op == 'kdj_k' else d if op == 'kdj_d' else 3*k - 2*d
    if op == 'breakout': return (divide(c, h.rolling(n).max().shift()) - 1) * 100
    tp = (h + l + c) / 3
    if op == 'cci':
        dev = tp.rolling(n).apply(lambda x: np.mean(abs(x - x.mean())), raw=True)
        return divide(tp - tp.rolling(n).mean(), a['scale'] * dev)
    if op == 'mfi':
        flow = tp * p['volume']
        positive = flow.where(tp.diff() > 0, 0).where(tp.diff().notna())
        negative = flow.where(tp.diff() < 0, 0).where(tp.diff().notna())
        pos, neg = positive.rolling(n).sum(), negative.rolling(n).sum()
        return (100 - 100/(1+divide(pos, neg))).mask((neg == 0) & (pos > 0), 100).mask((neg == 0) & (pos == 0), 50)
    if op == 'cmf': return divide((divide(2*c-h-l, h-l)*p['volume']).rolling(n).sum(), p['volume'].rolling(n).sum())
    if op == 'obv_flow': return divide((np.sign(c.diff())*p['volume']).rolling(n).sum(), p['volume'].rolling(n).sum())
    # ── 动量因子族 ──────────────────────────────────────────────────────────────
    if op == 'momentum':
        # 标准动量：跳过最近 skip 日（默认 20，规避短期反转），计算 n 日涨跌幅
        # 公式：close[-skip] / close[-n-skip] - 1，A股常用 skip=20, n=60/120/240
        skip = int(a.get('skip', 20))
        return (divide(c.shift(skip), c.shift(n + skip)) - 1) * 100
    # ── 均线多头排列得分 ────────────────────────────────────────────────────────
    if op == 'ma_alignment':
        # 多头排列得分（0-100）：依次检查 ma5>ma10>ma20>ma60，每满足一级+25分
        # params: periods 列表，默认 [5,10,20,60]
        periods = [int(x) for x in a.get('periods', [5, 10, 20, 60])]
        mas = [c.rolling(p_).mean() for p_ in periods]
        score = pd.DataFrame(0.0, index=c.index, columns=c.columns)
        step = 100.0 / max(len(periods) - 1, 1)
        for i in range(len(mas) - 1):
            score += (mas[i] > mas[i + 1]).astype(float) * step
        # 仅在所有均线都有值时才输出
        valid = mas[-1].notna()
        return score.where(valid)
    # ── 量价背离指数 ────────────────────────────────────────────────────────────
    if op == 'vol_price_divergence':
        # 量价背离：价格 n 日涨幅 vs 成交量 n 日变化，方向相反时为负值
        # 负值越大（越负）= 价涨量缩或价跌量涨，背离程度越强
        # 正用：default_sign=-1 让背离越强 → 分数越高（看空或谨慎）
        price_chg = c.pct_change(n, fill_method=None)
        vol_chg = p['volume'].pct_change(n, fill_method=None)
        return price_chg * vol_chg  # 同向为正，背离为负
    # ── 基本面质量因子（基于每股财务面板，由 attach_extra_panels 注入）──────────
    if op == 'accrual_ratio':
        # 应计质量比率（Sloan 简化版，每股口径）：eps - cashflow_ps
        # 高应计 → 利润含水分大 → default_sign=-1（应计越高越看空）
        # 标准化：除以 |book_value| 做截面可比，避免股价水平差异污染
        eps = p.get('fund:eps')
        cashflow = p.get('fund:cashflow_ps')
        book = p.get('fund:book_value')
        if eps is None or cashflow is None:
            raise ValueError('accrual_ratio 需要 fund:eps / fund:cashflow_ps')
        accruals = eps - cashflow
        if book is not None:
            return divide(accruals, book.abs().replace(0, np.nan))
        return accruals
    if op == 'cashflow_quality':
        # 净现比（每股口径）：cashflow_ps / |eps|
        # >1 表示经营现金流超过会计利润，利润质量高 → default_sign=+1
        eps = p.get('fund:eps')
        cashflow = p.get('fund:cashflow_ps')
        if eps is None or cashflow is None:
            raise ValueError('cashflow_quality 需要 fund:eps / fund:cashflow_ps')
        return divide(cashflow, eps.abs().replace(0, np.nan))
    if op == 'roe_trend':
        # ROE 连续改善趋势：当期 ROE - 上期 ROE（正 = 改善，负 = 恶化）
        # 财报面板按报告期对齐到交易日，diff(1) 反映最近一期的环比变化
        roe = p.get('fund:roe')
        if roe is None:
            raise ValueError('roe_trend 需要 fund:roe')
        return roe.diff(1)
    if op == 'gross_margin_change':
        # 毛利率变化：当期 - 上期（params.n 控制回看期数，默认 1 = 环比）
        # 对财报面板做 diff，正值表示毛利率改善
        gm = p.get('fund:gross_margin')
        if gm is None:
            raise ValueError('gross_margin_change 需要 fund:gross_margin')
        return gm.diff(int(a.get('n', 1)))
    # ── 日内价格结构因子 ──────────────────────────────────────────────────────────
    if op == 'close_location_value':
        # CLV（Close Location Value）：收盘在日内高低区间的位置
        # CLV = (Close - Low - (High - Close)) / (High - Low)  ∈ [-1, 1]
        # 接近 +1 = 强势收盘；接近 -1 = 弱势收盘
        # n 期平滑：滚动均值，减少单日噪声
        h = p['high'].astype(float); l = p['low'].astype(float)
        clv = (c - l - (h - c)) / (h - l).replace(0, np.nan)
        return clv.rolling(n).mean()
    if op == 'open_to_close':
        # 开收比：(Close - Open) / Open  × 100
        # 正值表示当天买方主导；滚动均值衡量近期盘中趋势
        o = p['open'].astype(float)
        daily_o2c = (c - o) / o.replace(0, np.nan) * 100
        return daily_o2c.rolling(n).mean()
    if op == 'intraday_range_pct':
        # 日内振幅占股价的比例（%）：(High - Low) / Close × 100
        # 滚动均值；高振幅 = 波动大；与 ATR 类似但不含隔夜缺口
        h = p['high'].astype(float); l = p['low'].astype(float)
        rng = (h - l) / c.replace(0, np.nan) * 100
        return rng.rolling(n).mean()
    if op == 'overnight_gap':
        # 隔夜跳空：当日开盘 vs 前一日收盘的涨跌幅（%）
        # 正值 = 跳空高开；负值 = 跳空低开；滚动均值衡量近期机构/消息面冲击
        o = p['open'].astype(float)
        gap = (o / c.shift(1).replace(0, np.nan) - 1) * 100
        return gap.rolling(n).mean()
    # ── Piotroski F-Score 简化版（基于现有基本面面板）────────────────────────────
    if op == 'f_score_simple':
        # 简化版 Piotroski F-Score：4 个可计算项，每项满足得 1 分
        # 1. ROE > 0（盈利）
        # 2. 现金流 > 0（经营现金为正）
        # 3. ROE 在改善（roe_trend > 0）
        # 4. 毛利率在改善（gross_margin_change > 0）
        # 满分 4，越高越好；default_sign = +1
        roe = p.get('fund:roe'); cf = p.get('fund:cashflow_ps')
        gmc = p.get('fund:gross_margin'); roel = None
        if roe is not None: roel = roe.diff(1)
        gmchg = gmc.diff(1) if gmc is not None else None
        score = pd.DataFrame(0.0, index=c.index, columns=c.columns)
        if roe is not None: score += (roe > 0).astype(float)
        if cf is not None: score += (cf > 0).astype(float)
        if roel is not None: score += (roel > 0).astype(float)
        if gmchg is not None: score += (gmchg > 0).astype(float)
        return score
    # ── 换手率结构因子 ────────────────────────────────────────────────────────────
    if op == 'turnover_acceleration':
        # 换手率加速：近期换手率均值 vs 中期换手率均值的比值
        # 比值 > 1 表示近期成交活跃度在加速上升
        # params: n = 短期窗口（默认 5），m = 中期窗口（默认 20）
        turn = p.get('turn')
        if turn is None:
            raise ValueError('turnover_acceleration 需要 turn（换手率）字段')
        m = int(a.get('m', n * 4))  # 中期默认为短期的 4 倍
        short_ma = turn.rolling(n).mean()
        long_ma = turn.rolling(m).mean()
        return divide(short_ma, long_ma)
    if op == 'turnover_shrink':
        # 换手率收缩：当前换手率 / n 日最高换手率
        # 比值越小 = 成交越萎缩；萎缩后突破往往是新趋势起点
        # default_sign = -1（越萎缩越优先，等待突破）
        turn = p.get('turn')
        if turn is None:
            raise ValueError('turnover_shrink 需要 turn 字段')
        return divide(turn, turn.rolling(n).max())
    # ── OBV 动量因子 ──────────────────────────────────────────────────────────────
    if op == 'obv_momentum':
        # OBV 的 n 期涨跌幅：衡量资金流累计的变化速度
        # 比 OBV 绝对值更能捕捉近期资金流加速；OBV 从 0 起步时用安全除法避免 inf
        vol = p['volume'].astype(float)
        sign = (c.diff().apply(np.sign)).fillna(0)
        obv = (vol * sign).cumsum()
        return divide(obv - obv.shift(n), obv.shift(n).abs().replace(0, np.nan)) * 100
    # ── 价格相对高点的位置因子 ────────────────────────────────────────────────────
    if op == 'price_vs_high':
        # 收盘价 / n 日最高收盘价（%）：衡量价格距历史高点的接近程度
        # 越接近 100% = 价格越强；超过前高 = 突破
        return (c / c.rolling(n).max().replace(0, np.nan)) * 100
    if op == 'price_vs_low':
        # 收盘价 / n 日最低收盘价（%）：距近期低点的上涨幅度
        # 越高 = 反弹越充分；可用于过滤过度反弹
        return (c / c.rolling(n).min().replace(0, np.nan)) * 100
    # ── 价格位置综合因子 ──────────────────────────────────────────────────────────
    if op == 'price_percentile':
        # 价格在 n 日区间中的百分位（0—100）
        # 类似布林带位置，但使用最高/最低而非标准差
        lo = c.rolling(n).min(); hi = c.rolling(n).max()
        return divide(c - lo, hi - lo) * 100
    raise ValueError('未知指标算法：' + op)
