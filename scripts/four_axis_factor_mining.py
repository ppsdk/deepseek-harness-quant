"""Full-period four-axis factor screening, HAC inference and explicit failures."""
import argparse
from contextlib import closing
import gc
import json
import math
from pathlib import Path
import sqlite3
import sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config,factor_definitions
from factors.research_axis import calculate
from factors.research_evaluation import axis_evaluation
from scripts.mainboard_repair_research import load_cached_snapshot,load_factor
from scripts.mainboard_strategy_research import write_json,log


def hac(values,lag):
    values=np.asarray(values,dtype=float);values=values[np.isfinite(values)];n=len(values)
    if n<3:return dict(t=None,p=1.)
    centered=values-values.mean();variance=np.dot(centered,centered)/n
    for k in range(1,min(lag,n-1)+1):variance+=2*(1-k/(lag+1))*np.dot(centered[k:],centered[:-k])/n
    se=np.sqrt(max(variance,0)/n)
    if se==0:return dict(t=None,p=1.)
    t=float(values.mean()/se)
    return dict(t=t,p=math.erfc(abs(t)/math.sqrt(2)))


def fdr(values):
    p=np.asarray(values,dtype=float);order=np.argsort(p);n=len(p);adjusted=np.empty(n)
    adjusted[order]=np.minimum.accumulate((p[order]*n/np.arange(1,n+1))[::-1])[::-1].clip(0,1)
    return adjusted.tolist()


def labels(panels,cfg):
    dates=panels['close'].index;full=cfg['research_windows']['full'];pos=np.flatnonzero((dates>=full['start'])&(dates<=full['end']))
    opening=panels['open'].to_numpy();tradable=(panels['volume'].to_numpy()>0)&(panels['high'].to_numpy()>panels['low'].to_numpy())&(opening>0)
    result={}
    for h in cfg['ic']['horizons']:
        chosen=pos[:-(h+1):cfg['ic']['sample_every_days']]
        future=opening[chosen+h+1]/opening[chosen+1]-1
        valid=tradable[chosen+1]&tradable[chosen+h+1]&np.isfinite(future)
        result[h]=(chosen,pd.DataFrame(future,index=dates[chosen],columns=panels['close'].columns),valid)
    return result


def inspect_factor(raw,eligible,targets,cfg):
    def finite(value):return float(value) if pd.notna(value) and np.isfinite(value) else None
    coverage=(raw.notna()&eligible).sum(axis=1)/eligible.sum(axis=1).replace(0,np.nan)
    start=cfg['research_windows']['full']['start'];end=cfg['research_windows']['full']['end']
    record=dict(coverage=float(coverage.loc[start:end].mean()),horizons={})
    for horizon,(pos,future,executable) in targets.items():
        pair=raw.iloc[pos];mask=pair.notna()&eligible.iloc[pos]&executable
        ranks=pair.where(mask).rank(axis=1,pct=True);frank=future.where(mask).rank(axis=1)
        ic=ranks.corrwith(frank,axis=1).where(mask.sum(axis=1)>=cfg['ic']['min_stocks']).dropna()
        stats=hac(ic,math.ceil((horizon+1)/cfg['ic']['sample_every_days'])-1)
        direction=1 if len(ic) and ic.mean()>0 else -1
        yearly=ic.groupby(pd.to_datetime(ic.index).year).mean()
        high=future.where(ranks.ge(.8)).mean(axis=1);low=future.where(ranks.le(.2)).mean(axis=1)
        spread=high-low
        record['horizons'][str(horizon)]=dict(rank_ic_mean=float(ic.mean()) if len(ic) else None,
            rank_ic_ir=float(ic.mean()/ic.std()) if len(ic)>1 and ic.std()>0 else None,
            observations=len(ic),direction=direction,
            ic_positive_rate=float((ic>0).mean()) if len(ic) else None,
            year_direction_fraction=float((yearly*direction>0).mean()) if len(yearly) else 0.,
            yearly_rank_ic={str(k):float(v) for k,v in yearly.items()},hac_t=stats['t'],p_value=stats['p'],
            signed_top_bottom_spread=finite(spread.reindex(ic.index).mean()*direction) if len(ic) else None,
            signed_preferred_group_excess=finite(((high if direction==1 else low)-future.where(mask).mean(axis=1)).reindex(ic.index).mean()) if len(ic) else None)
    return record


def valuation_panels(panels,cfg):
    universe=json.loads((ROOT/cfg['output_dir']/'valuation_universe.json').read_text(encoding='utf-8'))['codes']
    codes=[c for c in panels['close'].columns if c in set(universe)]
    view={k:v.loc[:,codes] for k,v in panels.items() if isinstance(v,pd.DataFrame)}
    path=ROOT/cfg['valuation_source']['db']
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as con:
        frame=pd.read_sql_query('SELECT code,date,pe,pb,ps,pcf,peg,pe_static FROM valuations WHERE source=? ORDER BY date',con,params=(cfg['valuation_source']['source'],))
    for field in ('pe','pb','ps','pcf','peg','pe_static'):
        view['valuation:'+field]=frame.pivot(index='date',columns='code',values=field).reindex(index=panels['close'].index,columns=codes)
    return view,codes


def finalize(records,cfg):
    rule=cfg['effectiveness'];primary=str(rule['primary_horizon'])
    q=fdr([r['horizons'][primary]['p_value'] for r in records])
    for r,adjusted in zip(records,q):
        m=r['horizons'][primary];r['q_value']=adjusted
        same=sum(bool(h['rank_ic_mean'] is not None and h['rank_ic_mean']*m['direction']>0) for h in r['horizons'].values())
        tests=dict(coverage=r['coverage']>=rule['min_coverage'],magnitude=m['rank_ic_mean'] is not None and abs(m['rank_ic_mean'])>=rule['min_abs_rank_ic'],
            yearly=m['year_direction_fraction']>=rule['min_year_direction_fraction'],observations=m['observations']>=rule['min_observations'],
            fdr=adjusted<=rule['fdr'],horizons=same>=rule['min_same_direction_horizons'],spread=m['signed_top_bottom_spread'] is not None and m['signed_top_bottom_spread']>0)
        r.update(effective=all(tests.values()),gates=tests,direction=m['direction'],rank_ic=m['rank_ic_mean'],year_stability=m['year_direction_fraction'],hac_t=m['hac_t'],spread=m['signed_top_bottom_spread'])
        r['evaluation']=axis_evaluation(r)
    return sorted(records,key=lambda r:(r['category'],not r['effective'],-abs(r['rank_ic'] or 0)))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='four_axis_factor_mining');parser.add_argument('--categories',default='momentum,volatility,liquidity,valuation');args=parser.parse_args(argv)
    cfg=read_config(args.config);out=ROOT/cfg['output_dir'];(out/'factors').mkdir(parents=True,exist_ok=True)
    panels,eligible,signature,audit=load_cached_snapshot(cfg);definitions=factor_definitions();base_targets=labels(panels,cfg)
    write_json(out/'policy.json',cfg);write_json(out/'data_audit.json',audit)
    old=json.loads((out/'factor_results.json').read_text(encoding='utf-8'))['records'] if (out/'factor_results.json').exists() else []
    selected=args.categories.split(',');records=[r for r in old if r['category'] not in selected]
    for category in selected:
        if category=='valuation':
            view,codes=valuation_panels(panels,cfg);eligible_view=eligible[codes];targets=labels(view,cfg)
        else:view,eligible_view,targets=panels,eligible,base_targets
        for family,spec in cfg['categories'][category].items():
            key=category+'_'+family
            if spec.get('source_factor'):
                raw,path,reused=load_factor(spec['source_factor'],view,eligible_view,cfg,definitions)
            else:raw=calculate(spec,view)
            raw=raw.replace([np.inf,-np.inf],np.nan).where(view['close'].notna())
            path=out/'factors'/f'{key}.npy';np.save(path,raw.to_numpy())
            record=dict(category=category,family=family,id=key,label=spec['label'],definition=spec,universe_stocks=len(eligible_view.columns),
                        **inspect_factor(raw,eligible_view,targets,cfg))
            records.append(record)
            write_json(out/'partial_results.json',dict(snapshot_hash=signature,records=records))
            m=record['horizons'][str(cfg['effectiveness']['primary_horizon'])];log(f"AXIS {key}: IC20={m['rank_ic_mean']} years={m['year_direction_fraction']:.2f} coverage={record['coverage']:.2%}")
            del raw;view.pop('_factor_cache',None);gc.collect()
        if category=='valuation':del view,eligible_view,targets;gc.collect()
    records=finalize(records,cfg)
    counts={k:sum(r['effective'] for r in records if r['category']==k) for k in cfg['categories']}
    write_json(out/'factor_results.json',dict(snapshot_hash=signature,records=records,effective_counts=counts,selection_uses_full_history=True,
               ml_requires_training_only_reselection=True,valuation_scope='deterministic_code_sample',target_met=all(v>=cfg.get('minimum_per_category',5) for v in counts.values())))
    pd.DataFrame([{k:r.get(k) for k in ['category','family','id','label','universe_stocks','effective','direction','rank_ic','coverage','year_stability','hac_t','q_value','spread']} for r in records]).to_csv(out/'factor_results.csv',index=False,encoding='utf-8-sig')
    log('COUNTS '+str(counts))


if __name__=='__main__':main()
