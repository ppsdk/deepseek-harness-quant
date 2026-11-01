"""Dividend timing signal service; all fills come from the shared simulator."""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from factors.research_library import read_config
from data.research_dividend import load_data, load_cash_actions, panels_for, summarize
from backtest.research_dividend_timing import causal_signal_panels, reversal_signals
from backtest.research_portfolio import simulate

ROOT = Path(__file__).resolve().parents[1]


def run_candidate(frame, code, group, candidate, period, base, actions, stress=False):
    panels = panels_for(frame, code)
    signal_panels = causal_signal_panels(panels, actions)
    adapters, indicators = reversal_signals(signal_panels, candidate['variant'], candidate['settings'])
    known = indicators.get('known', indicators['trend'].notna() & indicators['d'].notna())
    valid = known.index[known.iloc[:, 0] & (known.index >= period['start']) & (known.index <= period['end'])]
    if not len(valid):
        raise ValueError(f'反转研究没有充分预热日期：{code}/{candidate["id"]}')
    panels.update(adapters)
    panels['_execution_price_basis'] = 'raw'
    panels['_cash_actions'] = actions
    params = {**base['execution'], **candidate['risk'], **period,
              'start': str(valid[0]), 'stamp_tax_bps': base['groups'][group]['stamp_tax_bps'],
              'rebalance_days': 1, 'min_trade_interval_days': candidate['settings']['min_trade_interval_days']}
    if stress:
        params.update(base['stress'])
    sim = simulate(panels, panels['close'] * 0. + 1., params)
    return dict(code=code, group=group, candidate=candidate['id'], variant=candidate['variant'],
                actual_start=params['start'], params=params, period=period, stress=stress,
                metrics=summarize(sim, params['initial_capital']), daily_account=sim['daily_account'],
                trades=sim['trades'], rejections=sim['rejections'],
                cash_distributions=sim['cash_distributions'],
                returns=[dict(date=str(d)[:10], ret=float(v)) for d, v in sim['returns'].items()])



def _iso(value, name):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError(name + ' 必须是 YYYY-MM-DD 日期。') from None
    return value


def _number(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def _profile(cfg, ident):
    if not isinstance(ident, str) or ident not in cfg['profiles']:
        raise ValueError('未知红利信号配置：' + str(ident))
    profile = deepcopy(cfg['profiles'][ident])
    profile['id'] = ident
    settings = profile['settings']
    variant = profile['variant']
    if variant == 'kdj_filtered':
        from backtest.research_kdj_timing import validate
        validate(settings)
    elif variant == 'kdj':
        for key in ('kdj_period', 'kdj_smooth', 'setup_lookback', 'confirmation_lookback',
                    'trend_period', 'trend_confirm', 'bollinger_period'):
            value = settings[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(key + ' 必须是正整数。')
        for key in ('oversold', 'overbought', 'bollinger_k', 'low_position', 'high_position'):
            value = settings[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
                raise ValueError(key + ' 必须是有限数值。')
        if not 0 <= settings['oversold'] < settings['overbought'] <= 100:
            raise ValueError('KDJ 超卖阈值必须低于超买阈值，且都在0至100之间。')
        if settings['bollinger_k'] <= 0 or not 0 <= settings['low_position'] < settings['high_position'] <= 1:
            raise ValueError('布林辅助计算参数无效。')
    else:
        raise ValueError('红利信号仅支持已实现的 kdj / kdj_filtered。')
    interval = settings.get('min_trade_interval_days')
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
        raise ValueError('min_trade_interval_days 必须是正整数。')
    allowed = {'stop_loss_pct', 'take_profit_pct', 'trailing_stop_pct',
               'max_holding_days', 'risk_cooldown_days'}
    for key, value in profile.get('risk', {}).items():
        if key not in allowed or isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value < 0:
            raise ValueError('不支持的风险参数：' + key)
        if key.endswith('_days') and not isinstance(value, int):
            raise ValueError(key + ' 必须是整数。')
    return profile


def describe_day(code, day, panels, adapters, indicators, account, labels):
    """Explain adapter outputs without inventing another recommendation rule.

    Holding state comes only from the execution ledger. Conditional decisions
    are also returned for a human whose actual holding differs from the model.
    """
    entry = _number(adapters['_entry'].at[day, code])
    exit_signal = _number(adapters['_exit'].at[day, code])
    known = entry is not None and exit_signal is not None
    held = bool(account and account['market_value'] > 0)
    def flag(key):
        frame = indicators.get(key)
        return frame is not None and pd.notna(frame.at[day, code]) and bool(frame.at[day, code])
    close = panels['close'].at[day, code]
    ma = indicators.get('exit_ma', indicators['trend']).at[day, code]
    weakening = bool(known and pd.notna(ma) and close < ma)
    if not known:
        flat_state = held_state = 'insufficient'
    else:
        flat_state = 'buy' if entry == 1 and exit_signal != 1 else 'setup' if flag('recent_low') and exit_signal != 1 else 'wait'
        held_state = 'sell' if exit_signal == 1 else 'weakening' if weakening else 'hold'
    state = held_state if held else flat_state
    reasons = []
    if entry == 1:
        reasons.append('kdj_low_reversal')
    if exit_signal == 1:
        if flag('high_exit'): reasons.append('kdj_high_cross_down')
        if flag('trend_exit'): reasons.append('ma_trend_exit')
    if state == 'setup': reasons.append('oversold_waiting_confirmation')
    if state == 'weakening': reasons.append('below_exit_ma_unconfirmed')
    if state == 'insufficient': reasons.append('indicator_inputs_unknown')
    k = _number(indicators['k'].at[day, code]); d = _number(indicators['d'].at[day, code])
    return dict(date=day, state=state, label=labels[state], if_flat=flat_state, if_held=held_state,
                entry=entry, exit=exit_signal, held=held, reasons=reasons,
                k=k, d=d, j=3*k-2*d if k is not None and d is not None else None,
                recent_low=flag('recent_low'), recent_high=flag('recent_high'),
                cross_up=flag('cross_up'), cross_down=flag('cross_down'),
                trend_filter=flag('trend_filter') if 'trend_filter' in indicators else None,
                volume_ratio=_number(indicators['volume_ratio'].at[day, code]) if 'volume_ratio' in indicators else None)


def _coverage(cfg, code, end):
    path = ROOT / cfg['action_coverage_file']
    row = json.loads(path.read_text(encoding='utf-8')).get('assets', {}).get(code, {}) if path.exists() else {}
    verified = row.get('verified_through')
    if verified:
        _iso(verified, 'verified_through')
    # A recent file timestamp or a last event date is not a completeness audit.
    complete = bool(verified and verified >= end and row.get('sources'))
    return dict(verified_through=verified, complete=complete, sources=row.get('sources', []))


def evaluate(query=None):
    """Read-only API/CLI evaluation; never downloads bars or places an order."""
    from data.research_quality import closed_day, local_calendar
    query = query or {}
    allowed = {'code', 'profile', 'start', 'end', 'history'}
    if set(query) - allowed:
        raise ValueError('未知红利信号参数：' + ','.join(sorted(set(query) - allowed)))
    history = query.get('history', False)
    if not isinstance(history, (str, bool)) or history not in (True, False, 'true', 'false', '1', '0'):
        raise ValueError('history 使用 true / false。')
    history = history is True or history in ('true', '1')
    cfg = read_config('dividend_signals'); base = read_config(cfg['base_config'])
    ident = query.get('profile') or cfg['default_profile']; profile = _profile(cfg, ident)
    code = query.get('code') or cfg['default_code']
    if code not in cfg['assets']:
        raise ValueError('证券不在红利信号配置的观察范围内。')
    group = next((g for g, spec in base['groups'].items() if code in spec['assets']), None)
    if group is None:
        raise ValueError('证券缺少基础资产配置：' + code)
    end = _iso(query.get('end') or closed_day(), 'end')
    start = _iso(query.get('start') or cfg['start'], 'start')
    if start > end or end > closed_day():
        raise ValueError('日期必须满足 start <= end <= 最近已收盘日期。')
    if start < base['data_start']:
        raise ValueError('开始日期早于配置的数据预热起点。')
    # Narrow the existing loader to this asset, retaining all cache conventions.
    inputs = deepcopy(base)
    inputs['groups'] = {group: {**base['groups'][group], 'assets': {code: base['groups'][group]['assets'][code]}}}
    inputs['data_end'] = end
    frames, audit = load_data(inputs, price_basis='none')
    frame = frames[code].loc[lambda x: x.index <= end]
    prices = frame[['open', 'high', 'low', 'close']].to_numpy(float)
    if not np.isfinite(prices).all():
        raise ValueError('原始OHLC含缺失或无穷值，不能生成完整的交易信号。')
    if len(frame) > cfg['max_history_rows']:
        raise ValueError('历史长度超过配置上限，请调整数据起点或 max_history_rows。')
    actions = load_cash_actions(inputs, code, group)
    panels = panels_for(frame, code)
    signal_panels = causal_signal_panels(panels, actions)
    adapters, indicators = reversal_signals(signal_panels, profile['variant'], profile['settings'])
    period = dict(start=start, end=end)
    run = run_candidate(frame, code, group, profile, period, base, actions)
    stress = run_candidate(frame, code, group, profile, period, base, actions, stress=True)
    control = {**panels, '_entry': panels['close']*0+1, '_exit': panels['close']*0,
               '_execution_price_basis': 'raw', '_cash_actions': actions}
    control_params = {**run['params'], 'stop_loss_pct': 0, 'take_profit_pct': 0,
                      'trailing_stop_pct': 0, 'max_holding_days': 0, 'risk_cooldown_days': 0}
    buy_hold = simulate(control, panels['close']*0+1, control_params)
    accounts = {row['date']: row for row in run['daily_account']}
    trades = {}; events = []
    for trade in run['trades']:
        trades.setdefault(trade['date'], []).append(trade)
    scale = signal_panels['close'][code] / frame['close']
    exit_ma = indicators.get('exit_ma', indicators['trend'])[code] / scale
    timeline = []
    for day, account in accounts.items():
        row = describe_day(code, day, signal_panels, adapters, indicators, account, cfg['states'])
        row.update(raw_close=_number(frame.at[day, 'close']), exit_ma_raw=_number(exit_ma.at[day]),
                   equity=account['equity'], cash=account['cash'], exposure=account['exposure'],
                   market_value=account['market_value'], dividend_receivable=account['dividend_receivable'],
                   fills=trades.get(day, []))
        timeline.append(row)
        # Only actionable model transitions become events; recurring exits while
        # already flat are still inspectable in history, not repeated sell calls.
        if row['state'] in ('buy', 'sell'):
            events.append(row.copy())
    if not timeline:
        raise ValueError('指定区间内没有完成预热的回测账户。')
    latest = timeline[-1]
    calendar, calendar_source = local_calendar(str(frame.index[0]), end)
    calendar_verified = bool(calendar and not any(s in calendar_source for s in ('可能尚未更新', '缺少交易日历')))
    expected = calendar[-1] if calendar_verified else None
    stale = latest['date'] < expected if expected else None
    missing = sorted(set(d for d in calendar if run['actual_start'] <= d <= latest['date']) - set(frame.index))
    coverage = _coverage(cfg, code, latest['date'])
    last_volume = _number(frame.at[latest['date'], 'volume'])
    last_bar_traded = last_volume is not None and last_volume > 0
    warnings = []
    if stale: warnings.append('行情落后于参考交易日历；历史信号不能作为当前委托依据。')
    if stale is None: warnings.append('缺少完整交易日历，无法确认行情是否已更新。')
    if missing: warnings.append('参考交易日缺少行情；可能停牌或数据缺口，尚未核实，不补造价格。')
    if not coverage['complete']: warnings.append('公司行为完整性尚未核验；已计入现有事件，不能声称分红/拆分无遗漏。')
    if not last_bar_traded: warnings.append('末日成交量缺失或为零，不应把该日条件作为可执行信号。')
    warnings.append('实验信号，历史区间已被观察；未通过全新样本外验证。')
    schedule, _ = local_calendar(latest['date'], (pd.Timestamp(latest['date']) + pd.Timedelta(days=30)).strftime('%Y-%m-%d'))
    next_session = next((d for d in schedule if d > latest['date']), None)
    effective = dict(profile=profile, execution=run['params'], signal_price_basis='causal_total_return_ohlc',
                     fill_price_basis='raw', conflict_policy='exit_first')
    fingerprint = hashlib.sha256(json.dumps(effective, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    input_hash = hashlib.sha256(json.dumps(dict(prices=audit[code]['hash'], actions=actions), sort_keys=True).encode()).hexdigest()
    run_hash = hashlib.sha256((fingerprint + input_hash).encode()).hexdigest()
    result = dict(ok=True, schema_version=cfg['schema_version'], code=code,
                  name=base['groups'][group]['assets'][code], profile=ident,
                  generated_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                  requested_end=end, data_end=latest['date'], actual_start=run['actual_start'],
                  latest=latest, next_scheduled_session=next_session,
                  execution_note='收盘确认后下一交易日开盘尝试成交；受现金、费用、风险冷却和可成交条件约束。模型持仓不是用户实际持仓。',
                  quality=dict(expected_session=expected, calendar_source=calendar_source,
                               stale=stale, missing_sessions=missing, cash_actions=coverage,
                               last_bar_traded=last_bar_traded,
                               data_ready=stale is False and not missing and coverage['complete'] and last_bar_traded and latest['state'] != 'insufficient',
                               research_status=profile['research_status']),
                  effective=effective, config_hash=fingerprint, input_hash=input_hash, run_hash=run_hash,
                  input_audit=audit[code], input_actions=actions,
                  metrics=run['metrics'], stress_metrics=stress['metrics'],
                  buy_hold_metrics=summarize(buy_hold, control_params['initial_capital']),
                  trades=run['trades'], rejections=run['rejections'], cash_distributions=run['cash_distributions'],
                  recent_events=events[-cfg['recent_events']:], warnings=warnings)
    if history:
        result.update(history=timeline, bars=[dict(date=day, **{f: _number(frame.at[day, f]) for f in ('open','high','low','close','volume')}) for day in accounts])
    return result
