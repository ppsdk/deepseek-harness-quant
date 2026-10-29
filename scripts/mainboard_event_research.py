"""Bounded MA event-trading replay using the existing ledger and frozen ranks."""
import gc
import json
from pathlib import Path
import sys
from copy import deepcopy
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from backtest.research_cadence import attach
from scripts.mainboard_strategy_research import load_snapshot,write_json,log,summarize,archive_finalists
from backtest.research_runner import simulate
from backtest.research_timing import evaluate
from data.cache import DailyCache

def main():
    base=read_config('mainboard_research');event=read_config('event_trading_research')
    root=ROOT/base['output_dir'];out=root/event['output_subdir'];out.mkdir(parents=True,exist_ok=True)
    panels,eligible,signature=load_snapshot(base,root)
    policy=deepcopy(base);policy['execution'].update(event['execution'])
    policy['reproduction_command']='python scripts/mainboard_event_research.py'
    policy['round_id']='event_trading';write_json(out/'protocol.json',dict(policy=policy,event_policy=event,snapshot_hash=signature,validation_is_fresh_blind=False,note='沿用每轮发现期已冻结的因子和选股规则；验证年份在以前探索中已被查看，本次为时间隔离回放。'))
    index=DailyCache().get_daily(policy['execution']['timing_index'],adjust='none').set_index('date').sort_index()
    gate=evaluate(index,policy['execution']['timing_rule'],read_config('research_services')['timing']).reindex(eligible.index)
    timing=pd.DataFrame({code:gate for code in eligible.columns})
    all_rounds=[]
    for spec in policy['rounds']:
        folder=out/spec['id'];folder.mkdir(exist_ok=True)
        prior=root/spec['id'];shortlist=json.loads((prior/'frozen_selection.json').read_text(encoding='utf-8'))['finalists']
        catalog=json.loads((prior/'frozen_candidates.json').read_text(encoding='utf-8'))['strategies']
        selections=json.loads((root/'stock_selection'/spec['id']/'frozen_selection.json').read_text(encoding='utf-8'))['selection']
        if len(shortlist)*len(event['variants'])>event['max_search_combinations_per_round']:raise ValueError('先缩小搜索空间再运行。')
        current=deepcopy(policy);current['round_id']='event_'+spec['id'];current['periods'].update(discovery=spec['discovery'],validation=spec['validation'])
        write_json(folder/'policy.json',current);write_json(folder/'frozen_candidates.json',dict(strategies={key:catalog[key] for key in shortlist}))
        ledger=folder/'experiments.jsonl';rows=[json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines() if line] if ledger.exists() else []
        paths={key:'selection_'+key+'_'+selections[key]+'.npy' for key in shortlist}
        def run(key,variant,period,stress=False):
            phase=period+('_stress' if stress else '')
            old=next((r for r in rows if r['strategy']==key and r['variant']==variant['id'] and r['period']==phase),None)
            if old:return old
            p={**current['execution'],**current['periods'][period],**{k:v for k,v in variant.items() if k not in ('id','label')}}
            if stress:p.update(current['stress'])
            score=pd.DataFrame(np.load(root/'cache'/paths[key],mmap_mode='r'),index=eligible.index,columns=eligible.columns)
            score=score.where(gate.eq(1),axis=0)
            view=dict(panels);view['_timing']=timing;view['_selection_policy']=current['stock_selection_policies'][selections[key]]
            attach(view,p)
            sim=simulate(view,score,p)
            item=dict(strategy=key,title=catalog[key]['name'],variant=variant['id'],variant_label=variant['label'],selection_policy=selections[key],period=phase,params={**p,'selection_policy':selections[key]},score_file=paths[key],factor_list=catalog[key]['factor_list'],snapshot_hash=signature,**summarize(sim,p,current))
            item['target_pass']=bool(item['target_pass'] and item['max_traded_codes']<=current['targets']['max_replacements'])
            item['cash_day_fraction']=float(np.mean([x['holding_count']==0 for x in sim['daily_account']]))
            (folder/'runs').mkdir(exist_ok=True)
            write_json(folder/'runs'/(key+'_'+variant['id']+'_'+phase+'.json'),dict(**item,dates=[str(d.date()) for d in sim['nav_series'].index],nav=list(sim['nav_series'].values),trades=sim['trades'],daily_account=sim['daily_account'],rejected_orders=sim['rejections']))
            with ledger.open('a',encoding='utf-8') as handle:handle.write(json.dumps(item,ensure_ascii=False)+'\n')
            rows.append(item);m=item['metrics'];log(f"{spec['id']} {phase} {key}/{variant['id']} annual={m['annual_return']:.2%} excess={m['annual_excess_pp']:.2f}pp DD={m['max_drawdown']:.2%} turnover={item['annual_one_way_turnover']:.2f}")
            del score,view,sim;gc.collect();return item
        discovery=[run(key,variant,'discovery') for key in shortlist for variant in event['variants']]
        winners={key:max([r for r in discovery if r['strategy']==key],key=lambda r:(r['target_pass'],r['frequency_pass'],r['metrics']['calmar']))['variant'] for key in shortlist}
        write_json(folder/'frozen_selection.json',dict(selection=winners,selection_data_end=spec['discovery']['end'],validation_is_fresh_blind=False))
        selected={key:next(v for v in event['variants'] if v['id']==winners[key]) for key in shortlist}
        result=dict(round_id=spec['id'],selection=winners,validation=[run(key,selected[key],'validation') for key in shortlist],full=[run(key,selected[key],'full') for key in shortlist],stress=[run(key,selected[key],'full',True) for key in shortlist])
        result['experiments']=rows;write_json(folder/'results.json',result)
        if not (folder/'platform_archives.json').exists():archive_finalists(current,folder,panels,eligible,signature,result)
        all_rounds.append(result)
    write_json(out/'results.json',dict(rounds=all_rounds,snapshot_hash=signature,validation_is_fresh_blind=False))
    flat=[r for result in all_rounds for r in result['experiments']]
    pd.DataFrame([{**{k:r[k] for k in ['strategy','variant','period','target_pass','max_holdings','max_traded_codes','annual_one_way_turnover','minimum_regular_trade_gap','minimum_regular_after_any_gap','emergency_exit_dates','cash_day_fraction']},**r['metrics']} for r in flat]).to_csv(out/'results.csv',index=False,encoding='utf-8-sig')
    log('EVENT RESEARCH COMPLETE '+str(out))

if __name__=='__main__':main()
