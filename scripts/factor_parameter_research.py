"""Freeze training-only parameter stability selections, then test real ledgers."""
import argparse
from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config,factor_definitions
from factors.research_controls import policy as controls
from scripts.mainboard_repair_research import load_cached_snapshot,canonical_hash
from scripts.mainboard_strategy_research import write_json,log
from scripts.four_axis_factor_mining import labels,valuation_panels
from backtest.research_factor_search import parameter_grid,raw_parameter,measure,select_training


def run_scan(cfg,base,out,panels,eligible,signature):
    definitions={**controls()['legacy_indicators'],**factor_definitions()}
    value_view,value_codes=valuation_panels(panels,base)
    targets=labels(panels,base);value_targets=labels(value_view,base)
    source=ROOT/base['valuation_source']['db']
    identity=canonical_hash(dict(config=cfg,base=base,definitions=definitions,snapshot=signature,
                                 valuation=[source.stat().st_size,source.stat().st_mtime_ns]))
    (out/'scan').mkdir(exist_ok=True)
    grid=[row for category,families in base['categories'].items() for family,spec in families.items()
          for row in parameter_grid(category,family,spec,cfg,definitions)]
    write_json(out/'experiment_manifest.json',dict(identity=identity,snapshot_hash=signature,config=cfg,base_policy=base,grid=grid,
              selection_uses_validation=False,validation_is_fresh_blind=False,known_prior_full_history_exploration=True))
    records={r['id']:[] for r in cfg['rounds']}
    for number,row in enumerate(grid,1):
        file=out/'scan'/(row['id']+'.json')
        old=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}
        if old.get('identity')==identity:
            for key,value in old['rounds'].items():records[key].append(value)
            continue
        view=value_view if row['category']=='valuation' else panels
        mask=eligible[value_codes] if row['category']=='valuation' else eligible
        raw=raw_parameter(row,view);data={}
        for rd in cfg['rounds']:
            item=dict(**row,**measure(raw,mask,value_targets if row['category']=='valuation' else targets,rd['discovery'],base,cfg))
            records[rd['id']].append(item);data[rd['id']]=item
        write_json(file,dict(identity=identity,rounds=data))
        del raw;gc.collect()
        if number%10==0 or number==1 or number==len(grid):log(f'PARAM SCAN {number}/{len(grid)} '+row['id'])
    for rd in cfg['rounds']:
        folder=out/rd['id'];folder.mkdir(exist_ok=True)
        measured,shortlists=select_training(records[rd['id']],cfg)
        family_best=[rows[0] for rows in shortlists.values() if rows]
        finalists=[]
        for category in base['categories']:
            families=sorted([r for r in family_best if r['category']==category],key=lambda r:r['robust_score'],reverse=True)
            for r in families[:cfg['search']['finalists_per_category']]:
                choices=shortlists[r['family_id']]
                default=next(item for item in measured if item['family_id']==r['family_id'] and item['is_default'])
                finalists.append(dict(family_id=r['family_id'],category=category,label=r['label'],parameters=choices,default=default))
        frozen=dict(identity=identity,round=rd,selection_uses_validation=False,validation_is_fresh_blind=False,
                    search_trials=len(grid),robust_families=len(family_best),finalists=finalists)
        write_json(folder/'training_scan.json',dict(identity=identity,records=measured,shortlists=shortlists))
        write_json(folder/'frozen_parameters.json',frozen)
        pd.DataFrame([dict(id=r['id'],family_id=r['family_id'],parameter=r['parameter'],value=r['value'],is_default=r['is_default'],rank_ic=r['rank_ic'],
            q_value=r['q_value'],point_pass=r['point_pass'],robust_pass=r['robust_pass'],neighbor_fraction=r['neighbor_fraction'],robust_score=r['robust_score'],
            top5_excess=r['tails']['5']['mean_forward_excess'],coverage=r['coverage']) for r in measured]).to_csv(folder/'training_parameters.csv',index=False,encoding='utf-8-sig')
        log(f"FROZEN {rd['id']}: {len(family_best)} robust families, {len(finalists)} finalists")
    return value_view,value_codes


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='factor_parameter_research');parser.add_argument('--stage',choices=['scan','portfolio','report','all'],default='all');args=parser.parse_args(argv)
    cfg=read_config(args.config);base=read_config(cfg['base_config']);out=ROOT/cfg['output_dir'];out.mkdir(parents=True,exist_ok=True)
    if args.stage in ('scan','all'):
        panels,eligible,signature,audit=load_cached_snapshot(base);write_json(out/'data_audit.json',audit)
        run_scan(cfg,base,out,panels,eligible,signature)
    if args.stage in ('portfolio','all'):
        from scripts.factor_parameter_portfolios import run
        run(cfg,base,out)
    if args.stage in ('report','all'):
        from scripts.factor_parameter_report import run
        run(cfg,base,out)


if __name__=='__main__':main()
