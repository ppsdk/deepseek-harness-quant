"""Portfolio sizing and daily-close risk exits, executed on the next available open."""
import numpy as np
import pandas as pd


def capped_weights(raw, budget, cap):
    raw = pd.Series(raw, dtype=float)
    raw = raw.where(np.isfinite(raw) & raw.gt(0)).dropna()
    result = raw * 0.
    remaining = min(float(budget), len(raw) * cap)
    active = list(raw.index)
    while active and remaining > 1e-12:
        proposal = raw.loc[active] / raw.loc[active].sum() * remaining
        capped = proposal[proposal > cap + 1e-12].index.tolist()
        if not capped:
            result.loc[active] = proposal
            break
        result.loc[capped] = cap
        remaining -= len(capped) * cap
        active = [code for code in active if code not in capped]
    return result


def weights(panels, codes, signal_day, p):
    if not codes:
        return {}
    exposure = p.get('target_exposure_pct', 100) / 100
    cap = p.get('max_position_pct', 100) / 100
    # Vacant candidate slots remain cash, matching the existing equal-weight ledger.
    budget = exposure * len(codes) / p['topn']
    raw = pd.Series(1., index=codes)
    if p.get('allocation', 'equal') == 'inverse_volatility':
        n = p.get('allocation_lookback', 20)
        cache = panels.setdefault('_allocation_volatility', {})
        if n not in cache:
            cache[n] = panels['close'].pct_change(fill_method=None).rolling(n).std()
        raw = 1 / cache[n].loc[signal_day, codes].replace(0, np.nan)
        raw = raw.where(np.isfinite(raw) & raw.gt(0)).dropna()
        budget = exposure * len(raw) / p['topn']
    return capped_weights(raw, budget, cap).to_dict()


def simulate(panels, score, p):
    from backtest.research_runner import rank_candidates
    close = panels['close']
    cash = float(p['initial_capital'])
    shares, marks, basis, peaks, entered, pending, cooldown = {}, {}, {}, {}, {}, {}, {}
    trades, rejected, equity, account = [], [], [], []
    dates = close.index[(close.index >= p['start']) & (close.index <= p['end'])]
    stale_days = 0
    realized = {}
    hf = p.get('holding_filter')
    hf_panel = panels.get('_holding_filter') if hf else None
    hf_min = hf.get('min') if hf else None
    hf_max = hf.get('max') if hf else None
    hf_active = hf_panel is not None
    cached_ranked = pd.Series(dtype=float)
    for day_number, day in enumerate(dates):
        pos = close.index.get_loc(day)
        opening = panels['open'].loc[day]
        signal_day = close.index[pos - 1] if pos > 0 else None
        if signal_day is not None:
            # ── daily holding filter (time-series gating) ──────────────────
            if hf_active:
                for code in list(shares):
                    if code in pending:
                        continue
                    try:
                        val = hf_panel.at[signal_day, code]
                    except KeyError:
                        val = float('nan')
                    if not np.isfinite(val):
                        continue
                    fail = False
                    if hf_min is not None and val < hf_min:
                        fail = True
                    if hf_max is not None and val > hf_max:
                        fail = True
                    if fail:
                        pending[code] = dict(trigger='holding_filter', signal_date=signal_day)
            for code in shares:
                if code in pending:
                    continue
                price = close.at[signal_day, code]
                if not np.isfinite(price) or price <= 0:
                    continue
                gain = price / basis[code] - 1
                trigger = None
                if '_exit' in panels and pd.notna(panels['_exit'].at[signal_day,code]) and panels['_exit'].at[signal_day,code]>0:
                    trigger = 'technical_exit'
                elif p.get('stop_loss_pct', 0) and gain <= -p['stop_loss_pct']/100:
                    trigger = 'stop_loss'
                elif p.get('take_profit_pct', 0) and gain >= p['take_profit_pct']/100:
                    trigger = 'take_profit'
                elif p.get('trailing_stop_pct', 0) and np.isfinite(peaks[code]) and price / peaks[code] - 1 <= -p['trailing_stop_pct']/100:
                    trigger = 'trailing_stop'
                elif p.get('max_holding_days', 0) and pos - entered[code] >= p['max_holding_days']:
                    trigger = 'max_holding_days'
                if trigger:
                    pending[code] = dict(trigger=trigger, signal_date=signal_day)
        market_exit = signal_day is not None and '_timing' in panels and p.get('timing_execution') == 'daily_exit' and not panels['_timing'].loc[signal_day].eq(1).all()
        signal_mode='_entry' in panels
        rebalance = day_number % p['rebalance_days'] == 0 or signal_mode
        if (rebalance or market_exit or pending) and signal_day is not None:
            blocked = set(pending) | {c for c, until in cooldown.items() if pos < until}
            ranked = rank_candidates(panels, score, signal_day, len(close.columns)) if rebalance and not market_exit else pd.Series(dtype=float)
            targets = [c for c in ranked.index if c not in blocked][:p['topn']]
            if signal_mode:
                targets=[c for c in ranked.index if c not in blocked and c not in shares
                         and pd.notna(panels['_entry'].at[signal_day,c]) and panels['_entry'].at[signal_day,c]>0
                         and pd.notna(panels['_exit'].at[signal_day,c]) and panels['_exit'].at[signal_day,c]<=0][:max(0,p['topn']-len(shares))]
            open_equity = cash + sum(q * (float(opening.get(c)) if pd.notna(opening.get(c)) and opening[c] > 0 else marks[c]) for c, q in shares.items())
            allocation = weights(panels, targets, signal_day, p)
            if rebalance or market_exit:
                desired = {c: open_equity * w / float(opening[c]) for c, w in allocation.items()
                           if pd.notna(opening.get(c)) and opening[c] > 0}
            else:
                # A daily risk exit must not rebalance unrelated positions.
                desired = dict(shares)
            if signal_mode and not market_exit:
                desired={**shares,**{c:open_equity*w/float(opening[c]) for c,w in allocation.items()
                         if pd.notna(opening.get(c)) and opening[c]>0}}
            for code in pending:
                desired.pop(code, None)

            def tradable(code):
                vals = [panels[f].at[day, code] for f in ('open', 'high', 'low', 'close', 'volume')]
                if any(not np.isfinite(v) or v <= 0 for v in vals):
                    return 'missing_or_suspended'
                if np.isclose(vals[1], vals[2], rtol=1e-8, atol=1e-8):
                    return 'one_price_bar'
                return None

            for side in ('sell', 'buy'):
                candidates = sorted(shares) if side == 'sell' else targets
                for code in candidates:
                    held = shares.get(code, 0.)
                    quantity = held - desired.get(code, 0.) if side == 'sell' else desired.get(code, 0.) - held
                    if quantity <= 1e-8:
                        continue
                    trigger = pending.get(code, {}).get('trigger') or ('market_risk_exit' if market_exit else 'technical_entry' if signal_mode and side=='buy' else 'rebalance')
                    if trigger == 'rebalance' and held > 0 and desired.get(code, 0) > 0 and pd.notna(opening.get(code)):
                        if quantity * opening[code] / open_equity * 100 < p.get('rebalance_threshold_pct', 0):
                            continue
                    reason = tradable(code)
                    if reason:
                        rejected.append(dict(date=day, code=code, side=side, reason=reason, trigger=trigger))
                        continue
                    raw = float(opening[code])
                    price = raw * (1 + (1 if side == 'buy' else -1) * p['slippage_bps'] / 10000)
                    rate = p['commission_bps'] / 10000
                    if side == 'buy':
                        affordable = min(cash / (price * (1 + rate)), max(0, cash - p['min_commission']) / price)
                        invested = sum(q*(float(opening[c]) if pd.notna(opening.get(c)) and opening[c]>0 else marks[c]) for c,q in shares.items())
                        room = max(0.,open_equity*p.get('target_exposure_pct',100)/100-invested)
                        quantity = min(quantity, affordable, room/raw)
                    if quantity <= 1e-8:
                        continue
                    amount = quantity * price
                    commission = max(p['min_commission'], amount * rate)
                    tax = amount * p['stamp_tax_bps'] / 10000 if side == 'sell' else 0.
                    if side == 'sell' and amount <= commission + tax:
                        continue
                    cash += amount - commission - tax if side == 'sell' else -(amount + commission)
                    after = held + (quantity if side == 'buy' else -quantity)
                    trade_signal = pending.get(code, {}).get('signal_date', signal_day)
                    if side == 'buy':
                        basis[code] = (held * basis.get(code, 0) + amount + commission) / after
                        if held <= 1e-8:
                            entered[code] = pos
                            peaks[code] = np.nan  # Updated only from actual valid closes below.
                    else:
                        realized[code] = realized.get(code, 0.) + amount - commission - tax - quantity * basis[code]
                    if after < 1e-8:
                        shares.pop(code, None)
                        if code in pending:
                            cooldown[code] = pos + p.get('risk_cooldown_days', 3) + 1
                        pending.pop(code, None)
                        for ledger in (basis, peaks, entered):
                            ledger.pop(code, None)
                    else:
                        shares[code] = after
                    marks[code] = raw
                    trades.append(dict(date=day, signal_date=trade_signal, code=code, side=side,
                                       trigger=trigger, quantity=quantity, position_before=held, position_after=max(0., after),
                                       open=raw, fill_price=price, amount=amount, commission=commission,
                                       stamp_tax=tax, slippage_cost=quantity*abs(price-raw), cash_after=cash))
        stale = False
        for code in shares:
            price = close.at[day, code]
            if pd.notna(price) and price > 0:
                marks[code] = float(price)
                peaks[code] = max(peaks[code], float(price)) if np.isfinite(peaks[code]) else float(price)
            else:
                stale = True
        stale_days += int(stale)
        market_value = sum(q * marks[c] for c, q in shares.items())
        total = cash + market_value
        equity.append(total)
        account.append(dict(date=day, cash=cash, market_value=market_value, equity=total,
                            exposure=market_value/total if total > 0 else None,
                            holding_count=len(shares), stale_valuation=stale))
    nav = pd.Series(equity, index=pd.to_datetime(dates), dtype=float) / p['initial_capital']
    returns = nav.pct_change(fill_method=None)
    if len(nav):
        returns.iloc[0] = nav.iloc[0] - 1
    final_equity = equity[-1] if equity else cash
    details = [dict(code=c, quantity=q, cost_price=basis[c], mark_price=marks[c],
                    market_value=q*marks[c], weight=q*marks[c]/final_equity if final_equity else 0.,
                    unrealized_pnl=q*(marks[c]-basis[c]), realized_pnl=realized.get(c, 0.),
                    holding_days=close.index.get_loc(dates[-1])-entered[c]+1,
                    pending_exit=pending.get(c)) for c, q in shares.items()]
    return dict(nav_series=nav, returns=returns, trades=trades, rejections=rejected,
                cash=cash, positions=shares, position_details=details, realized_pnl=realized,
                stale_valuation_days=stale_days, daily_account=account)
