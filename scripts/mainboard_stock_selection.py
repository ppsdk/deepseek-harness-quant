"""Compare causal stock-selection rules in the user's two rolling windows.

The same validation years have already been inspected in earlier exploration;
these are chronological replays, not a claim of a new untouched holdout.
"""
import gc
import json
from pathlib import Path
import sys
import time
from copy import deepcopy

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from scripts.mainboard_strategy_research import load_snapshot,write_json,log,execute,summarize,archive_finalists


def masked_score(key,rule_id,rule,panels,eligible,policy,out):
    cache=ROOT/policy['output_dir']/'cache'
    raw=pd.DataFrame(np.load(cache/('score_'+key+'.npy'),mmap_mode='r'),index=eligible.index,columns=eligible.columns)
    if rule.get('risk_filter'):
        mask=eligible.copy()
        for name,threshold in [('lowvol_60',rule['max_vol_percentile']),('research_gap_risk',rule['max_gap_percentile'])]:
            factor=pd.DataFrame(np.load(cache/('factor_'+name+'.npy'),mmap_mode='r'),index=eligible.index,columns=eligible.columns)
            mask &= factor.where(eligible).rank(axis=1,pct=True).le(threshold)
            del factor;gc.collect()
        trend=panels['close']/panels['close'].rolling(rule['trend_period']).mean()-1
        mask &= trend.ge(rule['trend_floor'])
        raw=raw.where(mask)
        del mask,trend;gc.collect()
    name='selection_'+key+'_'+rule_id+'.npy'
    np.save(cache/name,raw.to_numpy())
    latest=raw.iloc[-1].notna().sum()
    return name,int(latest)


def process_round(spec,policy,panels,eligible,signature,out):
    prior=ROOT/policy['output_dir']/spec['id']
    frozen=json.loads((prior/'frozen_selection.json').read_text(encoding='utf-8'))
    catalog=json.loads((prior/'frozen_candidates.json').read_text(encoding='utf-8'))['strategies']
    current=deepcopy(policy);current['round_id']='stock_selection_'+spec['id']
    current['periods'].update(discovery=spec['discovery'],validation=spec['validation'])
    current['reproduction_command']='python scripts/mainboard_stock_selection.py'
    folder=out/spec['id'];folder.mkdir(exist_ok=True)
    write_json(folder/'policy.json',current)
    write_json(folder/'frozen_candidates.json',dict(strategies={key:catalog[key] for key in frozen['finalists']}))
    ledger=folder/'experiments.jsonl'
    rows=[json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines() if line] if ledger.exists() else []
    paths={};counts={}
    for key in frozen['finalists']:
        for rule_id,rule in policy['stock_selection_policies'].items():
            paths[(key,rule_id)],counts[(key,rule_id)]=masked_score(key,rule_id,rule,panels,eligible,policy,folder)
    def batch(period,selection=None,stress=False):
        result=[]
        for key in frozen['finalists']:
            base=next(v for v in policy['variants'] if v['id']==frozen['variants'][key])
            for rule_id in policy['stock_selection_policies']:
                if selection and rule_id!=selection[key]:continue
                phase=period+('_stress' if stress else '')
                old=next((r for r in rows if r['strategy']==key and r['selection_policy']==rule_id and r['period']==phase),None)
                if old:result.append(old);continue
                score_name=paths[(key,rule_id)]
                score=pd.DataFrame(np.load(ROOT/policy['output_dir']/'cache'/score_name,mmap_mode='r'),index=eligible.index,columns=eligible.columns)
                variant={**base,'selection_policy':rule_id}
                if stress:variant.update(policy['stress'])
                sim,p=execute(panels,score,current['periods'][period],variant,current)
                item=dict(strategy=key,title=catalog[key]['name'],variant=base['id'],selection_policy=rule_id,
                    selection_label=policy['stock_selection_policies'][rule_id]['label'],period=phase,params=p,
                    score_file=score_name,factor_list=catalog[key]['factor_list'],snapshot_hash=signature,
                    **summarize(sim,p,current))
                filename=key+'_'+rule_id+'_'+phase+'.json'
                (folder/'runs').mkdir(exist_ok=True)
                write_json(folder/'runs'/filename,dict(**item,dates=[str(d.date()) for d in sim['nav_series'].index],
                    nav=list(sim['nav_series'].values),trades=sim['trades'],daily_account=sim['daily_account'],rejected_orders=sim['rejections']))
                with ledger.open('a',encoding='utf-8') as handle:handle.write(json.dumps(item,ensure_ascii=False)+'\n')
                rows.append(item);result.append(item)
                m=item['metrics'];log(f"{spec['id']} {phase} {key}/{rule_id} CAGR={m['annual_return']:.2%} excess={m['annual_excess_pp']:.2f}pp DD={m['max_drawdown']:.2%} turnover={item['annual_one_way_turnover']:.2f}")
                del score,sim;gc.collect()
        return result
    discovery=batch('discovery')
    selection={key:max([r for r in discovery if r['strategy']==key],
        key=lambda r:(r['target_pass'],r['max_holdings']<=policy['targets']['max_holdings'],r['frequency_pass'],r['metrics']['calmar']))['selection_policy'] for key in frozen['finalists']}
    write_json(folder/'frozen_selection.json',dict(selection=selection,selection_data_end=spec['discovery']['end'],validation_is_fresh_blind=False,
        note='只用发现期选规则；旧选股方案的同一验证年份已被查看，本次属于有时间隔离的探索性回放。'))
    validation=batch('validation',selection)
    observation=batch('observation',selection)
    full=batch('full',selection)
    stress=batch('full',selection,True)
    result=dict(round_id=spec['id'],selection=selection,validation=validation,observation=observation,full=full,stress=stress,experiments=rows,
        latest_pool_counts=[dict(strategy=k,selection_policy=s,count=n) for (k,s),n in counts.items()],
        passing_validation=sum(row['target_pass'] for row in validation),validation_is_fresh_blind=False)
    write_json(folder/'results.json',result)
    pd.DataFrame([{**{k:r[k] for k in ['strategy','selection_policy','period','target_pass','max_holdings','average_exposure','annual_one_way_turnover']},**r['metrics']} for r in rows]).to_csv(folder/'results.csv',index=False,encoding='utf-8-sig')
    if not (folder/'platform_archives.json').exists():archive_finalists(current,folder,panels,eligible,signature,result)
    return result


def main():
    policy=read_config('mainboard_research');root=ROOT/policy['output_dir'];out=root/'stock_selection';out.mkdir(exist_ok=True)
    write_json(root/'policy.json',policy)
    write_json(out/'protocol.json',dict(policy=policy,scope='固定各轮已选因子和调仓方案，只比较3种选股机制；全部只读历史，不使用今天名单回填。',
        validation_is_fresh_blind=False,design_note='在已知前轮验证结果后提出，不能声称重新获得未触碰的盲测。'))
    panels,eligible,signature=load_snapshot(policy,root)
    results=[process_round(spec,policy,panels,eligible,signature,out) for spec in policy['rounds']]
    write_json(out/'results.json',dict(rounds=results,snapshot_hash=signature,validation_is_fresh_blind=False))
    log('STOCK SELECTION COMPLETE')


if __name__=='__main__':main()
