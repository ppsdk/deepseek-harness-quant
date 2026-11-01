"""Plot the dividend signal API's states, fills and matched equity histories.

Local research preview: raw-price candles, causal MA mapped to raw prices,
and unsmoothed equity from the shared simulator. No new signal calculation.
"""
import argparse
import json
from pathlib import Path
import sys

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backtest.research_dividend_signals import evaluate

plt.rcParams.update({'font.family': 'Microsoft YaHei', 'axes.unicode_minus': False,
                     'svg.fonttype': 'none', 'font.size': 10})
BLUE, PURPLE, ORANGE = '#2166ac', '#762a83', '#e69f00'
STATE_COLORS = {'wait': '#e1e5e9', 'setup': '#f6d77a', 'buy': BLUE, 'hold': '#a9cce4',
                'weakening': ORANGE, 'sell': PURPLE, 'insufficient': '#666666'}


def save(fig, path):
    fig.savefig(path, dpi=170, bbox_inches='tight', facecolor='white')
    fig.savefig(path.with_suffix('.svg'), bbox_inches='tight', facecolor='white')
    plt.close(fig)


def spans(mask):
    changes = np.diff(np.r_[False, np.asarray(mask, dtype=bool), False].astype(int))
    return zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1))


def plot_signals(result, path, display_start):
    hist = pd.DataFrame(result['history']).set_index('date')
    bars = pd.DataFrame(result['bars']).set_index('date')
    view = hist.loc[hist.index >= display_start]
    if view.empty:
        raise ValueError('展示起点之后没有信号记录。')
    bars = bars.loc[view.index]
    st = result['effective']['profile']['settings']
    exit_period = st.get('exit_period', st.get('trend_period'))
    regime = result['effective']['profile']['variant'] == 'kdj_regime'
    separate_exit = result['effective']['profile']['variant'] in ('kdj_trend_exit','kdj_regime')
    heights = [3.8, 1.4, 1.2, .24] if separate_exit else [3.8, 1.45, .24]
    fig, axes = plt.subplots(len(heights), 1, figsize=(16, 11 if separate_exit else 9), sharex=True,
                             gridspec_kw={'height_ratios': heights})
    ax, kd, state = axes[0], axes[1], axes[-1]
    fig.subplots_adjust(left=.065, right=.985, top=.84, bottom=.18, hspace=.12)
    dates = pd.to_datetime(view.index)
    loc = {day: i for i, day in enumerate(view.index)}
    x = np.arange(len(view))
    for i, (_, row) in enumerate(bars.iterrows()):
        color = '#d73027' if row['close'] >= row['open'] else '#1a9850'
        ax.vlines(i, row['low'], row['high'], color=color, linewidth=.55)
        ax.add_patch(Rectangle((i-.32, min(row['open'], row['close'])), .64,
                              max(abs(row['close']-row['open']), row['close']*.0004),
                              facecolor=color, edgecolor=color, linewidth=.25))
    for left, right in spans(view['held']):
        ax.axvspan(left-.5, right-.5, color=BLUE, alpha=.065, zorder=0)
    ax.plot(x, view['exit_ma_raw'], color=ORANGE, lw=1.25)
    extra_lines = []
    if regime:
        ax.plot(x, view['entry_ma_raw'], color='#008b8b', lw=.9)
        ax.plot(x, view['long_ma_raw'], color='#999999', lw=.9, ls='--')
        extra_lines = [Line2D([],[],color='#008b8b',label=f"MA{st['entry_ma_period']}入场确认"),
                       Line2D([],[],color='#999999',ls='--',label=f"MA{exit_period}参考")]
    for value, marker, color, text, offset in [('buy','^',BLUE,'买',-30),('sell','v',PURPLE,'卖',26)]:
        signals = view[view['state'].eq(value)]
        sx = [loc[day] for day in signals.index]
        ax.scatter(sx, bars.loc[signals.index,'close'], facecolors='none', edgecolors=color, s=40, zorder=4)
        if not separate_exit or value == 'buy':
            kd.scatter(sx, signals['k'], facecolors='none', edgecolors=color, s=30, zorder=4)
        for trade in result['trades']:
            if trade['side'] != value or trade['date'] not in loc:
                continue
            if trade['signal_date'] >= trade['date']:
                raise ValueError('信号日和成交日顺序错误。')
            i = loc[trade['date']]
            ax.scatter(i, trade['fill_price'], marker=marker, color=color, s=65,
                       edgecolors='white', linewidth=.6, zorder=5)
            ax.annotate(text+trade['date'][5:], (i, trade['fill_price']), xytext=(0, offset),
                        textcoords='offset points', ha='center', color=color, fontsize=8,
                        arrowprops=dict(arrowstyle='-', color=color, lw=.6))
    ma_label = f"MA{st['weak_exit_period']}退出" if regime else f'MA{exit_period}' + ('（参考）' if separate_exit and st['exit_mode'] in ('dmi','dmi_weakening') else '')
    ax.legend(handles=[Line2D([],[],color=ORANGE,label=ma_label),
                       Line2D([],[],marker='o',linestyle='',markerfacecolor='none',color=BLUE,label='买入收盘信号'),
                       Line2D([],[],marker='^',linestyle='',color=BLUE,label='次日回测买入'),
                       Line2D([],[],marker='o',linestyle='',markerfacecolor='none',color=PURPLE,label='卖出收盘信号'),
                       Line2D([],[],marker='v',linestyle='',color=PURPLE,label='次日回测卖出')]+extra_lines,
              loc='upper left', ncol=4 if regime else 5, frameon=False, fontsize=9)
    ax.set_ylabel('未复权价格')
    for column, color in [('k', BLUE), ('d', '#d95f02'), ('j', '#7570b3')]:
        kd.plot(x, view[column], color=color, lw=1.1 if column!='j' else .8, label=column.upper())
    for value in [st['oversold']] + ([st['overbought']] if 'overbought' in st else []):
        kd.axhline(value, color='#888888', lw=.8, linestyle='--')
    kd.set_ylabel('KDJ · 买入' if separate_exit else 'KDJ')
    kd.legend(loc='upper left', ncol=3, frameon=False, fontsize=9)
    if separate_exit:
        dm = axes[2]
        peak_mode = st['exit_mode']=='dmi_weakening'
        adx_label = 'ADX（参与退出）' if peak_mode else 'ADX（仅观察）'
        for key, color, style, label in [('pdi', BLUE, '-', '+DI'), ('mdi', PURPLE, '-', '-DI'), ('adx', '#777777', '--', adx_label)]:
            dm.plot(x, view[key], color=color, ls=style, lw=1, label=label)
        sells = view[view['state'].eq('sell')]
        if peak_mode:
            triggers = view[view['reasons'].map(lambda values: 'dmi_uptrend_weakening' in values)]
            dm.scatter([loc[day] for day in triggers.index], triggers['adx'],
                       facecolors='none', edgecolors=PURPLE, s=32, label='多头衰减（含空仓）', zorder=3)
            if regime:
                weak_condition = hist['weak_rebound_exit'].eq(True)
                weak_onset = weak_condition & ~weak_condition.shift(fill_value=False)
                weak = view[weak_onset.reindex(view.index)]
                dm.scatter([loc[day] for day in weak.index], weak['mdi'], marker='s',
                           facecolors='none', edgecolors='#b06a00', s=24, label='弱趋势起点（含空仓）', zorder=3)
        if st['exit_mode'] != 'ma':
            sell_y = [row['adx'] if peak_mode and 'dmi_uptrend_weakening' in row['reasons'] else row['mdi'] for _, row in sells.iterrows()]
            dm.scatter([loc[day] for day in sells.index], sell_y, color=PURPLE, marker='v', s=35, zorder=4)
        if peak_mode:
            dm.axhline(st['dmi_peak_adx_min'], color='#888888', lw=.6, ls=':')
        dm.set_ylabel(f"DMI({st['dmi_period']})" + (' · 参考' if st['exit_mode']=='ma' else ''))
        top = view[['pdi','mdi','adx']].max().max()
        if pd.notna(top): dm.set_ylim(0,max(1.,top*1.25))
        dm.legend(loc='upper left', ncol=5 if regime else 4, frameon=False, fontsize=8 if regime else 9)
    for i, value in enumerate(view['state']):
        state.add_patch(Rectangle((i-.5, 0), 1., 1., facecolor=STATE_COLORS[value], linewidth=0))
    state.set_ylim(0,1); state.set_yticks([]); state.set_ylabel('状态', rotation=0, labelpad=22, va='center')
    state.set_xlim(-1,len(view))
    ticks = np.unique(np.linspace(0,len(view)-1,min(12,len(view))).astype(int))
    state.set_xticks(ticks); state.set_xticklabels([dates[i].strftime('%Y-%m') for i in ticks],rotation=30,ha='right')
    for axis in axes:
        axis.spines[['top','right']].set_visible(False)
        if axis is not state: axis.grid(axis='y',alpha=.18)
    profile = result['effective']['profile']
    fig.suptitle(f"{result['name']} {result['code']}｜交易信号 v1", x=.065, y=.97, ha='left',fontsize=17,weight='bold')
    volume = '启用趋势/量能过滤' if profile['variant']=='kdj_filtered' else '未启用量能过滤'
    threshold = f"超卖≤{st['oversold']}" if separate_exit else f"阈值{st['oversold']}/{st['overbought']}"
    if separate_exit:
        dmi_text = f"-DI>+DI 连续{st['dmi_confirm']}日"
        ma_text = f"收盘<MA{exit_period}连续{st['exit_confirm']}日" + (f"且均线低于{st['exit_slope_lookback']}日前" if st['exit_require_falling'] else '')
        if st['exit_mode']=='dmi_weakening':
            exit_text = f"近{st['dmi_peak_lookback']}日曾+DI>-DI、+DI≥{st['dmi_peak_pdi_min']}、ADX≥{st['dmi_peak_adx_min']}；连续{st['dmi_peak_confirm']}日+DI↓、ADX↓、-DI↑"
        else:
            exit_text = {'dmi': dmi_text, 'ma': ma_text, 'dmi_and_ma': dmi_text+'，同时'+ma_text}[st['exit_mode']]
        volume = '仅买入用KDJ；卖出：'+exit_text
    kdj_text = f"KDJ：RSV {st['kdj_period']}日，K/D各平滑{st['kdj_smooth']}，{threshold}"
    header = (kdj_text+'；仅用于买入\n卖出：'+exit_text) if separate_exit and st['exit_mode']=='dmi_weakening' else kdj_text+'；'+volume
    if regime:
        header = kdj_text+f"；准备/金叉窗口{st['setup_lookback']}/{st['confirmation_lookback']}日\n"+f"买入还需+DI>-DI连续{st['entry_dmi_confirm']}日、收盘>MA{st['entry_ma_period']}且均线上升；卖出：DMI多头衰减，或DMI偏空且跌破下行MA{st['weak_exit_period']}"
    fig.text(.065,.925,header+f"\n展示 {view.index[0]}—{view.index[-1]}；浅蓝背景为实际回测持仓，底部色带为当日收盘状态",color='#555555',fontsize=10,va='top')
    labels = {'wait':'空仓等待','setup':'超卖待确认','buy':'买入条件','hold':'持有','weakening':'持仓转弱','sell':'卖出条件'}
    fig.legend(handles=[Patch(facecolor=STATE_COLORS[key],label=value) for key,value in labels.items()],
               loc='lower left',bbox_to_anchor=(.065,.045),ncol=6,frameon=False,fontsize=9)
    fig.text(.065,.015,'历史研究回放；已计入现有分红/拆分和费用，公司行为完整性仍待核验。',fontsize=9,color='#666666')
    save(fig,path)
    return dict(display_start=str(view.index[0]),display_end=str(view.index[-1]),
                state_counts=view['state'].value_counts().to_dict())


def plot_equity(result, path, reference=None):
    initial = result['effective']['execution']['initial_capital']
    account = pd.DataFrame(result['history']).set_index('date')
    controls = {key:pd.DataFrame(value).set_index('date') for key,value in result['comparison_history'].items()}
    for control in controls.values():
        if not account.index.equals(control.index):
            raise ValueError('策略与对照账本日期不一致。')
    series = {'策略':account['equity']/initial,
              '买入持有':controls['buy_hold']['equity']/initial,
              '策略·压力费用':controls['stress']['equity']/initial}
    expected = {'策略':'metrics','买入持有':'buy_hold_metrics','策略·压力费用':'stress_metrics'}
    for key,nav in series.items():
        if not np.isclose(nav.iloc[-1],result[expected[key]]['final_nav']):
            raise ValueError('曲线末值与对应指标不一致。')
    if reference:
        other = pd.DataFrame(reference['history']).set_index('date')
        if (not other.index.equals(account.index) or reference['code'] != result['code']
                or reference['effective']['execution'] != result['effective']['execution']):
            raise ValueError('旧规则对照必须使用相同资产、日期和执行参数。')
        series['旧规则'] = other['equity']/initial
        if not np.isclose(series['旧规则'].iloc[-1], reference['metrics']['final_nav']):
            raise ValueError('旧规则曲线末值与指标不一致。')
    fig,axes=plt.subplots(3,1,figsize=(15,9),sharex=True,
                          gridspec_kw={'height_ratios':[3.3,1.4,.8]})
    fig.subplots_adjust(left=.08,right=.98,top=.80,bottom=.13,hspace=.12)
    dates=pd.to_datetime(account.index)
    styles={'策略':(BLUE,'-'),'买入持有':('#b2182b','-'),'策略·压力费用':('#777777','--'), '旧规则':(ORANGE,'-.')}
    metrics=[]
    for key,nav in series.items():
        color,linestyle=styles[key]
        axes[0].plot(dates,nav-1,color=color,ls=linestyle,lw=1.7,label=key)
        peak=np.maximum.accumulate(np.r_[1.,nav.to_numpy()])[1:]
        axes[1].plot(dates,nav/peak-1,color=color,ls=linestyle,lw=1.1)
        m=reference['metrics'] if key=='旧规则' else result[expected[key]]
        metrics.append(f"{key}：累计 {m['total_return']:.1%}｜年化 {m['annual_return']:.2%}｜最大回撤 {m['max_drawdown']:.2%}")
    axes[0].legend(loc='upper left',ncol=4,frameon=False)
    axes[0].set_ylabel('累计收益'); axes[1].set_ylabel('回撤')
    for axis in axes[:2]:
        axis.yaxis.set_major_formatter(PercentFormatter(1.))
        axis.axhline(0,color='#999999',lw=.6)
    axes[2].fill_between(dates,account['exposure'].astype(float),0,color=BLUE,alpha=.3,step='post')
    axes[2].set_ylabel('策略仓位'); axes[2].set_ylim(0,1.05)
    axes[2].yaxis.set_major_formatter(PercentFormatter(1.))
    axes[2].xaxis.set_major_locator(mdates.YearLocator())
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    axes[2].set_xlim(dates[0],dates[-1])
    for axis in axes:
        axis.spines[['top','right']].set_visible(False); axis.grid(alpha=.18)
    fig.suptitle(f"{result['code']}｜交易信号收益与回撤",x=.08,y=.97,ha='left',fontsize=17,weight='bold')
    fig.text(.08,.825 if reference else .845,'\n'.join(metrics),fontsize=10,color='#444444',linespacing=1.7)
    fig.text(.08,.035,f"{result['actual_start']}—{result['data_end']}；同起始资金、同资产、同窗口；权益直接来自统一模拟器，未平滑。\n"
             '已计入现有现金分红/拆分及费用；实验参数，非全新样本外结果，公司行为完整性仍待核验。',fontsize=9,color='#666666')
    save(fig,path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code'); parser.add_argument('--profile'); parser.add_argument('--end')
    parser.add_argument('--start')
    parser.add_argument('--compare-profile', help='Compare another profile from equal initial cash and date')
    parser.add_argument('--display-start',default='2025-01-01')
    args=parser.parse_args()
    result=evaluate({**{key:value for key,value in vars(args).items() if key in ('code','profile','start','end') and value},'history':True})
    reference = evaluate(dict(code=result['code'], profile=args.compare_profile, start=result['actual_start'],
                              end=result['data_end'], history=True)) if args.compare_profile else None
    output=ROOT/'output/strategy_research/charts'
    output.mkdir(parents=True,exist_ok=True)
    stem=f"{result['code']}_{result['profile']}_signal_v1"
    signals=output/f'{stem}_signals.png'; equity=output/f'{stem}_equity.png'
    metadata=plot_signals(result,signals,args.display_start)
    plot_equity(result,equity,reference)
    (output/f'{stem}_source.json').write_text(json.dumps(dict(result=result,display=metadata,reference=reference),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(signals);print(equity)


if __name__=='__main__':
    main()
