"""Frozen low-zone reversal rules through the shared raw-price cash ledger."""
from pathlib import Path
import json
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from factors.research_library import read_config
from backtest.research_dividend_timing import causal_signal_panels, reversal_signals
from backtest.research_portfolio import simulate
from scripts.dividend_timing_research import load_data, load_cash_actions, panels_for, summarize, write
from backtest.research_runtime import snapshot


def run(frame, code, group, variant, period, base, settings, actions, stress=False):
    panels = panels_for(frame, code)
    signal_panels = causal_signal_panels(panels, actions)
    adapters, indicators = reversal_signals(signal_panels, variant if variant != 'buy_hold' else 'kdj', settings)
    known = indicators['trend'].notna() & indicators['position'].notna() & indicators['d'].notna()
    valid = known.index[known.iloc[:, 0] & (known.index >= period['start']) & (known.index <= period['end'])]
    if not len(valid):
        raise ValueError('反转研究没有充分预热日期：' + code)
    if variant == 'buy_hold':
        enter = panels['close'].notna().astype(float)
        adapters = dict(_entry=enter, _exit=enter * 0.)
    panels.update(adapters)
    panels['_execution_price_basis'] = 'raw'
    panels['_cash_actions'] = actions
    params = {**base['execution'], **period, 'start':str(valid[0]),
              'stamp_tax_bps':base['groups'][group]['stamp_tax_bps'],
              'rebalance_days':1, 'min_trade_interval_days':settings['min_trade_interval_days']}
    if stress:
        params.update(base['stress'])
    sim = simulate(panels, panels['close'] * 0. + 1., params)
    return dict(code=code, group=group, variant=dict(id=variant), actual_start=params['start'],
                params=params, period=period, stress=stress, metrics=summarize(sim, params['initial_capital']),
                daily_account=sim['daily_account'], trades=sim['trades'], rejections=sim['rejections'],
                cash_distributions=sim['cash_distributions'],
                returns=[dict(date=str(d)[:10],ret=float(v)) for d,v in sim['returns'].items()],
                signal_events=[dict(date=str(day),entry=bool(adapters['_entry'].at[day,code] == 1),
                                    exit=bool(adapters['_exit'].at[day,code] == 1))
                               for day in valid if adapters['_entry'].at[day,code] == 1 or adapters['_exit'].at[day,code] == 1])


def main():
    base = read_config('dividend_timing_research')
    settings = read_config('dividend_volatility_research')['reversal']
    out = ROOT / settings['output_dir']
    frames, audit = load_data(base, price_basis='none')
    write(out/'manifest.json',dict(config=settings,base_config=base,data=audit,price_basis='none',
                                  provenance=snapshot(),evaluation='exploratory_frozen_three_candidates'))
    records, selected, ranking = [], {}, {}
    variants = settings['variants']
    for group,spec in base['groups'].items():
        for code in spec['assets']:
            actions = load_cash_actions(base,code,group)
            for phase,period in base['periods'].items():
                for variant in variants + ['buy_hold']:
                    for stress in (False,True):
                        row = run(frames[code],code,group,variant,period,base,settings,actions,stress)
                        row['phase'] = phase
                        write(out/'runs'/f"{code}_{variant}_{phase}{'_stress' if stress else ''}.json",row)
                        records.append({k:v for k,v in row.items() if k not in ('daily_account','trades','returns','rejections','signal_events')})
            print('DONE',code,flush=True)
        rank = []
        for variant in variants:
            xs = [r['metrics'] for r in records if r['group']==group and r['phase']=='discovery' and r['variant']['id']==variant]
            rank.append(dict(variant=variant,median_calmar=float(np.median([x['calmar'] for x in xs])),
                             median_annual=float(np.median([x['annual_return'] for x in xs]))))
        rank.sort(key=lambda r:(r['median_annual']>0,r['median_calmar'],r['median_annual']),reverse=True)
        selected[group] = rank[0]['variant']
        ranking[group] = rank
    write(out/'results.json',dict(records=records,selected=selected,ranking=ranking,selection_uses_validation=False))
    lines = ['# 红利低波低位反转与趋势转弱择时','',
             '固定三组候选，按2020—2022发现期跨标的常规/压力Calmar中位数选取；其他历史已被观察，属于探索性回放。',
             '信号采用仅向前应用已发生分红/拆分的连续OHLC；未复权成交价、现金分红、FIFO红利税仍由统一账本处理。','',
             '|标的|规则|窗口|年化|回撤|夏普|成交笔数|平均仓位|','|---|---|---|---:|---:|---:|---:|---:|']
    for group,spec in base['groups'].items():
        for code in [spec['primary']]:
            for phase in ['full','forward_replay']:
                for variant in variants+['buy_hold']:
                    r=next(r for r in records if r['code']==code and r['phase']==phase and r['variant']['id']==variant and not r['stress'])
                    m=r['metrics'];lines.append(f"|{code}|{variant}|{phase}|{m['annual_return']:.2%}|{m['max_drawdown']:.2%}|{m['sharpe']:.2f}|{m['trades']}|{m['average_exposure']:.1%}|")
    lines += ['', '发现期冻结选择：'+json.dumps(selected,ensure_ascii=False), '',
              'KDJ(9,3,3)：近5日K、D均≤30，近3日金叉、当前K>D且收盘上涨买入；近5日K、D均≥70后死叉卖出。',
              '布林(20,2)：近5日位置≤10%，近3日重回10%以上、当前仍>10%且上涨买入；近5日≥90%后跌回90%以下卖出。',
              '组合：近5日同时存在上述两类低位，当前K>D、布林位置>10%，近3日金叉或收复10%边界且当天上涨才买；高位两类条件后死叉卖。',
              '三组共同趋势退出：近10日曾站上MA10，连续2日收盘低于MA10且MA10下降。所有信号下一观测交易日开盘尝试成交。',
              '该规则允许亏损卖出，不能保证每一轮卖价高于买价，也不能确认真正的历史最低/最高点。',
              '复现：`python -X utf8 scripts/dividend_reversal_research.py`；行情及完整成交账本仅保存在本地output目录。']
    (ROOT/'docs/红利低波反转择时研究.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('COMPLETE',len(records),selected,flush=True)


if __name__ == '__main__':
    main()
