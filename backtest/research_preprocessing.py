"""Causal cross-sectional normalization and training-only feature reduction."""
from copy import deepcopy
import math
import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin


class CorrelationFilter(TransformerMixin, BaseEstimator):
    """Order-preserving, training-only pairwise absolute correlation cap."""
    def __init__(self, threshold=0.5, min_features=3):
        self.threshold=threshold
        self.min_features=min_features

    def fit(self, x, y=None):
        x=np.asarray(x,dtype=float)
        if x.ndim!=2 or not np.isfinite(x).all():
            raise ValueError('相关性筛选需要完整、有限的训练因子矩阵。')
        self.n_features_in_=x.shape[1]
        nonconstant=np.ptp(x,axis=0)>0
        self.correlation_=np.full((x.shape[1],x.shape[1]),np.nan)
        active=np.flatnonzero(nonconstant)
        if len(active):
            self.correlation_[np.ix_(active,active)]=np.corrcoef(x[:,active],rowvar=False) if len(active)>1 else 1.
        selected=[];dropped={}
        for i in range(x.shape[1]):
            if not nonconstant[i]:
                dropped[i]=dict(reason='constant_training_feature');continue
            conflicts=[j for j in selected if abs(self.correlation_[i,j])>self.threshold]
            if conflicts:
                j=max(conflicts,key=lambda j:abs(self.correlation_[i,j]))
                dropped[i]=dict(reason='correlation_above_threshold',with_index=int(j),correlation=float(self.correlation_[i,j]))
            else:selected.append(i)
        if len(selected)<self.min_features:
            raise ValueError(f'相关性去冗余后仅剩{len(selected)}个因子，少于配置下限{self.min_features}；请扩充因子或调整门槛。')
        self.selected_indices_=np.array(selected,dtype=int)
        self.dropped_=dropped
        matrix=self.correlation_[np.ix_(selected,selected)]
        off=matrix[np.triu_indices(len(selected),1)]
        self.max_abs_correlation_=float(np.abs(off).max()) if len(off) else 0.
        return self

    def transform(self, x):
        x=np.asarray(x,dtype=float)
        if x.ndim!=2 or x.shape[1]!=self.n_features_in_:
            raise ValueError('预测因子数量与训练期不一致。')
        return x[:,self.selected_indices_]


def settings():
    from factors.research_library import read_config
    return read_config('factor_preprocessing')


def validate(values=None, feature_count=None):
    cfg = settings()
    if values is not None and not isinstance(values, dict):
        raise ValueError('因子预处理参数必须是对象。')
    result = {**deepcopy(cfg['defaults']), **(values or {})}
    for field in ('normalization', 'scaling', 'reduction', 'correlation_filter'):
        if result[field] not in cfg[field]:
            raise ValueError('不支持的因子预处理选项：' + field)
    mode = result['component_mode']
    if mode not in cfg['component_modes']:
        raise ValueError('请选择 PCA 维数设置方式。')
    for field, spec in cfg['numeric_fields'].items():
        try:
            value = float(result[field])
        except (ValueError, TypeError, OverflowError):
            raise ValueError(spec['label'] + '必须是有效数值。') from None
        if not math.isfinite(value) or not spec['min'] <= value <= spec['max']:
            raise ValueError(spec['label'] + '超出允许范围。')
        if spec.get('step') == 1:
            if not value.is_integer():
                raise ValueError(spec['label'] + '必须为整数。')
            value = int(value)
        result[field] = value
    if feature_count and result['reduction'] == 'pca' and mode == 'count' and result['n_components'] > feature_count:
        raise ValueError('PCA 维数不能超过所选因子数。')
    return result


def normalize_frame(frame, mode):
    """Each row uses only currently observed stocks; missing values stay missing."""
    frame = frame.replace([np.inf, -np.inf], np.nan)
    if mode == 'none':
        return frame
    if mode == 'rank':
        return frame.rank(axis=1, pct=True)
    if mode == 'zscore':
        center, scale = frame.mean(axis=1), frame.std(axis=1, ddof=0)
    elif mode == 'minmax':
        center, scale = frame.min(axis=1), frame.max(axis=1) - frame.min(axis=1)
    else:
        raise ValueError('不支持的截面归一化方式。')
    # A constant observed cross-section carries zero signal, not fabricated data.
    return frame.sub(center, axis=0).div(scale.mask(scale.eq(0), 1), axis=0)


def make_transformer(spec):
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler, MinMaxScaler, RobustScaler
    from sklearn.decomposition import PCA
    steps = []
    if spec.get('correlation_filter')=='greedy':
        steps.append(('correlation',CorrelationFilter(spec['max_abs_correlation'],spec['min_features'])))
    scalers = {'standard': StandardScaler, 'minmax': MinMaxScaler, 'robust': RobustScaler}
    if spec['scaling'] != 'none':
        steps.append(('scaling', scalers[spec['scaling']]()))
    if spec['reduction'] == 'pca':
        components = spec['n_components'] if spec['component_mode'] == 'count' else spec['variance_ratio']
        steps.append(('pca', PCA(n_components=components, svd_solver='full')))
    return Pipeline(steps) if steps else None


def describe(transformer, spec, names):
    result = dict(settings=spec, input_features=list(names), input_dimension=len(names), output_dimension=len(names))
    if transformer is None:
        return result
    selector=transformer.named_steps.get('correlation')
    if selector is not None:
        selected=[names[i] for i in selector.selected_indices_]
        result.update(selected_features=selected,selected_dimension=len(selected),output_dimension=len(selected),
                      correlation_filter=dict(threshold=selector.threshold,max_abs_retained_correlation=selector.max_abs_correlation_,
                          pass_threshold=bool(selector.max_abs_correlation_<=selector.threshold),
                          retained_correlation_matrix=selector.correlation_[np.ix_(selector.selected_indices_,selector.selected_indices_)].tolist(),
                          dropped_features={names[i]:{**{k:v for k,v in meta.items() if k!='with_index'},
                              **({'correlated_with':names[meta['with_index']]} if 'with_index' in meta else {})} for i,meta in selector.dropped_.items()}))
    else:selected=list(names)
    scaler = transformer.named_steps.get('scaling')
    if scaler is not None:
        result['scaler_state'] = {key: getattr(scaler, key).tolist()
                                  for key in ('mean_', 'scale_', 'center_', 'data_min_', 'data_max_')
                                  if hasattr(scaler, key)}
    pca = transformer.named_steps.get('pca')
    if pca is not None:
        ratio = np.nan_to_num(pca.explained_variance_ratio_, nan=0.)
        if selector is not None and ratio.sum()+1e-12 < spec['variance_ratio']:
            raise ValueError(f"相关性筛选后的PCA累计解释方差仅{ratio.sum():.2%}，低于要求{spec['variance_ratio']:.2%}；请增加维数或按解释方差自动保留。")
        result.update(output_dimension=int(pca.n_components_), explained_variance_ratio=ratio.tolist(),
                      cumulative_explained_variance=float(ratio.sum()),
                      correlation_and_variance_pass=bool(selector.max_abs_correlation_<=selector.threshold and ratio.sum()+1e-12>=spec['variance_ratio']) if selector is not None else None,
                      explained_variance_basis='selected_features_after_correlation_filter',
                      loadings={f'PC{i+1}': dict(zip(selected, row.tolist())) for i, row in enumerate(pca.components_)})
    return result
