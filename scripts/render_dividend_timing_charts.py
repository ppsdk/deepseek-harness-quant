"""Render local research charts for a dividend low-volatility timing ledger.

The script reads the private/local raw-price cache and archived cash-flow
ledgers.  It deliberately writes under ``output/`` so third-party market data
is not redistributed with the source repository.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

plt.rcParams["font.family"] = "Microsoft YaHei"
plt.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from factors.research_library import read_config
from scripts.dividend_timing_research import load_data


def read_run(folder: Path, code: str, name: str) -> dict:
    path = folder / "runs" / f"{code}_{name}_full.json"
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def frame_from_accounts(run: dict) -> pd.DataFrame:
    frame = pd.DataFrame(run["daily_account"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.set_index("date").sort_index()
    frame["nav"] = frame["equity"] / float(run["params"]["initial_capital"])
    return frame


def make_kline(frame: pd.DataFrame, run: dict, path: Path, code: str, label='波动预算基线') -> None:
    start = pd.to_datetime(run["actual_start"])
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index)
    price = frame.loc[(frame.index >= start) & (frame.index <= pd.Timestamp(run['params']['end']))].copy()
    price.index = pd.to_datetime(price.index)
    x = np.arange(len(price), dtype=float)
    index_map = {day.strftime("%Y-%m-%d"): i for i, day in enumerate(price.index)}

    fig, ax = plt.subplots(figsize=(18, 8), dpi=180)
    up = price["close"] >= price["open"]
    colors = np.where(up, "#d73027", "#1a9850")
    width = 0.62
    for i, (_, row) in enumerate(price.iterrows()):
        ax.vlines(i, row["low"], row["high"], color=colors[i], linewidth=0.45, alpha=0.9)
        lower = min(row["open"], row["close"])
        height = max(abs(row["close"] - row["open"]), row["close"] * 0.0005)
        ax.add_patch(Rectangle((i - width / 2, lower), width, height,
                               facecolor=colors[i], edgecolor=colors[i], linewidth=0.25))

    buys = [t for t in run["trades"] if t["side"] == "buy"]
    sells = [t for t in run["trades"] if t["side"] == "sell"]
    for trade, marker, color, side_label in ((buys, "^", "#2166ac", "买入"),
                                        (sells, "v", "#762a83", "卖出")):
        xs, ys = [], []
        for item in trade:
            key = str(item["date"])[:10]
            if key in index_map:
                xs.append(index_map[key])
                ys.append(float(item["fill_price"]))
        if xs:
            ax.scatter(xs, ys, marker=marker, s=44, color=color, edgecolor="white",
                       linewidth=0.45, zorder=4, label=side_label)

    tick_step = max(1, len(price) // 12)
    ticks = np.arange(0, len(price), tick_step)
    ax.set_xticks(ticks)
    ax.set_xticklabels([price.index[i].strftime("%Y-%m") for i in ticks], rotation=35, ha="right")
    ax.set_xlim(-2, len(price) + 2)
    ax.set_ylabel("未复权价格")
    ax.set_title(f"{code} K线与买卖成交标记（现金流账本 · {label}）", loc="left", pad=22, fontsize=14, weight="bold")
    ax.text(0.99, 0.98, f"回测成交价（含滑点）；买{len(buys)}笔/卖{len(sells)}笔；{price.index[0].date()}—{price.index[-1].date()}",
            transform=ax.transAxes, fontsize=9, color="#555555", va="top", ha="right",
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=2))
    ax.grid(axis="y", alpha=0.22, linewidth=0.6)
    ax.spines[["top", "right"]].set_visible(False)
    handles = [Line2D([0], [0], marker="^", color="w", markerfacecolor="#2166ac",
                      markeredgecolor="white", markersize=8, label="买入成交"),
               Line2D([0], [0], marker="v", color="w", markerfacecolor="#762a83",
                      markeredgecolor="white", markersize=8, label="卖出成交")]
    ax.legend(handles=handles, loc="upper left", frameon=False, ncol=2)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    fig.savefig(path.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def make_equity(strategy: dict, buy_hold: dict, path: Path, code: str, label='波动预算基线') -> None:
    strat = frame_from_accounts(strategy)
    hold = frame_from_accounts(buy_hold)
    if not strat.index.equals(hold.index):
        raise ValueError('策略和对照日期不一致，不能静默截取或拼接曲线。')
    joined = pd.concat([strat["nav"].rename("策略"), hold["nav"].rename("买入持有")], axis=1)
    fig, ax = plt.subplots(figsize=(14, 7), dpi=180)
    ax.plot(joined.index, (joined["策略"] - 1) * 100, color="#2166ac", linewidth=2.0, label=label)
    ax.plot(joined.index, (joined["买入持有"] - 1) * 100, color="#b2182b", linewidth=1.7, label="买入持有")
    ax.axhline(0, color="#777777", linewidth=0.8)
    ax.set_ylabel("累计收益（%，含现金分红及已扣费用/税负）")
    ax.set_xlabel("日期")
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    sm = strategy["metrics"]
    bm = buy_hold["metrics"]
    note = (f"策略：年化 {sm['annual_return']:.2%}，最大回撤 {sm['max_drawdown']:.2%}；"
            f"买入持有：年化 {bm['annual_return']:.2%}，最大回撤 {bm['max_drawdown']:.2%}")
    ax.set_title(f"{code} 收益曲线（现金流账本）", loc="left", pad=22, fontsize=14, weight="bold")
    ax.text(0.99, 0.98, note, transform=ax.transAxes, fontsize=9, color="#555555", va="top", ha="right",
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=2))
    ax.grid(axis="both", alpha=0.22, linewidth=0.6)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    fig.savefig(path.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default="600900.SH")
    parser.add_argument("--study", choices=['volatility', 'reversal'], default='volatility')
    parser.add_argument("--output-dir", default="output/strategy_research/charts")
    args = parser.parse_args()
    base = read_config("dividend_timing_research")
    frames, _ = load_data(base, download=False, price_basis="none")
    if args.code not in frames:
        raise ValueError(f"本地现金流行情中没有 {args.code}")
    cfg = read_config('dividend_volatility_research')
    if args.study == 'reversal':
        folder = ROOT / cfg['reversal']['output_dir']
        results = json.loads((folder/'results.json').read_text(encoding='utf-8'))
        group = next(g for g,spec in base['groups'].items() if args.code in spec['assets'])
        variant = results['selected'][group]
        strategy = read_run(folder,args.code,variant)
        buy_hold = read_run(folder,args.code,'buy_hold')
        label = f'低位反转/{variant}（发现期选取）'
        stem = f'{args.code}_reversal'
    else:
        cash_overlay = ROOT / cfg['overlay_output_dir'].replace('_v1','_cashflow_v1')
        cash_initial = ROOT / base['cashflow_output_dir']
        strategy = read_run(cash_overlay, args.code, "volatility_budget_base")
        buy_hold = read_run(cash_initial, args.code, "buy_hold")
        label, stem = '波动预算基线', args.code
    output = ROOT / args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    make_kline(frames[args.code], strategy, output / f"{stem}_kline_signals.png", args.code,label)
    make_equity(strategy, buy_hold, output / f"{stem}_equity_curve.png", args.code,label)
    print(output / f"{stem}_kline_signals.png")
    print(output / f"{stem}_equity_curve.png")


if __name__ == "__main__":
    main()
