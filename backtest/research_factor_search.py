"""Training-only factor parameter grids and chronological, purged IC tests."""
from copy import deepcopy
import numpy as np
import pandas as pd
from factors.research_indicators import calculate
from scripts.four_axis_factor_mining import inspect_factor,fdr


def parameter_grid(category,family,spec,cfg,definitions):
    key=category+'_'+family
    name=spec.get('source_factor','axis_'+key)
    definition=deepcopy(definitions.get(name,spec))
    if spec.get('source_factor') is None:definition['operation']='axis_'+spec['operation']
    definition.setdefault('params',{})
    override=cfg['search']['overrides'].get(key)
    if override:parameter=override['parameter'];values=override['values']
    elif definition['operation']=='axis_relative_valuation':parameter='n';values=cfg['search']['relative_valuation_periods']
    elif 'n' in definition['params']:parameter='n';values=cfg['search']['periods']
    else:parameter='smooth_n';values=cfg['search']['smoothing_periods']
    default=definition['params'].get(parameter,1)
    rows=[]
    for value in sorted(set([*values,default])):
        candidate=deepcopy(definition);candidate['params'][parameter]=value
        rows.append(dict(id=key+'__'+parameter+str(value),family_id=key,category=category,label=spec['label'],
                         factor_key=name,parameter=parameter,value=value,is_default=value==default,definition=candidate))
    return rows


def raw_parameter(record,panels):
    return calculate(record['definition'],panels).replace([np.inf,-np.inf],np.nan).where(panels['close'].notna())


def restrict_targets(targets,dates,window):
    """Every label's realization, including its exit open, stays inside window."""
    result={}
    for h,(pos,future,valid) in targets.items():
        keep=(dates[pos]>=window['start'])&(dates[pos+h+1]<=window['end'])
        result[h]=(pos[keep],future.iloc[np.flatnonzero(keep)],valid[keep])
    return result


def tail_metrics(raw,eligible,target,direction,counts):
    pos,future,executable=target
    # Choose using signal-day information. Unavailable future endpoints remove
    # the label, never promote a lower-ranked stock into the selected tail.
    rank=(raw.iloc[pos]*direction).where(eligible.iloc[pos]).rank(axis=1,method='first',ascending=False)
    observed=future.where(executable);benchmark=observed.where(eligible.iloc[pos]).mean(axis=1)
    result={}
    for topn in counts:
        chosen=rank.le(topn);ret=observed.where(chosen).mean(axis=1)
        result[str(topn)]=dict(mean_forward_return=nullable(ret.mean()),mean_forward_excess=nullable((ret-benchmark).mean()),
            positive_fraction=nullable(ret.dropna().gt(0).mean()),valid_windows=int(ret.notna().sum()),
            unavailable_label_fraction=float((chosen&observed.isna()).sum().sum()/max(1,chosen.sum().sum())))
    return result


def nullable(value):
    return float(value) if np.isfinite(value) else None


def measure(raw,eligible,targets,window,base,cfg,*,direction=None):
    policy=deepcopy(base);policy['research_windows']={'full':window}
    allowed=restrict_targets(targets,raw.index,window)
    result=inspect_factor(raw,eligible,allowed,policy)
    h=str(cfg['search']['primary_horizon']);primary=result['horizons'][h]
    result['tails']=tail_metrics(raw,eligible,allowed[int(h)],primary['direction'] if direction is None else direction,cfg['portfolio']['top_counts'])
    result['last_label_exit']={str(k):str(raw.index[pos[-1]+k+1]) if len(pos) else None for k,(pos,_,_) in allowed.items()}
    return result


def select_training(records,cfg):
    rule=cfg['search'];h=str(rule['primary_horizon'])
    adjusted=fdr([r['horizons'][h]['p_value'] for r in records])
    for record,q in zip(records,adjusted):
        m=record['horizons'][h];ic=m['rank_ic_mean'];sign=m['direction']
        same=sum(x['rank_ic_mean'] is not None and x['rank_ic_mean']*sign>0 for x in record['horizons'].values())
        tests=dict(coverage=record['coverage']>=rule['min_coverage'],magnitude=ic is not None and abs(ic)>=rule['min_abs_rank_ic'],
            yearly=m['year_direction_fraction']>=rule['min_year_stability'],observations=m['observations']>=rule['min_observations'],
            fdr=q<=rule['fdr'],horizons=same>=2,spread=(m['signed_top_bottom_spread'] or 0)>0,
            top5_excess=(record['tails'][str(cfg['portfolio']['primary_topn'])]['mean_forward_excess'] or 0)>0)
        record.update(q_value=q,direction=sign,rank_ic=ic,gates=tests,point_pass=all(tests.values()))
    grouped={}
    for record in records:grouped.setdefault(record['family_id'],[]).append(record)
    for group in grouped.values():
        ordered=sorted(group,key=lambda r:r['value'])
        for i,record in enumerate(ordered):
            neighbors=[r for r in ordered[max(0,i-1):i+2] if r is not record]
            good=[r for r in neighbors if r['point_pass'] and r['direction']==record['direction']]
            fraction=len(good)/max(1,len(neighbors))
            record.update(neighbor_ids=[r['id'] for r in neighbors],same_direction_passing_neighbors=len(good),neighbor_fraction=fraction,
                robust_pass=bool(record['point_pass'] and len(good)>=rule['min_same_direction_neighbors'] and fraction>=rule['min_neighbor_fraction']),
                robust_score=float(np.quantile([abs(record['rank_ic'] or 0),*[abs(r['rank_ic']) for r in good]],rule['robust_quantile'])))
    shortlists={key:sorted([r for r in group if r['robust_pass']],key=lambda r:(r['robust_score'],abs(r['rank_ic'])),reverse=True)[:rule['parameters_per_family']]
                for key,group in grouped.items()}
    return records,shortlists
