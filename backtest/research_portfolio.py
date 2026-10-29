"""Portfolio sizing and causal, cadence-aware trade execution."""
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
    # Vacant candidate slots remain cash, matching the equal-weight ledger.
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
    """Run the research ledger.

    A fixed-cadence target portfolio is frozen until every executable order
    has either filled or become provably unfillable. Ordinary trading days are
    separated by ``min_trade_interval_days``. A market-risk exit may bypass
    that spacing rule, while the per-day traded-symbol cap always applies.
    """
    from backtest.research_runner import rank_candidates

    close = panels['close']
    opening_frame = panels['open']
    cash = float(p['initial_capital'])
    shares, marks, basis, peaks, entered = {}, {}, {}, {}, {}
    pending, cooldown = {}, {}
    trades, rejected, equity, account = [], [], [], []
    dates = close.index[(close.index >= p['start']) & (close.index <= p['end'])]
    stale_days = 0
    realized = {}

    hf = p.get('holding_filter')
    hf_panel = panels.get('_holding_filter') if hf else None
    hf_min = hf.get('min') if hf else None
    hf_max = hf.get('max') if hf else None
    hf_active = hf_panel is not None

    event_mode = p.get('trade_mode', 'fixed') == 'event'
    signal_mode = '_entry' in panels
    max_codes = max(1, int(p.get('max_traded_codes_per_day', p['topn'])))
    min_gap = max(1, int(p.get('min_trade_interval_days', p.get('rebalance_days', 1))))
    last_trade_pos = None
    active_plan = None
    emergency_exit = False
    emergency_signal_date = None
    position_eps = 1e-8

    def tradable(day, code):
        try:
            values = [panels[field].at[day, code]
                      for field in ('open', 'high', 'low', 'close', 'volume')]
        except KeyError:
            return 'missing_or_suspended'
        if any(not np.isfinite(value) or value <= 0 for value in values):
            return 'missing_or_suspended'
        if np.isclose(values[1], values[2], rtol=1e-8, atol=1e-8):
            return 'one_price_bar'
        return None

    def average_cost(held, old_basis, amount, commission, after):
        return (held * old_basis + amount + commission) / after

    def remove_plan_target(code):
        nonlocal active_plan
        if active_plan is None or code not in active_plan['targets']:
            return
        active_plan['targets'].remove(code)
        active_plan['weights'].pop(code, None)
        active_plan['quantities'].pop(code, None)

    def initialize_plan_quantities(plan, open_equity, opening):
        for code in plan['targets']:
            if code in plan['quantities']:
                continue
            price = opening.get(code)
            weight = plan['weights'].get(code, 0.)
            if weight <= 0:
                plan['quantities'][code] = 0.
            elif pd.notna(price) and price > 0:
                plan['quantities'][code] = open_equity * float(weight) / float(price)

    def plan_is_complete(plan):
        target_set = set(plan['targets'])
        if any(code not in target_set for code in shares):
            return False
        if any(code in pending for code in shares):
            return False
        for code in plan['targets']:
            if code not in plan['quantities']:
                return False
            target_quantity = plan['quantities'][code]
            held_quantity = shares.get(code, 0.)
            tolerance = max(position_eps, abs(target_quantity) * 1e-7)
            if abs(held_quantity - target_quantity) > tolerance:
                return False
        return True

    def accept_partial_target(code, quantity):
        if active_plan is not None and code in active_plan['targets']:
            active_plan['quantities'][code] = quantity

    for day_number, day in enumerate(dates):
        pos = close.index.get_loc(day)
        spacing_ok = last_trade_pos is None or pos - last_trade_pos >= min_gap
        trade_count_before = len(trades)
        traded_codes = set()
        opening = opening_frame.loc[day]
        signal_day = close.index[pos - 1] if pos > 0 else None

        if signal_day is None:
            pass
        else:
            # Time-series holding filters and individual exits are decided on
            # the prior close, then remain queued until an eligible open.
            for code in list(shares):
                if code in pending:
                    continue
                if hf_active:
                    try:
                        value = hf_panel.at[signal_day, code]
                    except KeyError:
                        value = float('nan')
                    if np.isfinite(value) and (
                            (hf_min is not None and value < hf_min) or
                            (hf_max is not None and value > hf_max)):
                        pending[code] = dict(trigger='holding_filter',
                                             signal_date=signal_day)
                        remove_plan_target(code)
                        continue

                price = close.at[signal_day, code]
                if not np.isfinite(price) or price <= 0:
                    continue
                gain = price / basis[code] - 1
                trigger = None
                if ('_exit' in panels and
                        pd.notna(panels['_exit'].at[signal_day, code]) and
                        panels['_exit'].at[signal_day, code] > 0):
                    trigger = 'technical_exit'
                elif p.get('stop_loss_pct', 0) and gain <= -p['stop_loss_pct'] / 100:
                    trigger = 'stop_loss'
                elif p.get('take_profit_pct', 0) and gain >= p['take_profit_pct'] / 100:
                    trigger = 'take_profit'
                elif (p.get('trailing_stop_pct', 0) and
                      np.isfinite(peaks.get(code, np.nan)) and
                      price / peaks[code] - 1 <= -p['trailing_stop_pct'] / 100):
                    trigger = 'trailing_stop'
                elif (p.get('max_holding_days', 0) and
                      pos - entered[code] >= p['max_holding_days']):
                    trigger = 'max_holding_days'
                if trigger:
                    pending[code] = dict(trigger=trigger, signal_date=signal_day)
                    remove_plan_target(code)

            timing_risk = (
                '_timing' in panels and
                p.get('timing_execution') == 'daily_exit' and
                not panels['_timing'].loc[signal_day].eq(1).all()
            )
            if timing_risk:
                emergency_exit = True
                emergency_signal_date = emergency_signal_date or signal_day
                active_plan = None
                pending.clear()

            targets = []
            desired = {}
            execute_this_day = False

            if emergency_exit:
                execute_this_day = True
                desired = {}
            else:
                normal_due = spacing_ok
                blocked = set(pending) | {
                    code for code, until in cooldown.items() if pos < until
                }

                # A fixed-frequency strategy freezes one target portfolio.
                # Unfilled orders continue on later eligible trading days.
                if (not event_mode and not signal_mode and active_plan is None and
                        normal_due and
                        day_number % int(p['rebalance_days']) == 0):
                    ranked = rank_candidates(
                        panels, score, signal_day, len(close.columns))
                    targets = [code for code in ranked.index
                               if code not in blocked][:p['topn']]
                    if panels.get('_selection_policy'):
                        from backtest.research_selection import choose
                        targets = choose(
                            panels, ranked, shares, signal_day, p['topn'],
                            panels['_selection_policy'], blocked)
                    allocation = weights(panels, targets, signal_day, p)
                    active_plan = dict(
                        signal_date=signal_day,
                        targets=list(targets),
                        weights=dict(allocation),
                        quantities={},
                    )

                direct_due = normal_due and (event_mode or signal_mode)
                should_rank = False
                if direct_due:
                    if event_mode:
                        should_rank = (
                            not signal_mode or
                            len(shares) < p['topn'] or
                            bool(pending)
                        )
                    else:
                        should_rank = True

                if active_plan is not None:
                    targets = list(active_plan['targets'])
                    allocation = dict(active_plan['weights'])
                elif should_rank:
                    ranked = rank_candidates(
                        panels, score, signal_day, len(close.columns))
                    targets = [code for code in ranked.index
                               if code not in blocked][:p['topn']]
                    if panels.get('_selection_policy'):
                        from backtest.research_selection import choose
                        targets = choose(
                            panels, ranked, shares, signal_day, p['topn'],
                            panels['_selection_policy'], blocked)
                    if signal_mode:
                        entry_candidates = (
                            targets if panels.get('_selection_policy') else ranked.index)
                        slots = max(0, p['topn'] - len(shares))
                        targets = [
                            code for code in entry_candidates
                            if code not in blocked and code not in shares and
                            pd.notna(panels['_entry'].at[signal_day, code]) and
                            panels['_entry'].at[signal_day, code] > 0 and
                            pd.notna(panels['_exit'].at[signal_day, code]) and
                            panels['_exit'].at[signal_day, code] <= 0
                        ][:slots]
                    allocation = weights(panels, targets, signal_day, p)
                else:
                    targets = []
                    allocation = {}

                open_equity = cash + sum(
                    quantity * (
                        float(opening.get(code))
                        if pd.notna(opening.get(code)) and opening[code] > 0
                        else marks[code]
                    )
                    for code, quantity in shares.items()
                )

                if active_plan is not None:
                    initialize_plan_quantities(active_plan, open_equity, opening)
                    desired = dict(active_plan['quantities'])
                    targets = list(active_plan['targets'])
                else:
                    # A queued individual exit is not a full rebalance. Keep
                    # every other holding as the execution baseline so that
                    # selling one name cannot accidentally liquidate the book.
                    desired = dict(shares) if pending and not should_rank else {}
                    for code, weight in allocation.items():
                        price = opening.get(code)
                        if pd.notna(price) and price > 0 and weight > 0:
                            desired[code] = open_equity * float(weight) / float(price)

                if signal_mode:
                    # Technical entries add positions; existing positions stay
                    # until the holding filter or an exit signal says otherwise.
                    desired = {**shares, **desired}
                for code in pending:
                    desired.pop(code, None)

                execute_this_day = (
                    normal_due and
                    (bool(targets) or bool(pending) or active_plan is not None)
                )

            if execute_this_day:
                sell_intents, buy_intents = [], []

                for code in sorted(
                        shares,
                        key=lambda item: (0 if item in pending else 1, item)):
                    held = shares[code]
                    target_quantity = desired.get(code, 0.)
                    quantity = held - target_quantity
                    if code in pending:
                        sell_intents.append((code, held))
                    elif quantity > position_eps:
                        sell_intents.append((code, quantity))

                target_order = [] if emergency_exit else list(targets)
                if active_plan is not None and not emergency_exit:
                    target_order = list(active_plan['targets'])
                for code in target_order:
                    held = shares.get(code, 0.)
                    wanted = desired.get(code)
                    if wanted is None:
                        continue
                    quantity = wanted - held
                    if quantity > position_eps:
                        buy_intents.append((code, quantity))

                for side, intents in (('sell', sell_intents), ('buy', buy_intents)):
                    for code, quantity in intents:
                        held = shares.get(code, 0.)

                        if code not in traded_codes and len(traded_codes) >= max_codes:
                            rejected.append(dict(
                                date=day, code=code, side=side,
                                reason='daily_symbol_limit',
                                trigger=(
                                    'market_risk_exit' if emergency_exit else
                                    pending.get(code, {}).get('trigger') if code in pending else
                                    'technical_entry' if signal_mode and side == 'buy' else
                                    'rebalance_entry' if side == 'buy' else
                                    'rebalance_trim' if code in targets else
                                    'rebalance_exit'
                                ),
                            ))
                            continue

                        trigger = (
                            'market_risk_exit' if emergency_exit else
                            pending[code]['trigger'] if code in pending else
                            'technical_entry' if signal_mode and side == 'buy' else
                            'rebalance_entry' if side == 'buy' else
                            'rebalance_trim' if code in targets else
                            'rebalance_exit'
                        )

                        if (side == 'buy' and held <= position_eps and
                                len(shares) >= p['topn']):
                            rejected.append(dict(
                                date=day, code=code, side=side,
                                reason='holding_limit', trigger=trigger))
                            continue

                        if (side == 'sell' and code in targets and held > 0 and
                                pd.notna(opening.get(code))):
                            proportion = quantity * opening[code] / open_equity * 100
                            if proportion < p.get('rebalance_threshold_pct', 0):
                                accept_partial_target(code, held)
                                continue

                        reason = tradable(day, code)
                        if reason:
                            rejected.append(dict(
                                date=day, code=code, side=side,
                                reason=reason, trigger=trigger))
                            continue

                        raw = float(opening[code])
                        side_multiplier = 1 if side == 'buy' else -1
                        price = raw * (
                            1 + side_multiplier * p['slippage_bps'] / 10000)
                        rate = p['commission_bps'] / 10000
                        requested_quantity = quantity
                        partial_fill = False

                        if side == 'buy':
                            affordable = min(
                                cash / (price * (1 + rate)),
                                max(0., cash - p['min_commission']) / price,
                            )
                            invested = sum(
                                quantity_held * (
                                    float(opening[c])
                                    if pd.notna(opening.get(c)) and opening[c] > 0
                                    else marks[c]
                                )
                                for c, quantity_held in shares.items()
                            )
                            room = max(
                                0.,
                                open_equity *
                                p.get('target_exposure_pct', 100) / 100 -
                                invested,
                            )
                            quantity = min(quantity, affordable, room / raw)
                            partial_fill = (
                                quantity + position_eps < requested_quantity)

                        if quantity <= position_eps:
                            if side == 'buy':
                                rejected.append(dict(
                                    date=day, code=code, side=side,
                                    reason='insufficient_cash',
                                    trigger=trigger))
                                if active_plan is not None:
                                    accept_partial_target(code, held)
                            continue

                        amount = quantity * price
                        commission = max(p['min_commission'], amount * rate)
                        tax = (
                            amount * p['stamp_tax_bps'] / 10000
                            if side == 'sell' else 0.
                        )
                        if side == 'sell' and amount <= commission + tax:
                            if active_plan is not None:
                                accept_partial_target(code, held)
                            continue

                        if side == 'sell':
                            cash += amount - commission - tax
                        else:
                            cash -= amount + commission
                        after = held + (quantity if side == 'buy' else -quantity)

                        if emergency_exit:
                            trade_signal = emergency_signal_date or signal_day
                        elif code in pending:
                            trade_signal = pending[code]['signal_date']
                        elif active_plan is not None:
                            trade_signal = active_plan['signal_date']
                        else:
                            trade_signal = signal_day

                        if side == 'buy':
                            basis[code] = average_cost(
                                held, basis.get(code, 0.), amount,
                                commission, after)
                            if held <= position_eps:
                                entered[code] = pos
                                peaks[code] = np.nan
                        else:
                            realized[code] = (
                                realized.get(code, 0.) + amount - commission -
                                tax - quantity * basis[code]
                            )

                        if after < position_eps:
                            shares.pop(code, None)
                            if code in pending:
                                cooldown[code] = (
                                    pos + p.get('risk_cooldown_days', 3) + 1)
                                pending.pop(code, None)
                            for ledger in (basis, peaks, entered):
                                ledger.pop(code, None)
                        else:
                            shares[code] = after

                        marks[code] = raw
                        trades.append(dict(
                            date=day, signal_date=trade_signal, code=code,
                            side=side, trigger=trigger, quantity=quantity,
                            position_before=held,
                            position_after=max(0., after),
                            open=raw, fill_price=price, amount=amount,
                            commission=commission, stamp_tax=tax,
                            slippage_cost=quantity * abs(price - raw),
                            cash_after=cash,
                        ))
                        traded_codes.add(code)

                        if (side == 'buy' and partial_fill and
                                active_plan is not None):
                            # Fees or the exposure cap make the remainder
                            # unfillable within this frozen rebalance.
                            accept_partial_target(code, after)

                if (active_plan is not None and
                        plan_is_complete(active_plan)):
                    active_plan = None
                if emergency_exit and not shares:
                    emergency_exit = False
                    emergency_signal_date = None

        if len(trades) > trade_count_before:
            last_trade_pos = pos

        stale = False
        for code in shares:
            price = close.at[day, code]
            if pd.notna(price) and price > 0:
                marks[code] = float(price)
                peaks[code] = (
                    max(peaks[code], float(price))
                    if np.isfinite(peaks.get(code, np.nan))
                    else float(price)
                )
            else:
                stale = True
        stale_days += int(stale)
        market_value = sum(
            quantity * marks[code] for code, quantity in shares.items())
        total = cash + market_value
        equity.append(total)
        account.append(dict(
            date=day, cash=cash, market_value=market_value, equity=total,
            exposure=market_value / total if total > 0 else None,
            holding_count=len(shares), stale_valuation=stale,
        ))

    nav = pd.Series(
        equity, index=pd.to_datetime(dates), dtype=float
    ) / p['initial_capital']
    returns = nav.pct_change(fill_method=None)
    if len(nav):
        returns.iloc[0] = nav.iloc[0] - 1
    final_equity = equity[-1] if equity else cash
    details = [dict(
        code=code, quantity=quantity, cost_price=basis[code],
        mark_price=marks[code], market_value=quantity * marks[code],
        weight=quantity * marks[code] / final_equity if final_equity else 0.,
        unrealized_pnl=quantity * (marks[code] - basis[code]),
        realized_pnl=realized.get(code, 0.),
        holding_days=close.index.get_loc(dates[-1]) - entered[code] + 1,
        pending_exit=pending.get(code),
    ) for code, quantity in shares.items()]
    return dict(
        nav_series=nav, returns=returns, trades=trades, rejections=rejected,
        cash=cash, positions=shares, position_details=details,
        realized_pnl=realized, stale_valuation_days=stale_days,
        daily_account=account,
    )
