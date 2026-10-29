"""Frozen discovery-only normalization/PCA/Ridge ablation on the shared ledger."""
import argparse
from copy import deepcopy
from pathlib import Path
import sys
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config,factor_definitions
from backtest.research_preprocessing import validate,normalize_frame,make_transformer,describe
from scripts.mainboard_repair_research import load_cached_snapshot,load_factor,build_pool_masks,build_entry_masks,finish_summary,canonical_hash
from scripts.mainboard_strategy_research import execute,write_json,log


def fit_discovery(features,target,dates,window,recipe,names):
    # Both feature and its next-open outcome must end inside discovery.
    h=int(recipe['horizon'])
    in_window=(dates>=window['start'])&(dates<=window['end'])
    last=np.flatnonzero(in_window)
    if not len(last):raise ValueError('发现期没有可用日期。')
    allowed=in_window.copy();allowed[max(0,last[-1]-h):]=False
    valid=np.isfinite(features).all(axis=2)&np.isfinite(target)&allowed[:,None]
    flat=np.flatnonzero(valid.ravel())
    limit=int(recipe['max_training_samples'])
    if len(flat)>limit:
        flat=flat[np.linspace(0,len(flat)-1,limit,dtype=int)]
    if len(flat)<2:raise ValueError('发现期没有足够的有效训练样本。')
    x=features.reshape(-1,len(names))[flat];y=target.ravel()[flat]
    spec=validate(recipe['preprocessing'],len(names))
    transformer=make_transformer(spec)
    if transformer is not None:x=transformer.fit_transform(x)
    model_key=recipe.get('model','ridge')
    if model_key=='ridge':model=Ridge(alpha=recipe['alpha'])
    else:
        from backtest.research_ml import make_model
        model=make_model(model_key,standardize=False)
    model.fit(x,y)
    last_feature=int(flat[-1]//features.shape[1])
    audit=dict(samples=len(y),last_training_feature_date=str(dates[last_feature]),
               last_training_outcome_date=str(dates[last_feature+h+1]),
               sample_policy='deterministic_even_sample_across_discovery',
               model_key=model_key,model_params=model.get_params(deep=False),
               coefficients=model.coef_.tolist() if hasattr(model,'coef_') else None,
               feature_importances=model.feature_importances_.tolist() if hasattr(model,'feature_importances_') else None,
               preprocessing=describe(transformer,spec,names))
    return model,transformer,audit


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='mainboard_pca_research')
    args=parser.parse_args(argv)
    experiment=read_config(args.config)
    policy=read_config(experiment['base_config']);policy['output_dir']=experiment['output_dir']
    if experiment.get('rounds'):policy['rounds']=experiment['rounds']
    out=ROOT/policy['output_dir'];out.mkdir(parents=True,exist_ok=True)
    panels,eligible,signature,audit=load_cached_snapshot(policy)
    write_json(out/'policy.json',dict(experiment=experiment,base_policy=policy,snapshot_hash=signature))
    write_json(out/'data_audit.json',audit)
    pools,_=build_pool_masks(panels,eligible,policy)
    entries,_=build_entry_masks(panels,eligible,policy)
    mask=pools[experiment['risk_pool']]&entries[experiment['entry_rule']]
    names=experiment['features'];definitions=factor_definitions()
    arrays=[]
    for name in names:
        frame,_,_=load_factor(name,panels,eligible,policy,definitions)
        # Identical normalization and candidate universe in both ablation arms.
        arrays.append(normalize_frame(frame.where(mask),experiment['normalization']).to_numpy())
    features=np.stack(arrays,axis=2)
    del arrays
    h=experiment['horizon'];target=(panels['open'].shift(-(h+1))/panels['open'].shift(-1)-1).to_numpy()
    # Avoid fitting on known non-executable endpoints.
    tradable=panels['volume'].gt(0)&panels['high'].gt(panels['low'])&panels['open'].gt(0)
    target[~(tradable.shift(-1,fill_value=False)&tradable.shift(-(h+1),fill_value=False)).to_numpy()]=np.nan
    rows=[];frozen=[]
    for round_spec in policy['rounds']:
        for key,spec in experiment['arms'].items():
            preprocessing={k:v for k,v in spec.items() if k!='model'}
            recipe={**experiment,'model':spec.get('model',experiment.get('model','ridge')),
                    'preprocessing':{**preprocessing,'normalization':experiment['normalization']}}
            model,transformer,training=fit_discovery(features,target,eligible.index,round_spec['discovery'],recipe,names)
            if training['last_training_outcome_date']>=round_spec['validation']['start']:
                raise ValueError('训练标签越过验证起点，拒绝执行。')
            record=dict(round_id=round_spec['id'],arm=key,training=training,discovery=round_spec['discovery'],validation=round_spec['validation'],snapshot_hash=signature)
            frozen.append(record)
            # Persist fit audit before any validation simulation.
            write_json(out/'frozen_models.json',frozen)
            valid=np.isfinite(features).all(axis=2)
            values=np.full(valid.shape,np.nan)
            validation=(eligible.index>=round_spec['validation']['start'])&(eligible.index<=round_spec['validation']['end'])
            # Include the prior signal day for execution on the first validation date.
            positions=np.flatnonzero(validation)
            if len(positions) and positions[0]>0:validation[positions[0]-1]=True
            for i in np.flatnonzero(validation):
                x=features[i,valid[i]]
                if not len(x):continue
                if transformer is not None:x=transformer.transform(x)
                values[i,valid[i]]=model.predict(x)
            score=pd.DataFrame(values,index=eligible.index,columns=eligible.columns)
            for stress in (False,True):
                variant=deepcopy(experiment['execution_variant'])
                if stress:variant.update(policy['stress'])
                sim,p=execute(panels,score,round_spec['validation'],variant,policy)
                row=dict(round_id=round_spec['id'],strategy=key,period='validation_stress' if stress else 'validation',
                         training=training,params=p,**finish_summary(sim,p,policy))
                rows.append(row)
                write_json(out/f'{round_spec["id"]}_{key}_{row["period"]}.json',dict(**row,trades=sim['trades'],daily_account=sim['daily_account'],
                           dates=[str(d.date()) for d in sim['nav_series'].index],nav=list(sim['nav_series'].values)))
                log(f"PCA {round_spec['id']} {key} {row['period']}: annual={row['metrics']['annual_return']:.2%} DD={row['metrics']['max_drawdown']:.2%}")
    write_json(out/'results.json',dict(snapshot_hash=signature,config_hash=canonical_hash(experiment),results=rows,
               validation_is_fresh_blind=False,model_fit_uses_validation=False,
               note=experiment.get('design_note','迭代探索数据；相同因子、股票池、交易规则及发现期样本，仅切换PCA。PCA不保证提高预测收益。')))
    pd.DataFrame([dict(round=r['round_id'],arm=r['strategy'],period=r['period'],**r['metrics'],
                       turnover=r['annual_one_way_turnover'],target_pass=r['target_pass'],max_holdings=r['max_holdings'],
                       max_traded_codes=r['max_traded_codes'],min_gap=r['minimum_regular_after_any_gap'],trades=r['trade_count']) for r in rows]).to_csv(out/'results.csv',index=False,encoding='utf-8-sig')


if __name__=='__main__':main()
