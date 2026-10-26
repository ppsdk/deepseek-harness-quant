"""Chronological, purged training and out-of-sample scores for the shared ledger."""
import math
import time

import numpy as np
import pandas as pd


def ml_config():
    from factors.research_library import read_config
    return read_config('research_ml')


def validate_recipe(values, definitions):
    cfg=ml_config();r={**cfg['defaults'],**values}
    if r['model'] not in cfg['models']:raise ValueError('请选择支持的机器学习模型。')
    for key,(lo,hi) in cfg['bounds'].items():
        try:v=float(r[key])
        except (TypeError,ValueError):raise ValueError('训练天数和更新间隔必须为整数。') from None
        if not math.isfinite(v) or not v.is_integer() or not lo<=v<=hi:raise ValueError(f'{cfg["field_labels"][key]}需要在 {lo}—{hi} 之间。')
        r[key]=int(v)
    if r['min_train_days']>r['train_days']:raise ValueError('最少训练天数不能超过训练窗口。')
    if r['horizon'] not in cfg['horizons']:raise ValueError('请选择支持的预测天数。')
    features=r['features']
    if not isinstance(features,list) or not 1<=len(features)<=20 or len(features)!=len(set(features)):
        raise ValueError('请选择 1—20 项不重复的训练指标。')
    for key in features:
        if key not in definitions or definitions[key]['kind'] in ('ml','expression'):
            raise ValueError('训练输入请选择技术、基本面或 Alpha101 指标；不支持模型之间递归训练。')
    return r


def make_model(key):
    from sklearn.tree import DecisionTreeRegressor
    from sklearn.ensemble import RandomForestRegressor,ExtraTreesRegressor,GradientBoostingRegressor,VotingRegressor
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge,ElasticNet
    from sklearn.neighbors import KNeighborsRegressor
    from sklearn.svm import SVR
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    cfg=ml_config();params=dict(cfg['models'][key]['params'])
    if key=='ensemble':
        return VotingRegressor([(k,make_model(k)) for k in cfg['models'][key]['members']],n_jobs=1)
    cls={'decision_tree':DecisionTreeRegressor,'random_forest':RandomForestRegressor,
         'extra_trees':ExtraTreesRegressor,'gradient_boosting':GradientBoostingRegressor,
         'hist_gradient_boosting':HistGradientBoostingRegressor,'ridge':Ridge,'elastic_net':ElasticNet,
         'knn':KNeighborsRegressor,'svr':SVR}[key]
    if 'random_state' in cls().get_params():params['random_state']=cfg['seed']
    model=cls(**params)
    return make_pipeline(StandardScaler(),model) if cfg['models'][key].get('standardize') else model


def predict_walkforward(panels,recipe,definitions=None,predict_start=None):
    from factors.research_library import factor_definitions,raw_factor
    definitions=definitions if definitions is not None else factor_definitions()
    r=validate_recipe(recipe,definitions);cfg=ml_config();close=panels['close'];days=close.index
    if len(close.columns)>cfg['max_stocks']:raise ValueError(f"机器学习实验最多使用 {cfg['max_stocks']} 只股票，请先缩小范围。")
    # Per-date ranks use only that day's observed stocks, not statistics fit on future data.
    xs=[raw_factor(f,panels,definitions).rank(axis=1,pct=True).to_numpy(dtype=float) for f in r['features']]
    features=np.stack(xs,axis=2)
    target=(panels['open'].shift(-(r['horizon']+1))/panels['open'].shift(-1)-1).to_numpy(dtype=float)
    valid_features=np.isfinite(features).all(axis=2)
    scores=pd.DataFrame(np.nan,index=days,columns=close.columns);folds=[];model=None;last_fit=-r['retrain_days']
    for i,day in enumerate(days):
        if predict_start and day<predict_start:continue
        if i<r['min_train_days']+r['horizon']+2:continue
        if model is None or i-last_fit>=r['retrain_days']:
            # A sample at t labels the open at t+h+1. All labels end by i-1.
            stop=i-r['horizon']-1;begin=max(0,stop-r['train_days'])
            mask=valid_features[begin:stop]&np.isfinite(target[begin:stop])
            if (mask.any(axis=1)).sum()<r['min_train_days']:continue
            x=features[begin:stop][mask];y=target[begin:stop][mask]
            limit=min(cfg['max_training_samples'],cfg['models'][r['model']].get('max_training_samples',cfg['max_training_samples']))
            x,y=x[-limit:],y[-limit:]
            if len(y)<2:continue
            model=make_model(r['model']);model.fit(x,y);last_fit=i
            importance=getattr(model,'feature_importances_',None)
            folds.append(dict(prediction_from=day,last_training_feature_date=days[stop-1],
                              last_training_outcome_date=days[i-1],samples=len(y),
                              importance={f:float(v) for f,v in zip(r['features'],importance)} if importance is not None else {}))
        mask=valid_features[i]
        if mask.any():scores.loc[day,close.columns[mask]]=model.predict(features[i,mask])
    return scores,folds


def run_ml(values):
    from backtest.research_runner import research_config,validate_params,load_panels,build_result
    from factors.research_library import factor_definitions
    started=time.monotonic();cfg=research_config();recipe=validate_recipe(values.get('recipe',{}),factor_definitions())
    key='ml_'+recipe['model'];title=ml_config()['models'][recipe['model']]['label']+'：按时间顺序训练'
    meta=dict(name=title,category='机器学习',factors=recipe['features'],factor_list=[{'name':f} for f in recipe['features']],recipe=recipe)
    cfg['strategies'][key]=meta
    p=validate_params({k:v for k,v in values.items() if k in cfg['parameters']}|{'strategy':key},cfg)
    # Read enough past observations to build a real training window before the test starts.
    cfg['warmup_calendar_days']=max(cfg['warmup_calendar_days'],int((recipe['train_days']+252)*1.6))
    panels,data=load_panels(p,cfg)
    dates=panels['close'].index;positions=np.flatnonzero(dates>=p['start'])
    if not len(positions):raise ValueError('回测区间没有行情。')
    first=max(0,int(positions[0])-1)
    scores,folds=predict_walkforward(panels,recipe,predict_start=dates[first])
    if not folds:raise ValueError('没有足够的历史训练数据，请延后回测开始日或减少训练天数。')
    if '_timing' in panels:scores=scores.where(panels['_timing'].eq(1))
    result=build_result(panels,data,scores,p,cfg,meta,started,archive=False)
    result.update(engine='ml_walkforward_v1',training=folds,recipe=recipe)
    result['warnings']+=['训练结果只使用预测日之前已经结束的收益区间；训练和检验按日期分开，没有随机打乱。',
                         '模型预测是实验分数，不是保证实现的收益；训练窗口、股票池和特征选择仍可能过拟合。']
    from backtest.research_runner import save_result
    return save_result(result)
