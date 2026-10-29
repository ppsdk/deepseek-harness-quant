"""Config-driven technical research: whole-period or chronological evaluation."""
import argparse
from copy import deepcopy
import gc
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config,factor_definitions
from backtest.research_technical import candidate
from scripts.mainboard_repair_research import load_cached_snapshot,build_pool_masks,load_factor,finish_summary,canonical_hash
from scripts.mainboard_strategy_research import execute,write_json,log


def assess(sim,params,policy):
    row=finish_summary(sim,params,policy)
    accept=policy['technical_acceptance']
    row['turnover_pass']=bool(row['annual_one_way_turnover']<=accept['max_annual_one_way_turnover'])
    row['activity_pass']=bool(row['average_exposure']>=accept['min_average_exposure'] and row['trade_dates']>=accept['min_trade_dates'])
    row['profitable']=bool(row['metrics']['total_return']>0)
    row['technical_constraints_pass']=bool(row['drawdown_pass'] and row['frequency_pass'] and row['holding_count_pass'] and row['trade_count_pass'] and row['turnover_pass'] and row['activity_pass'])
    # Do not reuse the old 20%-CAGR target for this separate technical study.
    row['target_pass']=row['technical_constraints_pass']
    row['return_target_pass']=None
    row['return_evaluation_version']='technical_risk_turnover_no_fixed_annual_threshold'
    return row


def ranking(row):
    return (row['activity_pass'],row['technical_constraints_pass'],row['profitable'],
            row['metrics']['calmar'],-row['annual_one_way_turnover'],row['metrics']['annual_return'])


def overall_ranking(base,stress):
    """Use whole-period base and stress constraints, without a year-specific gate."""
    return (base['activity_pass'] and stress['activity_pass'],
            base['technical_constraints_pass'] and stress['technical_constraints_pass'],
            base['profitable'] and stress['profitable'],
            min(base['metrics']['calmar'],stress['metrics']['calmar']),
            -max(base['annual_one_way_turnover'],stress['annual_one_way_turnover']))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='mainboard_technical_research')
    args=parser.parse_args(argv)
    experiment=read_config(args.config);policy=read_config(experiment['base_config'])
    overall=experiment.get('evaluation_mode')=='overall'
    policy.update(output_dir=experiment['output_dir'],rounds=[] if overall else experiment.get('rounds',policy['rounds']),technical_acceptance=experiment['acceptance'])
    out=ROOT/policy['output_dir'];out.mkdir(parents=True,exist_ok=True)
    panels,eligible,signature,audit=load_cached_snapshot(policy)
    write_json(out/'policy.json',dict(experiment=experiment,base_policy=policy,snapshot_hash=signature))
    write_json(out/'data_audit.json',audit)
    pools,_=build_pool_masks(panels,eligible,policy);definitions=factor_definitions()
    paths={}
    for key,spec in experiment['candidates'].items():
        source=experiment.get('signal_cache_sources',{}).get(key)
        if source:
            source=ROOT/source
            meta=json.loads((source.parent/'policy.json').read_text(encoding='utf-8'))
            fields=['score','_exit']+(['_entry'] if spec['mode']=='event' else [])
            if (meta['snapshot_hash']==signature and meta['experiment']['candidates'].get(key)==spec
                    and meta['base_policy']['universe']==policy['universe']
                    and all((source/f'{key}_{f}.npy').exists() for f in fields)):
                paths[key]={f:source/f'{key}_{f}.npy' for f in fields}
                log('READ ONLY SIGNAL CACHE '+key)
                continue
        loader=lambda name:load_factor(name,panels,eligible,policy,definitions)[0]
        score,signals=candidate(panels,eligible,pools[spec['risk_pool']],spec,loader)
        paths[key]={}
        for field,value in {'score':score,**signals}.items():
            path=out/'cache'/f'{key}_{field}.npy';np.save(path,value.to_numpy());paths[key][field]=path
        del score,signals;gc.collect()
    allrows=[];rounds=[]
    round_specs=[dict(id='overall')] if overall else experiment['rounds']
    for rd in round_specs:
        folder=out/rd['id'];(folder/'runs').mkdir(parents=True,exist_ok=True)
        rows=[]
        def run(key,variant,phase,stress=False):
            spec=experiment['candidates'][key];params={**experiment['execution'],**spec['execution'],**variant}
            if stress:params.update(policy['stress'])
            view=dict(panels)
            for field,path in paths[key].items():
                if field!='score':view[field]=pd.DataFrame(np.load(path,mmap_mode='r'),index=eligible.index,columns=eligible.columns)
            score=pd.DataFrame(np.load(paths[key]['score'],mmap_mode='r'),index=eligible.index,columns=eligible.columns)
            period=policy['periods']['full'] if phase=='full' else rd[phase]
            sim,p=execute(view,score,period,params,policy,attach_cadence=False)
            label=phase+('_stress' if stress else '')
            result=dict(round_id=rd['id'],strategy=key,title=spec['label'],variant=variant['id'],period=label,params=p,
                        snapshot_hash=signature,config_hash=canonical_hash(experiment),**assess(sim,p,policy))
            path=folder/'runs'/f'{key}_{variant["id"]}_{label}.json'
            write_json(path,dict(**result,dates=[str(d.date()) for d in sim['nav_series'].index],nav=list(sim['nav_series'].values),trades=sim['trades'],daily_account=sim['daily_account'],rejected_orders=sim['rejections']))
            rows.append(result);allrows.append(result)
            m=result['metrics'];log(f"TECH {rd['id']} {key}/{variant['id']} {label}: CAGR={m['annual_return']:.2%} DD={m['max_drawdown']:.2%} TURN={result['annual_one_way_turnover']:.2f} PASS={result['target_pass']}")
            return result
        if overall:
            pairs=[]
            for key in experiment['candidates']:
                for variant in experiment['variants']:
                    base=run(key,variant,'full');stress=run(key,variant,'full',True)
                    pairs.append(dict(strategy=key,variant=variant['id'],base=base,stress=stress,
                                      joint_risk_pass=bool(base['technical_constraints_pass'] and stress['technical_constraints_pass']),
                                      positive_net_return_pass=bool(base['profitable'] and stress['profitable'])))
            pairs.sort(key=lambda pair:overall_ranking(pair['base'],pair['stress']),reverse=True)
            write_json(folder/'overall_ranking.json',dict(evaluation_mode='overall',period=policy['periods']['full'],
                       annual_return_target=None,single_year_validation_gate=False,ranking=pairs))
            rounds.append(dict(round=rd,results=rows,ranking=pairs))
            continue
        discovery=[run(key,variant,'discovery') for key in experiment['candidates'] for variant in experiment['variants']]
        best=[max([r for r in discovery if r['strategy']==key],key=ranking) for key in experiment['candidates']]
        finalists=sorted(best,key=ranking,reverse=True)[:experiment['finalists']]
        frozen=dict(round=rd,selection_uses_validation=False,validation_is_fresh_blind=False,
                    selection=[dict(strategy=r['strategy'],variant=r['variant']) for r in finalists],discovery_ranking=sorted(best,key=ranking,reverse=True),config_hash=canonical_hash(experiment))
        write_json(folder/'frozen_selection.json',frozen)
        for r in finalists:
            variant=next(v for v in experiment['variants'] if v['id']==r['variant'])
            run(r['strategy'],variant,'validation');run(r['strategy'],variant,'validation',True);run(r['strategy'],variant,'full');run(r['strategy'],variant,'full',True)
        rounds.append(dict(round=rd,frozen=frozen,results=rows))
    write_json(out/'results.json',dict(snapshot_hash=signature,rounds=rounds,results=allrows,
               evaluation_mode='overall' if overall else 'chronological',single_year_validation_gate=not overall,
               sample_type='in_sample_full_period' if overall else 'historical_chronological_replay',
               validation_is_fresh_blind=False,technical_return_target=None))
    pd.DataFrame([dict(round_id=r['round_id'],strategy=r['strategy'],variant=r['variant'],period=r['period'],**r['metrics'],turnover=r['annual_one_way_turnover'],average_exposure=r['average_exposure'],technical_constraints_pass=r['technical_constraints_pass'],profitable=r['profitable'],trade_count=r['trade_count']) for r in allrows]).to_csv(out/'results.csv',index=False,encoding='utf-8-sig')


if __name__=='__main__':main()
