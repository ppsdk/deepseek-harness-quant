"""Cross-sectional rank correlation and descriptive PCA; never an ML fit artifact."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from scripts.mainboard_repair_research import load_cached_snapshot
from scripts.mainboard_strategy_research import write_json


def representatives(records,corr,cap,categories):
    """Round-robin category priority, refusing missing or excessive correlations."""
    chosen=[];pending={c:[r['id'] for r in records if r['category']==c and r['effective']] for c in categories}
    while any(pending.values()):
        for category in categories:
            if not pending[category]:continue
            name=pending[category].pop(0)
            values=corr.loc[name,chosen]
            if not chosen or (values.notna().all() and values.abs().le(cap).all()):chosen.append(name)
    return chosen


def pca_summary(frame,variance):
    clean=frame.dropna()
    if len(clean)<max(10,len(frame.columns)*2):return dict(state='insufficient_complete_rows',complete_rows=len(clean))
    z=StandardScaler().fit_transform(clean)
    fit=PCA(n_components=float(variance),svd_solver='full').fit(z)
    pcs=fit.transform(z);off=np.corrcoef(pcs,rowvar=False)
    max_corr=float(np.max(np.abs(off-np.eye(pcs.shape[1])))) if pcs.shape[1]>1 else 0.
    return dict(state='complete',complete_rows=len(clean),input_features=list(frame.columns),input_dimensions=len(frame.columns),
        components=fit.n_components_,explained_variance_ratio=fit.explained_variance_ratio_.tolist(),
        cumulative_explained_variance=float(fit.explained_variance_ratio_.sum()),max_abs_training_pc_corr=max_corr,
        loadings={f'PC{i+1}':dict(zip(frame.columns,map(float,row))) for i,row in enumerate(fit.components_)})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='four_axis_factor_mining');args=parser.parse_args(argv)
    cfg=read_config(args.config);out=ROOT/cfg['output_dir'];settings=cfg['correlation']
    result=json.loads((out/'factor_results.json').read_text(encoding='utf-8'));records=[r for r in result['records'] if r['effective']]
    panels,eligible,signature,_=load_cached_snapshot(cfg)
    if result['snapshot_hash']!=signature:raise ValueError('Factor statistics and price snapshot differ')
    universe=json.loads((out/'valuation_universe.json').read_text(encoding='utf-8'))['codes']
    codes=[c for c in panels['close'].columns if c in set(universe)];indices=panels['close'].columns.get_indexer(codes)
    dates=panels['close'].index;window=cfg['research_windows']['full']
    pos=np.flatnonzero((dates>=window['start'])&(dates<=window['end']))[::settings['sample_every_days']]
    mask=eligible[codes].iloc[pos];flat=np.flatnonzero(mask.to_numpy().ravel());limit=settings['max_samples']
    if len(flat)>limit:flat=flat[np.linspace(0,len(flat)-1,limit,dtype=int)]
    samples={}
    for record in records:
        raw=np.load(out/'factors'/(record['id']+'.npy'),mmap_mode='r')
        values=raw[pos] if record['category']=='valuation' else raw[np.ix_(pos,indices)]
        rank=pd.DataFrame(values,index=mask.index,columns=codes).where(mask).rank(axis=1,pct=True)
        samples[record['id']]=rank.to_numpy().ravel()[flat]
    sample=pd.DataFrame(samples)
    sample.corr(min_periods=settings['min_pair_observations']).to_csv(out/'factor_pairwise_available_correlations.csv',encoding='utf-8-sig')
    # Select and fit on the same complete rows; pairwise missingness must not
    # silently change the correlation cap when PCA subsequently drops rows.
    complete=sample.dropna()
    if len(complete)<settings['min_pair_observations']:raise ValueError('Insufficient common observations for the correlation gate')
    corr=complete.corr()
    corr.to_csv(out/'factor_rank_correlations.csv',encoding='utf-8-sig')
    cap=cfg['effectiveness']['max_representative_abs_corr'];categories=list(cfg['categories'])
    chosen=representatives(records,corr,cap,categories)
    groups={c:representatives([r for r in records if r['category']==c],corr,cap,[c]) for c in categories}
    pairs=[]
    for i,a in enumerate(corr.columns):
        for b in corr.columns[i+1:]:
            v=corr.loc[a,b]
            if np.isfinite(v):pairs.append(dict(factor_a=a,factor_b=b,correlation=float(v)))
    pairs.sort(key=lambda p:abs(p['correlation']),reverse=True)
    pd.DataFrame(pairs).to_csv(out/'factor_correlation_pairs.csv',encoding='utf-8-sig',index=False)
    selected_corr=corr.loc[chosen,chosen].to_numpy()
    actual_corr=float(np.max(np.abs(selected_corr-np.eye(len(chosen))))) if chosen else None
    payload=dict(snapshot_hash=signature,record_ids=[r['id'] for r in records],normalization='within-date percentile rank, then complete-row standardization for PCA',
        scope=f'same deterministic {len(codes)}-code sample for every category; eligible observed rows; no future returns used for correlation',
        sample_rows=len(sample),complete_rows=len(complete),stocks=len(codes),max_representative_abs_corr=cap,actual_max_abs_corr=actual_corr,representatives=chosen,
        per_category_representatives=groups,per_category_counts={k:len(v) for k,v in groups.items()},
        pca_all_effective=pca_summary(complete,settings['pca_explained_variance']),
        pca_low_correlation=pca_summary(complete[chosen],settings['pca_explained_variance']),
        top_correlated_pairs=pairs[:20],selection_uses_full_history=True,ml_requires_training_only_reselection=True,
        five_independent_per_category_achieved=all(len(v)>=cfg['minimum_per_category'] for v in groups.values()))
    write_json(out/'correlation_pca.json',payload)
    print('CORRELATION representatives',len(chosen),'per-category',payload['per_category_counts'])
    p=payload['pca_low_correlation']
    print('PCA',p['input_dimensions'],p['components'],p['cumulative_explained_variance'],'max input correlation',actual_corr)


if __name__=='__main__':main()
