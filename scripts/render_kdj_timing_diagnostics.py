"""Render causal KDJ timing diagnostics from the archived ledger.

The figure keeps raw execution prices separate from the forward-only signal
series. It is intended to inspect whether entries occur after an oversold
turn and exits after a weakening trend, not to make a hindsight claim.
"""
from __future__ import annotations

import json
import argparse
from copy import deepcopy
from pathlib import Path
import sys

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

plt.rcParams['font.family'] = 'Microsoft YaHei'
plt.rcParams['axes.unicode_minus'] = False

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from factors.research_library import read_config
from backtest.research_dividend_timing import causal_signal_panels, reversal_signals
from scripts.dividend_timing_research import load_cash_actions, load_data, panels_for, write
from scripts.dividend_kdj_parameter_research import run as run_candidate


def candidate_settings(manifest, candidate_id):
    for candidate in manifest['candidates']:
        if candidate['id'] == candidate_id:
            return candidate
    raise KeyError(candidate_id)


def render(code, group, base, config, results, output_dir, *, override=None,
           display_start=None, full=False):
    candidate_id = results['selected'][group]
    manifest_path = ROOT / config['output_dir'] / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    candidate = deepcopy(candidate_settings(manifest, candidate_id))
    frames, _ = load_data(base, price_basis='none')
    raw = frames[code]
    panels = panels_for(raw, code)
    actions = load_cash_actions(base, code, group)
    if override:
        candidate['settings'].update(override)
        st = candidate['settings']
        candidate_id = f"kdj_{st['kdj_period']}_{st['kdj_smooth']}_{st['oversold']:g}_{st['overbought']:g}"
        candidate.update(id=candidate_id, variant='kdj', risk={})
        run = run_candidate(raw, code, group, candidate, base['periods']['full'], base, actions)
        write(output_dir / 'ledgers' / f'{code}_{candidate_id}_full.json', run)
    else:
        run_path = ROOT / config['output_dir'] / 'runs' / f'{code}_{candidate_id}_full.json'
        run = json.loads(run_path.read_text(encoding='utf-8'))
    signal_panels = causal_signal_panels(panels, actions)
    adapters, indicators = reversal_signals(signal_panels, 'kdj', candidate['settings'])

    start = max(pd.Timestamp(run['actual_start']), pd.Timestamp(display_start)) if display_start else pd.Timestamp(run['actual_start'])
    end = pd.Timestamp(run['params']['end'])
    view = raw.loc[(raw.index >= start.strftime('%Y-%m-%d')) & (raw.index <= end.strftime('%Y-%m-%d'))].copy()
    idx = pd.to_datetime(view.index)
    x = np.arange(len(view), dtype=float)
    index_map = {str(day)[:10]: i for i, day in enumerate(view.index)}
    # Map the continuous signal series back onto each day's raw price scale.
    # Keep raw candles and fills intact; neither the MA nor signal points may
    # float at a cumulative dividend/split-adjusted level above the candles.
    scale = signal_panels['close'][code] / raw['close']
    close = raw['close'].reindex(view.index)
    k = indicators['k'][code].reindex(view.index)
    d = indicators['d'][code].reindex(view.index)
    ma = (indicators['trend'][code] / scale).reindex(view.index)
    volume = view['volume'].astype(float)
    volume_avg = raw['volume'].shift().rolling(20, min_periods=20).mean().reindex(view.index)
    trend_label = f"MA{candidate['settings']['trend_period']}"

    fig, axes = plt.subplots(3, 1, figsize=(16, 10), dpi=160, sharex=True,
                             gridspec_kw={'height_ratios': [3.5, 1.65, 1.35]})
    ax, kd_ax, vol_ax = axes
    up = view['close'] >= view['open']
    colors = np.where(up, '#d73027', '#1a9850')
    width = .62
    for i, (_, row) in enumerate(view.iterrows()):
        ax.vlines(i, row['low'], row['high'], color=colors[i], linewidth=.42, alpha=.8)
        lower = min(row['open'], row['close'])
        height = max(abs(row['close'] - row['open']), row['close'] * .0005)
        ax.add_patch(Rectangle((i - width / 2, lower), width, height,
                               facecolor=colors[i], edgecolor=colors[i], linewidth=.2))
    ax.plot(x, ma, color='#f39c12', linewidth=1.15, label=trend_label + '（信号均线映射至原价）')

    visible_trades = [t for t in run['trades'] if str(t['date'])[:10] in index_map]
    buys = [t for t in visible_trades if t['side'] == 'buy']
    sells = [t for t in visible_trades if t['side'] == 'sell']
    for items, marker, color in ((buys, '^', '#2166ac'), (sells, 'v', '#762a83')):
        xs, ys = [], []
        for trade in items:
            day = str(trade['date'])[:10]
            if day in index_map:
                xs.append(index_map[day]); ys.append(float(trade['fill_price']))
        if xs:
            ax.scatter(xs, ys, marker=marker, s=52, color=color, edgecolor='white', linewidth=.45, zorder=5)
    # Show actionable signal dates tied to fills. A condition can stay true
    # while flat for months; drawing all such exits obscures the actual path.
    for items, color in ((buys, '#2166ac'), (sells, '#762a83')):
        for trade in items:
            signal_day = str(trade['signal_date'])[:10]
            if signal_day in index_map:
                signal = adapters['_entry' if trade['side'] == 'buy' else '_exit'].at[signal_day, code]
                if signal != 1 or signal_day >= str(trade['date'])[:10]:
                    raise ValueError('成交与原始信号不一致。')
                i = index_map[signal_day]
                ax.scatter(i, close.iloc[i], marker='o', s=32, facecolors='none', edgecolors=color, linewidth=1., zorder=4)
                kd_ax.scatter(i, k.iloc[i], marker='o', s=32, facecolors='none', edgecolors=color, linewidth=1., zorder=4)
            if not full:
                i = index_map[str(trade['date'])[:10]]
                side_text = '买' if trade['side'] == 'buy' else '卖'
                offset = -32 if trade['side'] == 'buy' else 24
                ax.annotate(side_text + str(trade['date'])[5:10], (i, float(trade['fill_price'])),
                            xytext=(0, offset), textcoords='offset points', ha='center',
                            fontsize=8, color=color, arrowprops=dict(arrowstyle='-', color=color, lw=.7))
    accounts = pd.DataFrame(run['daily_account']).set_index('date')
    held = accounts['market_value'].reindex(view.index).gt(0).to_numpy()
    for left, right in zip(np.flatnonzero(np.diff(np.r_[False, held].astype(int)) == 1),
                           np.flatnonzero(np.diff(np.r_[held, False].astype(int)) == -1)):
        ax.axvspan(left - .5, right + .5, color='#2166ac', alpha=.055, zorder=0)
    ax.set_ylabel('未复权价格')
    st = candidate['settings']
    title = f"{base['groups'][group]['assets'][code]} {code}｜KDJ({st['kdj_period']},{st['kdj_smooth']},{st['oversold']:g},{st['overbought']:g})"
    ax.set_title(title, loc='left', fontsize=14, weight='bold', pad=46)
    ax.text(0, 1.04, f'回测成交：买{len(buys)}笔 / 卖{len(sells)}笔；空心圆为对应收盘信号，三角为次日成交，浅蓝背景为持仓\n{idx[0].date()}—{idx[-1].date()}；成交量仅作观察，未启用量能过滤',
            transform=ax.transAxes, ha='left', va='bottom', fontsize=9, color='#555',
            bbox=dict(facecolor='white', edgecolor='none', alpha=.78, pad=2))
    ax.grid(axis='y', alpha=.2)
    ax.spines[['top', 'right']].set_visible(False)
    ax.legend(handles=[Line2D([0], [0], color='#f39c12', label=trend_label),
                       Line2D([0], [0], marker='o', color='w', markerfacecolor='none', markeredgecolor='#2166ac', label='买入信号'),
                       Line2D([0], [0], marker='^', color='w', markerfacecolor='#2166ac', label='回测买入'),
                       Line2D([0], [0], marker='o', color='w', markerfacecolor='none', markeredgecolor='#762a83', label='卖出信号'),
                       Line2D([0], [0], marker='v', color='w', markerfacecolor='#762a83', label='回测卖出')],
              loc='upper left', ncol=5, frameon=False)

    kd_ax.plot(x, k, color='#2166ac', linewidth=1.1, label='K')
    kd_ax.plot(x, d, color='#d95f02', linewidth=1.1, label='D')
    kd_ax.plot(x, 3 * k - 2 * d, color='#7570b3', linewidth=.8, alpha=.8, label='J')
    kd_ax.axhline(float(candidate['settings']['oversold']), color='#1b9e77', linewidth=.8, linestyle='--')
    kd_ax.axhline(float(candidate['settings']['overbought']), color='#d95f02', linewidth=.8, linestyle='--')
    kd_ax.fill_between(x, 0, float(candidate['settings']['oversold']), color='#1b9e77', alpha=.06)
    kd_ax.fill_between(x, float(candidate['settings']['overbought']), 100, color='#d95f02', alpha=.06)
    j = 3 * k - 2 * d
    kd_ax.set_ylim(min(-10., float(j.min()) - 4), max(110., float(j.max()) + 4))
    kd_ax.set_ylabel('KDJ')
    kd_ax.grid(axis='y', alpha=.18)
    kd_ax.spines[['top', 'right']].set_visible(False)
    kd_ax.legend(loc='upper left', ncol=3, frameon=False)

    vol_ax.bar(x, volume, width=.72, color=colors, alpha=.55)
    vol_ax.plot(x, volume_avg, color='#636363', linewidth=1.0, label='前20日均量')
    vol_ax.set_ylabel('成交量')
    vol_ax.grid(axis='y', alpha=.18)
    vol_ax.spines[['top', 'right']].set_visible(False)
    vol_ax.legend(loc='upper left', frameon=False)
    ticks = np.linspace(0, len(view) - 1, min(12, len(view)), dtype=int)
    axes[-1].set_xticks(ticks)
    axes[-1].set_xticklabels([idx[i].strftime('%Y-%m') for i in ticks], rotation=35, ha='right')
    axes[-1].set_xlabel('日期')
    fig.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = '_full' if full else '_recent'
    path = output_dir / f'{code}_{candidate_id}_diagnostic{suffix}.png'
    fig.savefig(path, bbox_inches='tight')
    fig.savefig(path.with_suffix('.svg'), bbox_inches='tight')
    plt.close(fig)
    write(path.with_suffix('.json'), dict(code=code, candidate=candidate, source_data_end=base['data_end'],
          display_start=str(idx[0].date()), display_end=str(idx[-1].date()), full_metrics=run['metrics'],
          visible_trades=visible_trades, price_basis='raw_candles_and_fills_signal_ma_divided_by_causal_scale',
          effective_rules=dict(variant='kdj', volume_filter=False,
               trend_exit_period=st['trend_period'], trend_exit_confirm=st['trend_confirm'],
               setup_lookback=st['setup_lookback'], confirmation_lookback=st['confirmation_lookback']),
          last_observation=dict(date=str(idx[-1].date()), k=float(k.iloc[-1]), d=float(d.iloc[-1]),
               j=float(j.iloc[-1]), entry=float(adapters['_entry'].at[view.index[-1], code]),
               exit=float(adapters['_exit'].at[view.index[-1], code]),
               held=bool(held[-1])),
          note='参数指定的历史探索性回放，未改变冻结选参结果。'))
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--code')
    parser.add_argument('--period', type=int)
    parser.add_argument('--smooth', type=int)
    parser.add_argument('--oversold', type=float, default=20.)
    parser.add_argument('--overbought', type=float, default=80.)
    parser.add_argument('--display-start', default='2025-01-01')
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    if (args.period is None) != (args.smooth is None):
        parser.error('--period 与 --smooth 必须同时提供。')
    if args.period is not None and (args.period < 2 or args.smooth < 1 or not 0 <= args.oversold < args.overbought <= 100):
        parser.error('KDJ周期、平滑或阈值无效。')
    base = read_config('dividend_timing_research')
    config = read_config('dividend_volatility_research')['kdj_parameter']
    results = json.loads((ROOT / config['output_dir'] / 'results.json').read_text(encoding='utf-8'))
    output = ROOT / 'output' / 'strategy_research' / 'charts'
    for group, spec in base['groups'].items():
        code = args.code or spec['primary']
        if code not in spec['assets']:
            continue
        override = dict(kdj_period=args.period, kdj_smooth=args.smooth, oversold=args.oversold,
                        overbought=args.overbought) if args.period is not None else None
        path = render(code, group, base, config, results, output, override=override,
                      display_start=None if args.full else args.display_start, full=args.full)
        print(path)


if __name__ == '__main__':
    main()
