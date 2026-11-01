"""Volatility-budget follow-up; reuse one ledger rather than synthetic returns."""
from pathlib import Path
import sys

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.dividend_timing_research import load_data,load_cash_actions,panels_for,summarize,write
from factors.research_library import read_config
from backtest.research_dividend_timing import volatility_budget
from backtest.research_portfolio import simulate


def run(frame,code,group,variant,period,base,cfg,stress=False,matched=None,
        price_basis='qfq', cash_actions=None):
    panels=panels_for(frame,code)
    if price_basis == 'none':
        panels['_execution_price_basis'] = 'raw'
        panels['_cash_actions'] = list(cash_actions or [])
    p={**base['execution'],**period,'stamp_tax_bps':base['groups'][group]['stamp_tax_bps'],
       'rebalance_days':cfg['rebalance_days'],'min_trade_interval_days':cfg['min_trade_interval_days'],
       'rebalance_threshold_pct':cfg['rebalance_threshold_pct']}
    if stress:p.update(base['stress'])
    if variant['id']=='volatility_budget':
        budget,vol=volatility_budget(panels,variant['window'],variant['target_volatility'],cfg['floors'][group],cfg['annual_days'])
        panels['_target_exposure_pct']=budget
    else:
        budget=None
        if matched is not None:p['target_exposure_pct']=float(matched*100)
    # Common maximum-grid warmup for every control, including new ETFs.
    warmed=panels['close'].rolling(max(cfg['windows'])+1).count().eq(max(cfg['windows'])+1)
    dates=warmed.index[warmed.iloc[:,0] & (warmed.index>=period['start']) & (warmed.index<=period['end'])]
    p['start']=str(dates[0])
    if variant['id']=='buy_hold':
        enter=panels['close'].notna().astype(float)
        panels.update(_entry=enter,_exit=enter*0)
    score=panels['close']*0+1.
    sim=simulate(panels,score,p)
    metrics=summarize(sim,p['initial_capital'])
    return dict(code=code,group=group,variant=variant,period=period,actual_start=p['start'],stress=stress,
                metrics=metrics,params=p, daily_account=sim['daily_account'],trades=sim['trades'],rejections=sim['rejections'],
                cash_distributions=sim.get('cash_distributions', {}),
                returns=[dict(date=str(d)[:10],ret=float(v)) for d,v in sim['returns'].items()],
                target_exposure=[dict(date=str(d),pct=float(v) if np.isfinite(v) else None)
                                 for d,v in budget.items()] if budget is not None else None)


def main():
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument('--cashflow',action='store_true')
    parser.add_argument('--download',action='store_true'); args=parser.parse_args()
    cfg=read_config('dividend_volatility_research');base=read_config(cfg['base_config'])
    price_basis='none' if args.cashflow else 'qfq'
    output_dir=cfg.get('cashflow_output_dir','output/strategy_research/dividend_volatility_cashflow_v1') if args.cashflow else cfg['output_dir']
    out=ROOT/output_dir
    frames,audit=load_data(base,args.download,price_basis)
    actions=({code:load_cash_actions(base,code,group) for group,spec in base['groups'].items() for code in spec['assets']}
             if args.cashflow else {code:[] for spec in base['groups'].values() for code in spec['assets']})
    variants=[dict(id='volatility_budget',window=w,target_volatility=v) for w in cfg['windows'] for v in cfg['target_volatilities']]
    from backtest.research_runtime import snapshot
    write(out/'manifest.json',dict(config=cfg,base_config=base,candidates=variants,data=audit,
                                   provenance=snapshot(),evaluation='post_trend_failure_exploratory',
                                   price_basis=price_basis,
                                   cash_action_counts={code:len(value) for code,value in actions.items()}))
    rows=[];selected={};ranking={}
    for group,spec in base['groups'].items():
        rank=[]
        for variant in variants:
            values=[]
            for code in spec['assets']:
                for stress in (False,True):
                    row=run(frames[code],code,group,variant,base['periods']['discovery'],base,cfg,stress,
                            price_basis=price_basis,cash_actions=actions[code])
                    rows.append({k:v for k,v in row.items() if k not in ('daily_account','returns','trades','rejections','target_exposure')})
                    values.append(row['metrics'])
            rank.append(dict(variant=variant,median_calmar=float(np.median([r['calmar'] for r in values])),
                             median_annual=float(np.median([r['annual_return'] for r in values]))))
        rank.sort(key=lambda r:(r['median_annual']>0,r['median_calmar'],r['median_annual']),reverse=True)
        ranking[group]=rank;selected[group]=rank[0]['variant']
        write(out/'frozen_selection.json',dict(selected=selected,ranking=ranking,selection_uses_validation=False))
        print('FROZEN',group,selected[group],flush=True)
        for code in spec['assets']:
            for phase,period in base['periods'].items():
                for stress in (False,True):
                    row=run(frames[code],code,group,selected[group],period,base,cfg,stress,
                            price_basis=price_basis,cash_actions=actions[code])
                    bh=run(frames[code],code,group,dict(id='buy_hold'),period,base,cfg,stress,
                           price_basis=price_basis,cash_actions=actions[code])
                    matched=run(frames[code],code,group,dict(id='matched_static_exposure'),period,base,cfg,stress,
                                matched=row['metrics']['average_exposure'],price_basis=price_basis,
                                cash_actions=actions[code])
                    for r in (row,bh,matched):
                        r['phase']=phase
                        write(out/'runs'/f"{code}_{r['variant']['id']}_{phase}{'_stress' if stress else ''}.json",r)
                        rows.append({k:v for k,v in r.items() if k not in ('daily_account','returns','trades','rejections','target_exposure')})
        print('DONE',group,flush=True)
    write(out/'results.json',dict(selected=selected,records=rows,data=audit))
    print('COMPLETE',len(rows),flush=True)


if __name__=='__main__':main()
