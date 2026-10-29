"""Audit saved ML artifacts against training-only rows; never refit a model."""
from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path
import sys

import joblib
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backtest.research_multiroute import training_positions, label_panel
from factors.research_library import read_config
from scripts.mainboard_repair_research import load_cached_snapshot


def main(config_name='multiroute_strategy_research'):
    cfg=read_config(config_name)
    out=ROOT/cfg['output_dir']
    manifest=json.loads((out/'experiment_manifest.json').read_text(encoding='utf-8'))
    source_matches={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in manifest['source_hashes'].items()}
    assert all(source_matches.values()), source_matches
    panels,eligible,signature,_=load_cached_snapshot(read_config(cfg['base_config']))
    assert signature==manifest['snapshot_hash']
    route='matched_ml'
    archive_map=json.loads((out/'platform_archives.json').read_text(encoding='utf-8'))
    anchor=next(x for x in archive_map if x['route']==route)
    archive=json.loads((ROOT/'output/backtest_archive'/(anchor['name']+'.json')).read_text(encoding='utf-8'))
    codes=archive['data']['selected_codes']
    eligible=eligible[codes].copy()
    # These five daily ratios define the common eligibility mask in prepare().
    for name in cfg['valuation_features']:
        arr=np.load(out/'cache'/(name+'.npy'),mmap_mode='r')
        assert arr.shape==eligible.shape
        eligible &= np.isfinite(arr)
    features={}
    full_columns=panels['close'].columns
    locations=full_columns.get_indexer(codes)
    for name in cfg['technical_features']:
        arr=np.load(out/'cache'/(name+'.npy'),mmap_mode='r')
        features[name]=arr[:,locations]
    dates=eligible.index
    target=label_panel(panels['open'][codes],eligible,cfg['ml']['horizon'])
    results=[]
    for rd in cfg['rounds']:
        folder=out/rd['id']
        if manifest.get('parameter_search') is False:
            candidates=cfg['routes'][route]['models']
            assert len(candidates)==1
            frozen=dict(choice=dict(candidate=candidates[0]))
            stages=['final']
        else:
            frozen=json.loads((folder/(route+'_frozen.json')).read_text(encoding='utf-8'))
            assert not frozen['selection_uses_validation']
            assert frozen['selection_period']==rd['selection']
            stages=['inner','final']
        for stage in stages:
            stem=f"{route}__{frozen['choice']['candidate']}__{stage}"
            artifact_path=folder/'cache'/(stem+'.joblib')
            before=hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            artifact=joblib.load(artifact_path)
            audit=json.loads((folder/(stem+'_fit.json')).read_text(encoding='utf-8'))
            end=rd['inner_training_end'] if stage=='inner' else rd['discovery']['end']
            pos=training_positions(dates,rd['discovery']['start'],end,cfg['ml']['horizon'],cfg['ml']['sample_every_days'])
            names=artifact['names']
            x=np.stack([features[n][pos] for n in names],axis=2)
            valid=eligible.iloc[pos].to_numpy() & np.isfinite(x).all(axis=2) & np.isfinite(target.iloc[pos].to_numpy())
            flat=np.flatnonzero(valid.ravel())
            prep_cfg=cfg['preprocessing']
            if len(flat)>prep_cfg['max_training_samples']:
                flat=np.sort(np.random.default_rng(prep_cfg['seed']).choice(flat,prep_cfg['max_training_samples'],replace=False))
            sample=x.reshape(-1,len(names))[flat]
            y=target.iloc[pos].to_numpy().ravel()[flat]
            used=pos[flat//len(codes)]
            last_feature=str(dates[used.max()])
            last_outcome=str(dates[used.max()+cfg['ml']['horizon']+1])
            assert last_outcome<=end and last_outcome<rd['validation']['start']
            assert audit['samples']==len(y)
            assert audit['last_feature_date']==last_feature and audit['last_outcome_date']==last_outcome
            assert artifact['fit_end']==end and not audit['validation_refit']
            transformer=artifact['transformer']
            selector=transformer.named_steps['correlation']
            selected=selector.transform(sample)
            scaler=transformer.named_steps['scaling']
            assert int(scaler.n_samples_seen_)==len(sample)
            np.testing.assert_allclose(scaler.mean_,selected.mean(axis=0),rtol=1e-10,atol=1e-10)
            np.testing.assert_allclose(scaler.var_,selected.var(axis=0),rtol=1e-10,atol=1e-10)
            pca=transformer.named_steps['pca']
            assert pca.n_samples_==len(sample)
            normalized=scaler.transform(selected)
            np.testing.assert_allclose(pca.mean_,normalized.mean(axis=0),rtol=1e-9,atol=1e-10)
            # Saved PCA axes must explain precisely the training-only covariance.
            covariance=np.cov(normalized,rowvar=False)
            projected=pca.components_@covariance@pca.components_.T
            np.testing.assert_allclose(projected,np.diag(pca.explained_variance_),rtol=1e-8,atol=1e-9)
            tx=transformer.transform(sample)
            model=artifact['model']
            if frozen['choice']['candidate']=='ridge':
                xc=tx-tx.mean(axis=0)
                yc=y-y.mean()
                coefficient=np.linalg.solve(xc.T@xc+model.alpha*np.eye(tx.shape[1]),xc.T@yc)
                np.testing.assert_allclose(model.coef_,coefficient,rtol=1e-8,atol=1e-9)
                np.testing.assert_allclose(model.intercept_,y.mean()-tx.mean(axis=0)@coefficient,rtol=1e-8,atol=1e-9)
                model_check='Saved Ridge coefficients equal training-only normal-equation solution'
            else:
                assert not model.bootstrap
                for tree in model.estimators_:
                    leaves=tree.apply(tx)
                    counts=np.bincount(leaves,minlength=tree.tree_.node_count)
                    sums=np.bincount(leaves,weights=y,minlength=tree.tree_.node_count)
                    used_leaves=np.flatnonzero(counts)
                    np.testing.assert_array_equal(tree.tree_.n_node_samples[used_leaves],counts[used_leaves])
                    np.testing.assert_allclose(tree.tree_.value[used_leaves,0,0],sums[used_leaves]/counts[used_leaves],rtol=1e-8,atol=1e-10)
                    assert tree.tree_.n_node_samples[0]==len(y)
                model_check='Every saved ExtraTrees leaf count and mean label match training-only rows'
            assert hashlib.sha256(artifact_path.read_bytes()).hexdigest()==before
            results.append(dict(round_id=rd['id'],stage=stage,model=frozen['choice']['candidate'],samples=len(y),
                first_feature_date=str(dates[used.min()]),last_feature_date=last_feature,last_outcome_date=last_outcome,
                fit_end=end,validation_start=rd['validation']['start'],validation_end=rd['validation']['end'],
                overlapping_training_outcomes=0,scaler_training_only=True,pca_training_only=True,
                saved_model_check=model_check,artifact_sha256=before))
    result=dict(ok=True,source_hashes_match_experiment=source_matches,snapshot_hash=signature,checks=results,
        distinction=('Annual expansion uses completed previous years only; the prediction year is excluded from that model and preprocessing.' if manifest.get('parameter_search') is False else 'Round 2 may train on 2023-2024, which were validation years in round 1; round 2 validation is 2025 and is excluded.'),
        limitation='Historical validation was already viewed; survivor universe and source-data vintage biases remain. This is not a fresh blind test.')
    destination=out/'matched_ml_training_boundary_audit.json'
    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='multiroute_strategy_research')
    main(parser.parse_args().config)
