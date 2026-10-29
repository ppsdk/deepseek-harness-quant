"""Build low-correlation strategies, calibrate risk in training, freeze and replay."""
from copy import deepcopy
import argparse
import gc
import json
import math
from pathlib import Path
import sys
import time
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from backtest import research_runner as runner
from backtest.research_assembly import blend,signals,choose_complements,joint_ranking,stable_shortlist
from backtest.research_factor_search import raw_parameter
from backtest.research_timing import evaluate
from scripts.factor_parameter_budget import calibrate
from scripts.factor_parameter_portfolios import assess,read_execution
from scripts.four_axis_factor_audit import pca_summary
from scripts.four_axis_factor_mining import valuation_panels
from scripts.mainboard_repair_research import load_cached_snapshot,canonical_hash
from scripts.mainboard_strategy_research import write_json,log,benchmark_snapshot
from data.cache import DailyCache


def prepare_features(cfg,source,base,panels,eligible,signature,rd,folder):
    origin=ROOT/source['output_dir'];records=json.loads((origin/rd['id']/'training_scan.json').read_text(encoding='utf-8'))['records']
    core=next(r for r in records if r['id']==cfg['core_parameter_id']);best={}
    for row in records:
        if row['robust_pass'] and (row['family_id'] not in best or row['robust_score']>best[row['family_id']]['robust_score']):best[row['family_id']]=row
    scopes=list(dict.fromkeys(s['scope'] for s in cfg['compositions'].values()))
    needs_valuation='valuation_sample' in scopes
    candidates=[r for r in best.values() if r['family_id']!=core['family_id'] and (needs_valuation or r['category']!='valuation')]
    values,value_codes=valuation_panels(panels,base) if needs_valuation else (None,[])
    raws={};cache=folder/'cache';cache.mkdir(exist_ok=True)
    for row in [core,*candidates]:
        path=cache/('raw_'+row['id']+'.npy');view=values if row['category']=='valuation' else panels
        if not path.exists():np.save(path,raw_parameter(row,view).to_numpy())
        raws[row['id']]=pd.DataFrame(np.load(path,mmap_mode='r'),index=eligible.index,columns=view['close'].columns)
    slots={};audits={}
    for scope in scopes:
        codes=list(panels['close'].columns) if scope=='mainboard' else value_codes
        allowed=[core,*[r for r in candidates if (r['category']!='valuation' if scope=='mainboard' else r['category']=='valuation')]]
        mask=eligible[codes];dates=mask.index;pos=np.flatnonzero((dates>=rd['discovery']['start'])&(dates<=rd['discovery']['end']))[::cfg['sample_every_days']]
        flat=np.flatnonzero(mask.iloc[pos].to_numpy().ravel())
        if len(flat)>cfg['max_correlation_samples']:flat=flat[np.linspace(0,len(flat)-1,cfg['max_correlation_samples'],dtype=int)]
        columns={}
        for row in allowed:
            rank=(raws[row['id']][codes].iloc[pos]*row['direction']).where(mask.iloc[pos]).rank(axis=1,pct=True)
            columns[row['id']]=rank.to_numpy().ravel()[flat]
        frame=pd.DataFrame(columns).dropna();correlation=frame.corr()
        correlation.to_csv(folder/(scope+'_training_correlations.csv'),encoding='utf-8-sig')
        chosen=choose_complements(core,allowed,correlation,cfg['technical_categories'] if scope=='mainboard' else ['valuation'],
                                  cfg['max_abs_factor_correlation'],cfg['technical_complements'] if scope=='mainboard' else cfg['valuation_complements'])
        mapping={'core':core,**{('technical_' if scope=='mainboard' else 'valuation_')+str(i+1):r for i,r in enumerate(chosen)}}
        slots[scope]=mapping
        selected=[r['id'] for r in mapping.values()];matrix=correlation.loc[selected,selected].to_numpy();pca=pca_summary(frame[selected],cfg['pca_explained_variance'])
        audits[scope]=dict(discovery=rd['discovery'],selection_uses_validation=False,stocks=len(codes),samples=len(frame),
            selected_parameters=selected,slot_records=mapping,max_abs_factor_correlation=float(np.max(np.abs(matrix-np.eye(len(selected))))),
            correlation_limit=cfg['max_abs_factor_correlation'],pca=pca,
            scaler_state={'mean_':frame[selected].mean().tolist(),'scale_':frame[selected].std(ddof=0).tolist()},
            pca_used_for_return_prediction=False)
        log(f"ASSEMBLY FEATURES {rd['id']} {scope}: "+', '.join(selected))
    write_json(folder/'frozen_features.json',dict(snapshot_hash=signature,round=rd,audits=audits))
    compositions={};skipped=[]
    for key,spec in cfg['compositions'].items():
        mapping=slots[spec['scope']]
        if any(slot not in mapping for slot in spec['weights']):skipped.append(key);continue
        codes=list(panels['close'].columns) if spec['scope']=='mainboard' else value_codes
        raw={slot:raws[mapping[slot]['id']][codes] for slot in spec['weights']}
        records={slot:mapping[slot] for slot in spec['weights']}
        score=blend(raw,records,spec['weights'],eligible[codes]);path=cache/('score_'+key+'.npy');np.save(path,score.to_numpy())
        if spec.get('core_pool_size'):
            score=stable_shortlist(score,raw['core'],records['core']['direction'],eligible[codes],spec['core_pool_size']);np.save(path,score.to_numpy())
        compositions[key]=dict(id=key,**spec,records=records,score_path=str(path.relative_to(ROOT)),codes=codes)
        del score
    write_json(folder/'composition_manifest.json',dict(compositions=compositions,skipped_missing_complements=skipped))
    del raws,values;gc.collect()
    return compositions,audits


def run(cfg,source,base,out):
    panels,eligible,signature,data_audit=load_cached_snapshot(base);policy=read_execution(source);budget=read_config(cfg['budget_config'])
    write_json(out/'data_audit.json',data_audit)
    origin=json.loads((ROOT/source['output_dir']/'experiment_manifest.json').read_text(encoding='utf-8'))
    if origin['snapshot_hash']!=signature:raise ValueError('Assembly source snapshot mismatch')
    identity=canonical_hash(dict(config=cfg,source=source,budget=budget,source_identity=origin['identity'],snapshot=signature))
    write_json(out/'policy.json',dict(identity=identity,snapshot_hash=signature,config=cfg,source_config=source,base_policy=base,budget=budget,
        known_prior_validation_observation=True,selection_uses_validation=False,validation_is_fresh_blind=False))
    gates={};cache=DailyCache()
    for key in {v['timing_rule'] for v in cfg['execution_variants'].values()}-{'none'}:
        frame=cache.get_daily(policy['execution']['timing_index'],adjust='none').set_index('date').sort_index()
        gates[key]=evaluate(frame,key,dict(benchmark=policy['benchmark'],rules=policy['timing_rules'])).reindex(eligible.index)
    results=[];contracts=[];archive_candidates=[]
    for rd in source['rounds']:
        folder=out/rd['id'];folder.mkdir(exist_ok=True);(folder/'runs').mkdir(exist_ok=True)
        compositions,audits=prepare_features(cfg,source,base,panels,eligible,signature,rd,folder)
        contexts={}
        def context(composition,variant):
            key=(composition,variant)
            if key in contexts:return contexts[key]
            spec=compositions[composition];v=cfg['execution_variants'][variant];codes=spec['codes']
            view={k:p[codes] for k,p in panels.items()};score=pd.DataFrame(np.load(ROOT/spec['score_path'],mmap_mode='r'),index=eligible.index,columns=codes)
            view.update(signals(view,v));view['_selection_policy']={'retain_rank':int(math.ceil(source['portfolio']['primary_topn']*source['portfolio']['candidate_buffer']))}
            if v['timing_rule']!='none':
                gate=gates[v['timing_rule']];view['_timing']=pd.DataFrame(np.broadcast_to(gate.to_numpy()[:,None],score.shape),index=score.index,columns=codes)
                score=score.where(gate.eq(1),axis=0)
            contexts[key]=(view,score);return view,score
        def ledger(composition,variant,exposure,period,phase,stress=False):
            key=f'{composition}__{variant}__E{exposure}_{phase}'+('_stress' if stress else '')
            file=folder/'runs'/(key+'.json');stamp=canonical_hash(dict(identity=identity,round=rd,key=key,composition=compositions[composition],period=period))
            if file.exists():
                old=json.loads(file.read_text(encoding='utf-8'))
                if old.get('identity')==stamp:
                    r={k:v for k,v in old.items() if k not in ('dates','nav','trades','daily_account','rejected_orders')};results.append(r);return r
            v=cfg['execution_variants'][variant];view,score=context(composition,variant)
            p={**policy['execution'],**period,**{k:source['portfolio'][k] for k in ['commission_bps','slippage_bps','stamp_tax_bps','min_trade_interval_days','max_traded_codes_per_day']},
               **{k:v[k] for k in ['trade_mode','rebalance_days','timing_rule','stop_loss_pct','take_profit_pct','trailing_stop_pct']},
               'target_exposure_pct':exposure,'topn':source['portfolio']['primary_topn']}
            if stress:p.update(source['stress'])
            sim=runner.simulate(dict(view),score,p)
            r=dict(id=key,identity=stamp,round_id=rd['id'],composition=composition,variant=variant,label=compositions[composition]['label']+' · '+v['label'],
                scope=compositions[composition]['scope'],phase=phase,period=period,stress=stress,target_exposure_pct=exposure,params=p,
                archived=True,**assess(sim,p,source))
            write_json(file,dict(**r,dates=list(sim['nav_series'].index.strftime('%Y-%m-%d')),nav=list(sim['nav_series'].values),
                trades=sim['trades'],daily_account=sim['daily_account'],rejected_orders=sim['rejections']))
            results.append(r);m=r['metrics'];log(f"ASSEMBLY {rd['id']} {key}: CAGR={m['annual_return']:.2%} DD={m['max_drawdown']:.2%} TURN={r['annual_one_way_turnover']:.2f}")
            return r
        training=[]
        for composition in compositions:
            for variant in cfg['execution_variants']:
                full=ledger(composition,variant,budget['maximum_exposure_pct'],rd['discovery'],'discovery')
                calibration=calibrate(full,source,budget);exposure=calibration['exposure_pct']
                fit=ledger(composition,variant,exposure,rd['discovery'],'discovery_budget')
                pressure=ledger(composition,variant,exposure,rd['discovery'],'discovery_budget',True)
                training.append(dict(composition=composition,variant=variant,exposure=exposure,calibration=calibration,base=fit,pressure=pressure))
        best=[max([r for r in training if r['composition']==key],key=lambda r:joint_ranking(r['base'],r['pressure'])) for key in compositions]
        technical=sorted([r for r in best if compositions[r['composition']]['scope']=='mainboard'],key=lambda r:joint_ranking(r['base'],r['pressure']),reverse=True)[:cfg['technical_finalists']]
        valuation=sorted([r for r in best if r['composition']=='valuation_pair'],key=lambda r:joint_ranking(r['base'],r['pressure']),reverse=True)[:cfg['valuation_finalists']]
        chosen=technical+valuation
        contract=dict(identity=identity,round=rd,selection_uses_validation=False,validation_is_fresh_blind=False,
            chosen=chosen,composition_definitions=compositions,execution_variants=cfg['execution_variants'],training_combinations=len(training))
        write_json(folder/'frozen_strategies.json',contract);contracts.append(contract)
        for selection in chosen:
            key=selection['composition'];variant=selection['variant'];exposure=selection['exposure']
            periods=[('validation',rd['validation']),('continuous_forward',dict(start=rd['validation']['start'],end=source['observation']['end'])),
                     ('overall',base['research_windows']['full'])]
            for phase,period in periods:
                for stress in [False,True]:
                    row=ledger(key,variant,exposure,period,phase,stress)
                    if phase=='continuous_forward' and not stress:archive_candidates.append(dict(round_id=rd['id'],selection=selection,row=row))
            if key!='core':
                control_key='valuation_control' if compositions[key]['scope']=='valuation_sample' else 'core'
                for phase,period in periods:
                    matched_phase='matched_'+phase if compositions[key]['scope']=='valuation_sample' else 'matched_'+key+'_'+phase
                    for stress in [False,True]:ledger(control_key,variant,exposure,period,matched_phase,stress)
        # Matched-period reference uses the earlier fixed 45% technical core,
        # regardless of which newly assembled strategy wins discovery.
        for phase,period in [('validation',rd['validation']),('continuous_forward',dict(start=rd['validation']['start'],end=source['observation']['end'])),('overall',base['research_windows']['full'])]:
            for stress in [False,True]:ledger('core','monthly',cfg['reference_exposure_pct'],period,'reference_'+phase,stress)
        del contexts;gc.collect()
    write_json(out/'results.json',dict(identity=identity,snapshot_hash=signature,selection_uses_validation=False,validation_is_fresh_blind=False,contracts=contracts,records=results))
    pd.DataFrame([dict(round_id=r['round_id'],id=r['id'],composition=r['composition'],variant=r['variant'],scope=r['scope'],phase=r['phase'],stress=r['stress'],exposure=r['target_exposure_pct'],
        **r['metrics'],turnover=r['annual_one_way_turnover'],average_exposure=r['average_exposure'],constraints_pass=r['constraints_pass']) for r in results]).to_csv(out/'results.csv',index=False,encoding='utf-8-sig')
    return archive_candidates


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='mainboard_strategy_assembly');parser.add_argument('--stage',choices=['research','deliver','all'],default='all');args=parser.parse_args()
    cfg=read_config(args.config);source=read_config(cfg['source_config']);base=read_config(source['base_config']);out=ROOT/cfg['output_dir'];out.mkdir(parents=True,exist_ok=True)
    if args.stage in ('research','all'):run(cfg,source,base,out)
    if args.stage in ('deliver','all'):
        from scripts.strategy_assembly_delivery import deliver
        deliver(cfg,source,base,out)


if __name__=='__main__':main()
