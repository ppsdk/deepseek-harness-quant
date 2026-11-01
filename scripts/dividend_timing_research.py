"""Prespecified stock/ETF timing comparison through the existing cash ledger."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factors.research_library import read_config
from backtest.research_dividend_timing import signals
from backtest.research_portfolio import simulate
from data.research_dividend import load_cash_actions, load_data, panels_for, summarize


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def run(frame, code, group, variant, period, cfg, stress=False, ledger=False,
        price_basis='qfq', cash_actions=None):
    p={**deepcopy(cfg['execution']),**period, 'stamp_tax_bps':cfg['groups'][group]['stamp_tax_bps']}
    if stress:
        p.update(cfg['stress'])
    panels=panels_for(frame,code)
    if price_basis == 'none':
        panels['_execution_price_basis'] = 'raw'
        panels['_cash_actions'] = list(cash_actions or [])
    # Only fully warmed dates may enter the timing study; the buy/hold control
    # for each asset uses the same maximum-grid warmup boundary.
    warmed=panels['close'].rolling(max(cfg['candidate_grid']['slow'])).count().eq(max(cfg['candidate_grid']['slow']))
    valid_dates=warmed.index[warmed.iloc[:,0] & (warmed.index>=period['start']) & (warmed.index<=period['end'])]
    if not len(valid_dates):
        raise ValueError('没有足够预热行情：'+code)
    p['start']=str(valid_dates[0])
    score=panels['close']*0+1.
    if variant['id']=='buy_hold':
        # Entry once; no discretionary exit or forced liquidation at year end.
        enter=panels['close'].notna().astype(float)
        parts=[(1.,dict(_entry=enter,_exit=enter*0))]
    else:
        adapters,_=signals(panels,variant['slow'],variant['band'],cfg['candidate_grid']['confirm_days'])
        core=cfg['groups'][group]['core_fraction']
        parts=[]
        if core:
            enter=panels['close'].notna().astype(float)
            parts.append((core,dict(_entry=enter,_exit=enter*0)))
        parts.append((1-core,adapters))
    sims=[]
    for fraction,adapter in parts:
        view={**panels,**adapter}; params={**p,'initial_capital':p['initial_capital']*fraction}
        sims.append(simulate(view,score,params))
    # Independent sleeves use actual capital/fees and never rebalance capital
    # between sleeves. Aggregate equity, not a synthetic mean of daily returns.
    accounts=[]
    for i,day in enumerate(sims[0]['daily_account']):
        rows=[s['daily_account'][i] for s in sims]
        equity=sum(r['equity'] for r in rows); mv=sum(r['market_value'] for r in rows)
        accounts.append(dict(date=day['date'],equity=equity,cash=sum(r['cash'] for r in rows),market_value=mv,
                             dividend_receivable=sum(r.get('dividend_receivable', 0.) for r in rows),
                             exposure=mv/equity if equity else 0.))
    nav=pd.Series([r['equity']/p['initial_capital'] for r in accounts],index=sims[0]['nav_series'].index)
    ret=nav.pct_change(fill_method=None);ret.iloc[0]=nav.iloc[0]-1
    sim=dict(returns=ret,nav_series=nav,daily_account=accounts,
             trades=[dict(t,sleeve=i) for i,s in enumerate(sims) for t in s['trades']],
             rejections=[r for s in sims for r in s['rejections']],
             cash_distributions=dict(
                 gross_dividends=sum(s.get('cash_distributions', {}).get('gross_dividends', 0.) for s in sims),
                 dividend_tax_paid=sum(s.get('cash_distributions', {}).get('dividend_tax_paid', 0.) for s in sims),
                 net_dividends=sum(s.get('cash_distributions', {}).get('net_dividends', 0.) for s in sims),
                 receivable=sum(s.get('cash_distributions', {}).get('receivable', 0.) for s in sims),
                 conditional_tax_if_sold=sum(s.get('cash_distributions', {}).get('conditional_tax_if_sold', 0.) for s in sims),
                 price_basis='raw' if price_basis == 'none' else 'unchanged',
                 events=[dict(event, sleeve=i) for i,s in enumerate(sims)
                         for event in s.get('cash_distributions', {}).get('events', [])]))
    metrics=summarize(sim,p['initial_capital'])
    result=dict(code=code,group=group,variant=variant,stress=stress,period=period,
                actual_start=p['start'],metrics=metrics,params=p,
                cash_distributions=sim['cash_distributions'])
    if ledger:
        result.update(daily_account=accounts,returns=[dict(date=str(d)[:10],ret=float(v)) for d,v in ret.items()],
                      trades=sim['trades'],rejections=sim['rejections'],
                      cash_distributions=sim['cash_distributions'])
    return result


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--download',action='store_true')
    parser.add_argument('--cashflow',action='store_true', help='使用未复权价格和现金分红账本')
    args=parser.parse_args();cfg=read_config('dividend_timing_research');out=ROOT/cfg['output_dir']
    price_basis='none' if args.cashflow else 'qfq'
    if args.cashflow:
        out=ROOT/cfg.get('cashflow_output_dir','output/strategy_research/dividend_timing_cashflow_v1')
    variants=[dict(id=f'ma{n}_b{int(b*100)}',slow=n,band=b) for n in cfg['candidate_grid']['slow'] for b in cfg['candidate_grid']['band']]
    frames,audit=load_data(cfg,args.download,price_basis)
    actions=({code:load_cash_actions(cfg,code,group) for group,spec in cfg['groups'].items() for code in spec['assets']}
             if args.cashflow else {code:[] for spec in cfg['groups'].values() for code in spec['assets']})
    write(out/'manifest.json',dict(config=cfg,candidates=variants,data=audit,
          source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                         ['scripts/dividend_timing_research.py','backtest/research_dividend_timing.py','backtest/research_portfolio.py']},
          evaluation='historically_observed_chronological_replay_not_blind',price_basis=price_basis,
          cash_action_counts={code:len(value) for code,value in actions.items()}))
    rows=[];selected={};ranking={}
    for group,spec in cfg['groups'].items():
        candidates=[]
        for v in variants:
            values=[]
            for code in spec['assets']:
                for stress in (False,True):
                    row=run(frames[code],code,group,v,cfg['periods']['discovery'],cfg,stress,
                            price_basis=price_basis,cash_actions=actions[code])
                    rows.append(row)
                    if row['metrics']['n_days']>=cfg['selection']['min_days']:
                        values.append(row['metrics'])
            if not values:
                continue
            median=lambda key:float(np.median([r[key] for r in values]))
            record=dict(variant=v,median_calmar=median('calmar'),median_annual=median('annual_return'),
                        worst_drawdown=min(r['max_drawdown'] for r in values),samples=len(values))
            record['drawdown_pass']=record['worst_drawdown']>=cfg['selection']['max_drawdown']
            candidates.append(record)
        candidates.sort(key=lambda r:(r['median_annual']>0,r['drawdown_pass'],r['median_calmar'],r['median_annual']),reverse=True)
        selected[group]=candidates[0]['variant'];ranking[group]=candidates
        print('FROZEN',group,selected[group],flush=True)
        write(out/'frozen_selection.json',dict(selected=selected,ranking=ranking,selection_uses_validation=False))
        for code in spec['assets']:
            for phase,period in cfg['periods'].items():
                for v in [dict(id='buy_hold'),selected[group]]:
                    for stress in (False,True):
                        row=run(frames[code],code,group,v,period,cfg,stress,ledger=True,
                                price_basis=price_basis,cash_actions=actions[code])
                        row['phase']=phase
                        path=out/'runs'/f"{code}_{v['id']}_{phase}{'_stress' if stress else ''}.json"
                        write(path,row)
                        rows.append({k:v for k,v in row.items() if k not in ('daily_account','returns','trades','rejections')})
        print('DONE',group,flush=True)
    write(out/'results.json',dict(selected=selected,records=rows,data=audit))
    print(json.dumps(dict(ok=True,selected=selected,records=len(rows)),ensure_ascii=False))


if __name__=='__main__':main()
