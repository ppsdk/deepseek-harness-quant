"""Reconstruct frozen PCA training samples and audit their factor correlations."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from backtest.research_runner import research_config
from backtest.research_preprocessing import normalize_frame


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='mainboard_pca_research')
    args=parser.parse_args(argv)
    cfg=read_config(args.config);policy=read_config(cfg['base_config'])
    source=ROOT/policy['snapshot_dir']/'cache';out=ROOT/cfg['output_dir']
    folder=out/'correlation';folder.mkdir(exist_ok=True)
    snapshot=json.loads((source/'snapshot.json').read_text(encoding='utf-8'))
    dates=pd.Index(snapshot['dates']);names=cfg['features'];defs=research_config()['factor_catalog']
    models=json.loads((out/'frozen_models.json').read_text(encoding='utf-8'))
    if any(r['snapshot_hash']!=snapshot['signature'] for r in models):
        raise ValueError('模型与价格快照不一致。')
    mask=np.load(out/'cache'/('pool_'+cfg['risk_pool']+'.npy'))&np.load(out/'cache'/('entry_'+cfg['entry_rule']+'.npy'))
    arrays=[]
    for name in names:
        paths=[source/('factor_'+name+'.npy'),ROOT/policy['factor_cache_dir']/('factor_'+name+'.npy')]
        path=next(p for p in paths if p.exists())
        frame=pd.DataFrame(np.load(path,mmap_mode='r'),index=dates).where(mask)
        arrays.append(normalize_frame(frame,cfg['normalization']).to_numpy())
    x=np.stack(arrays,axis=2);del arrays,frame
    opening=pd.DataFrame(np.load(source/'open.npy',mmap_mode='r'),index=dates)
    h=cfg['horizon'];target=(opening.shift(-(h+1))/opening.shift(-1)-1).to_numpy()
    tradable=np.load(source/'volume.npy',mmap_mode='r')>0
    tradable &= np.load(source/'high.npy',mmap_mode='r')>np.load(source/'low.npy',mmap_mode='r')
    tradable &= opening.to_numpy()>0
    trade=pd.DataFrame(tradable,index=dates)
    valid_target=(trade.shift(-1,fill_value=False)&trade.shift(-(h+1),fill_value=False)).to_numpy()&np.isfinite(target)
    summary=[];allpairs=[]
    for window in policy['rounds']:
        allowed=np.asarray((dates>=window['discovery']['start'])&(dates<=window['discovery']['end']))
        last=np.flatnonzero(allowed)[-1];allowed[max(0,last-h):]=False
        valid=np.isfinite(x).all(axis=2)&valid_target&allowed[:,None]
        flat=np.flatnonzero(valid.ravel());limit=cfg['max_training_samples']
        if len(flat)>limit:flat=flat[np.linspace(0,len(flat)-1,limit,dtype=int)]
        full_sample=x.reshape(-1,len(names))[flat]
        record=next(r['training']['preprocessing'] for r in models if r['round_id']==window['id'] and 'loadings' in r['training']['preprocessing'])
        selected=record.get('selected_features',names)
        selected_indices=[names.index(n) for n in selected]
        sample=full_sample[:,selected_indices]
        pd.DataFrame(np.corrcoef(full_sample,rowvar=False),index=names,columns=names).to_csv(folder/(window['id']+'_all_candidate_correlations.csv'),encoding='utf-8-sig')
        # Ensure the correlation sample reproduces the actual frozen fit.
        np.testing.assert_allclose(sample.mean(axis=0),record['scaler_state']['mean_'],rtol=1e-12,atol=1e-12)
        np.testing.assert_allclose(sample.std(axis=0),record['scaler_state']['scale_'],rtol=1e-12,atol=1e-12)
        pooled=np.corrcoef(sample,rowvar=False)
        pd.DataFrame(pooled,index=selected,columns=selected).to_csv(folder/(window['id']+'_pca_training_rank_pearson.csv'),encoding='utf-8-sig')
        daily=[];counts=[]
        for i in np.flatnonzero(allowed):
            row=x[i,valid[i]][:,selected_indices]
            if len(row)>=3:
                daily.append(np.corrcoef(row,rowvar=False));counts.append(len(row))
        daily=np.stack(daily);mean=np.nanmean(daily,axis=0);median=np.nanmedian(daily,axis=0)
        pd.DataFrame(mean,index=selected,columns=selected).to_csv(folder/(window['id']+'_daily_rank_corr_mean.csv'),encoding='utf-8-sig')
        pairs=[]
        for i,a in enumerate(selected):
            for j,b in enumerate(selected[i+1:],i+1):
                pair=dict(round_id=window['id'],factor_a=a,factor_b=b,label_a=defs[a]['label'],label_b=defs[b]['label'],training_pooled_corr=float(pooled[i,j]),daily_corr_mean=float(mean[i,j]),daily_corr_median=float(median[i,j]))
                pairs.append(pair);allpairs.append(pair)
        pairs.sort(key=lambda r:abs(r['training_pooled_corr']),reverse=True)
        z=(sample-np.array(record['scaler_state']['mean_']))/np.array(record['scaler_state']['scale_'])
        components=np.array([[record['loadings'][pc][name] for name in selected] for pc in record['loadings']])
        pc_corr=np.corrcoef(z@components.T,rowvar=False)
        validation=(dates>=window['validation']['start'])&(dates<=window['validation']['end'])
        validation_rows=x[validation];validation_rows=validation_rows[np.isfinite(validation_rows).all(axis=2)][:,selected_indices]
        if len(validation_rows)>limit:validation_rows=validation_rows[np.linspace(0,len(validation_rows)-1,limit,dtype=int)]
        vcorr=np.corrcoef(validation_rows,rowvar=False)
        pd.DataFrame(vcorr,index=selected,columns=selected).to_csv(folder/(window['id']+'_validation_selected_correlations.csv'),encoding='utf-8-sig')
        off=np.triu_indices(len(selected),1)
        summary.append(dict(round_id=window['id'],window=window['discovery'],actual_first_training_date=str(dates[flat[0]//x.shape[1]]),actual_last_training_date=str(dates[flat[-1]//x.shape[1]]),samples=len(sample),days=len(daily),selected_features=selected,average_daily_stocks=float(np.mean(counts)),mean_absolute_pair_corr=float(np.mean(np.abs(pooled[off]))),max_abs_training_factor_corr=float(np.abs(pooled[off]).max()),max_abs_validation_factor_corr=float(np.abs(vcorr[off]).max()),explained_variance_ratio=record['explained_variance_ratio'],max_abs_training_pc_cross_corr=float(np.max(np.abs(pc_corr-np.eye(len(components))))),top_pairs=pairs[:10],all_pairs=pairs))
        print(window['id'],'PC1',record['explained_variance_ratio'][0],'mean_abs_corr',summary[-1]['mean_absolute_pair_corr'])
        for pair in pairs[:10]:print(pair['factor_a'],pair['factor_b'],round(pair['training_pooled_corr'],3),'daily',round(pair['daily_corr_mean'],3))
    payload=dict(snapshot_hash=snapshot['signature'],names=names,labels={n:defs[n]['label'] for n in names},universe='same balanced risk pool and MA20-repair candidates as frozen PCA; complete executable-label training rows',normalization=cfg['normalization'],groupwise_pca=False,results=summary)
    (folder/'correlation_summary.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    pd.DataFrame(allpairs).to_csv(folder/'factor_pair_correlations.csv',index=False,encoding='utf-8-sig')


if __name__=='__main__':main()
