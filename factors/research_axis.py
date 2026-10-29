"""Parameterized factor hypotheses for four-axis research, with causal inputs."""
import numpy as np


def calculate(spec,p):
    result=_calculate(spec,p)
    smooth=int(spec.get('params',{}).get('smooth_n',1))
    if smooth<1:raise ValueError('smooth_n must be positive')
    return result.rolling(smooth,min_periods=smooth).mean() if smooth>1 else result


def _calculate(spec,p):
    op=spec['operation'];a=spec.get('params',{});n=int(a.get('n',20))
    c=p['close'];r=c.pct_change(fill_method=None)
    if op=='risk_adjusted_momentum':return (c/c.shift(n)-1)/r.rolling(n).std().replace(0,np.nan)
    if op=='volume_weighted_return':return (r*p['volume']).rolling(n).sum()/p['volume'].rolling(n).sum().replace(0,np.nan)
    if op=='trend_slope':
        # Equally-spaced OLS log-price slope, normalized by its mean time index.
        log=np.log(c.where(c>0));weights=np.arange(n)-(n-1)/2
        return log.rolling(n).apply(lambda v:np.dot(v,weights)/np.dot(weights,weights),raw=True)
    if op=='parkinson_volatility':return ((np.log(p['high']/p['low'])**2).rolling(n).mean()/(4*np.log(2)))**.5
    if op=='garman_klass':
        var=.5*np.log(p['high']/p['low'])**2-(2*np.log(2)-1)*np.log(c/p['open'])**2
        return var.rolling(n).mean().clip(lower=0)**.5
    if op=='volatility_of_volatility':return r.rolling(n).std().rolling(int(a['m'])).std()
    if op=='return_kurtosis':return r.rolling(n).kurt()
    if op=='amount_cv':return p['amount'].rolling(n).std()/p['amount'].rolling(n).mean().replace(0,np.nan)
    if op=='log_dollar_volume':return np.log(p['amount'].rolling(n).mean().where(lambda v:v>0))
    if op=='roll_spread':return (-r.rolling(n).cov(r.shift(1))).clip(lower=0)**.5*2
    if op=='zero_return_fraction':return r.eq(0).astype(float).where(r.notna()).rolling(n).mean()
    if op=='turn_price_impact':return (r.abs()/p['turn'].replace(0,np.nan)).rolling(n).mean()
    if op=='yield':
        v=p['valuation:'+a['field']]
        if a.get('positive_only'):v=v.where(v>0)
        return 1/v.replace(0,np.nan)
    if op=='value_composite':
        ranks=[(1/p['valuation:'+f].replace(0,np.nan)).rank(axis=1,pct=True) for f in a['fields']]
        return sum(ranks)/len(ranks)
    if op=='relative_valuation':
        v=p['valuation:'+a['field']]
        return v.rolling(n,min_periods=n).rank(pct=True)
    if op=='valuation_change':return p['valuation:'+a['field']].pct_change(n,fill_method=None)
    if op=='valuation_dispersion':
        ranks=[(1/p['valuation:'+f].replace(0,np.nan)).rank(axis=1,pct=True) for f in a['fields']]
        mean=sum(ranks)/len(ranks)
        return (sum([(rank-mean)**2 for rank in ranks])/len(ranks))**.5
    if op=='earnings_book_spread':
        e=1/p['valuation:pe'].replace(0,np.nan);b=1/p['valuation:pb'].replace(0,np.nan)
        return e.rank(axis=1,pct=True)-b.rank(axis=1,pct=True)
    raise ValueError('Unknown four-axis factor operation: '+op)


def latest():
    """Only study statistics are exposed; provider observations stay local."""
    import json
    from pathlib import Path
    from factors.research_library import read_config
    cfg=read_config('four_axis_factor_mining');root=Path(__file__).resolve().parents[1];folder=root/cfg['output_dir']
    if not (folder/'factor_results.json').exists():
        return dict(ok=True,state='pending',categories=cfg['category_labels'],minimum_per_category=cfg.get('minimum_per_category',5),records=[],effective_counts={})
    result=json.loads((folder/'factor_results.json').read_text(encoding='utf-8'))
    from factors.research_evaluation import axis_evaluation
    selected=[]
    for key in cfg['categories']:
        selected.extend([dict(r, evaluation=axis_evaluation(r)) for r in result['records'] if r['category']==key and r['effective']][:cfg.get('minimum_per_category',5)])
    audit={}
    if (folder/'correlation_pca.json').exists():
        saved=json.loads((folder/'correlation_pca.json').read_text(encoding='utf-8'))
        if saved['snapshot_hash']==result['snapshot_hash'] and saved.get('record_ids')==[r['id'] for r in result['records'] if r['effective']]:
            audit=dict(per_category_counts=saved['per_category_counts'],representatives=saved['representatives'],
                       pca={k:v for k,v in saved['pca_low_correlation'].items() if k!='loadings'},
                       max_abs_corr=saved['max_representative_abs_corr'],actual_max_abs_corr=saved['actual_max_abs_corr'])
    return dict(ok=True,state='complete' if result['target_met'] else 'partial',categories=cfg['category_labels'],
                minimum_per_category=cfg.get('minimum_per_category',5),effective_counts=result['effective_counts'],records=selected,
                as_of=cfg['as_of'],data_end=cfg['data_end'],correlation=audit,correlation_note='统计通过不等于相互独立；组合前需相关性去冗余。',
                valuation_note='估值为固定代码样本的供应商历史序列，非原始财报版本库；其余三类为全主板快照。')
