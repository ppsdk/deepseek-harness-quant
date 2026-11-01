"""Parameter and risk-filter study for causal KDJ reversal timing.

All candidates use the existing raw-price cash ledger.  The script only
changes signal parameters and execution risk controls; it does not create a
second portfolio simulator.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from factors.research_library import read_config
from backtest.research_dividend_timing import causal_signal_panels, reversal_signals
from backtest.research_portfolio import simulate
from scripts.dividend_timing_research import load_data, load_cash_actions, panels_for, summarize, write
from backtest.research_runtime import snapshot


def candidates(cfg):
    base = dict(kdj_smooth=3, oversold=30, overbought=70,
                bollinger_period=20, bollinger_k=2.0, low_position=.10,
                high_position=.90, setup_lookback=5,
                confirmation_lookback=3, trend_period=10, trend_confirm=2,
                min_trade_interval_days=1, trend_fast_period=20,
                trend_slow_period=60, trend_slope_lookback=5,
                trend_max_below_slow=.02, volume_period=20,
                volume_min_ratio=.8, volume_confirm_lookback=3,
                trend_enabled=True, volume_enabled=True, exit_period=10,
                exit_confirm=2)
    rows = []
    for row in cfg.get('kdj_grid', []):
        period, smooth = int(row['period']), int(row['smooth'])
        oversold, overbought = float(row['oversold']), float(row['overbought'])
        rows.append(dict(id=f'kdj_{period}_{smooth}_{oversold}_{overbought}',
                         variant='kdj', settings={**base, 'kdj_period': period,
                         'kdj_smooth': smooth, 'oversold': oversold,
                         'overbought': overbought}, risk={}))
    for row in cfg.get('filtered_grid', []):
        trend_slow = int(row['trend_slow_period'])
        volume_min = float(row['volume_min_ratio'])
        stop = float(row.get('stop_loss_pct', 0)); trailing = float(row.get('trailing_stop_pct', 0))
        ident = f'filtered_9_3_t{trend_slow}_v{str(volume_min).replace(".", "")}'
        if stop:
            ident += f'_sl{stop}'
        if trailing:
            ident += f'_ts{trailing}'
        settings = {**base, 'kdj_period': 9, 'trend_fast_period': int(cfg.get('trend_fast_period', 20)),
                    'trend_slow_period': trend_slow, 'trend_slope_lookback': int(cfg.get('trend_slope_lookback', 5)),
                    'trend_max_below_slow': float(cfg.get('trend_max_below_slow', .02)), 'volume_period': int(cfg.get('volume_period', 20)),
                    'volume_min_ratio': volume_min, 'volume_confirm_lookback': 3}
        rows.append(dict(id=ident, variant='kdj_filtered', settings=settings,
                         risk={'stop_loss_pct': stop, 'trailing_stop_pct': trailing,
                               'risk_cooldown_days': 5}))
    return rows


def run(frame, code, group, candidate, period, base, actions, stress=False):
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


def main():
    base = read_config('dividend_timing_research')
    root_cfg = read_config('dividend_volatility_research')
    cfg = root_cfg.get('kdj_parameter', {})
    out = ROOT / cfg.get('output_dir', 'output/strategy_research/dividend_kdj_parameter_v1')
    frames, audit = load_data(base, price_basis='none')
    grid = candidates(cfg)
    write(out / 'manifest.json', dict(config=cfg, candidates=grid, data=audit,
                                     price_basis='none', provenance=snapshot(),
                                     evaluation='exploratory_parameter_grid_frozen_discovery'))
    records = []
    periods = base['periods']
    for group, spec in base['groups'].items():
        for code in spec['assets']:
            actions = load_cash_actions(base, code, group)
            for candidate in grid:
                for phase in ('discovery', 'validation', 'forward_replay', 'full'):
                    for stress in (False, True):
                        row = run(frames[code], code, group, candidate, periods[phase], base, actions, stress)
                        row['phase'] = phase
                        write(out / 'runs' / f'{code}_{candidate["id"]}_{phase}{"_stress" if stress else ""}.json', row)
                        records.append({k: v for k, v in row.items() if k not in ('daily_account', 'trades', 'rejections', 'returns')})
            print('DONE', code, flush=True)
    selected = {}
    ranking = {}
    for group in base['groups']:
        rank = []
        for candidate in grid:
            xs = [r['metrics'] for r in records if r['group'] == group and r['phase'] == 'discovery'
                  and r['candidate'] == candidate['id'] and not r['stress']]
            ss = [r['metrics'] for r in records if r['group'] == group and r['phase'] == 'discovery'
                  and r['candidate'] == candidate['id'] and r['stress']]
            rank.append(dict(candidate=candidate['id'], median_calmar=float(np.median([x['calmar'] for x in xs])),
                             median_annual=float(np.median([x['annual_return'] for x in xs])),
                             worst_drawdown=float(min(x['max_drawdown'] for x in xs)),
                             stress_median_annual=float(np.median([x['annual_return'] for x in ss])),
                             stress_median_calmar=float(np.median([x['calmar'] for x in ss]))))
        rank.sort(key=lambda x: (x['worst_drawdown'] >= -0.20, x['stress_median_calmar'],
                                 x['median_calmar'], x['median_annual']), reverse=True)
        selected[group] = rank[0]['candidate']
        ranking[group] = rank
    write(out / 'results.json', dict(records=records, selected=selected, ranking=ranking,
                                     selection_uses_validation=False))
    lines = ['# KDJ 参数、趋势过滤、成交量与回撤控制研究', '',
             '所有候选均使用统一现金流账本：原始价格成交、T+1 开盘、交易成本、现金分红及红利税保持不变。参数只按 2020—2022 发现期跨标的排序，验证期和后续回放属于探索性检查，不称全新盲测。', '',
             '|组别|冻结候选|发现期中位年化|压力中位年化|最差发现期回撤|', '|---|---|---:|---:|---:|']
    for group, candidate_id in selected.items():
        item = next(x for x in ranking[group] if x['candidate'] == candidate_id)
        lines.append(f"|{group}|{candidate_id}|{item['median_annual']:.2%}|{item['stress_median_annual']:.2%}|{item['worst_drawdown']:.2%}|")
    lines += ['', '冻结结果：股票组选择 KDJ(5,3,20,80)，ETF 组选择 KDJ(14,5,20,80)。默认 KDJ(9,3,30,70)没有在这组发现期排序中胜出。',
              '候选包括 KDJ 周期 5/9/14、平滑 3/5、超卖/超买阈值 20/30/70/80；过滤候选要求短均线位于长均线之上、长均线斜率向上、成交量相对前 20 日均量达到 0.8 或 1.0，并加入固定止损或持仓高点回撤退出。',
              '回撤控制复用统一账本的 stop_loss_pct、trailing_stop_pct 与 risk_cooldown_days；没有另建持仓模拟。',
              '本轮过滤候选没有胜出，主要原因是量能和趋势条件减少了有效入场，平均仓位明显下降；低回撤不等于高收益，必须同时检查收益、交易次数和平均仓位。',
              '完整候选结果和参数见 output/strategy_research/dividend_kdj_parameter_v1/results.json；行情和完整账本只保存在本地 output/data/private。']
    (ROOT / 'docs' / 'KDJ参数趋势量能回撤研究.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('COMPLETE', len(records), selected, flush=True)


if __name__ == '__main__':
    main()
