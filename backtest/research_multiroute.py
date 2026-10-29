"""Causal feature, timing, graph and frozen-model primitives for route research."""
from copy import deepcopy
import numpy as np
import pandas as pd
from backtest.research_preprocessing import make_transformer, describe


def feature(panels, spec):
    p = spec['params']; op = spec['operation']; c = panels['close']
    n = int(p.get('n', 1))
    if op == 'momentum':
        skip = int(p['skip']); value = c.shift(skip) / c.shift(skip + n) - 1
    elif op == 'volatility':
        value = c.pct_change(fill_method=None).rolling(n).std()
    elif op == 'downside':
        value = c.pct_change(fill_method=None).clip(upper=0).pow(2).rolling(n).mean().pow(.5)
    elif op == 'amount': value = panels['amount'].rolling(n).mean()
    elif op == 'money_flow':
        spread = (panels['high'] - panels['low']).replace(0, np.nan)
        flow = ((2*c-panels['high']-panels['low'])/spread)*panels['volume']
        value = flow.rolling(n).sum()/panels['volume'].rolling(n).sum().replace(0, np.nan)
    elif op == 'efficiency':
        value = (c-c.shift(n))/(c.diff().abs().rolling(n).sum().replace(0, np.nan))
    elif op == 'price_location':
        lo = c.rolling(n).min(); hi = c.rolling(n).max()
        value = (c-lo)/(hi-lo).replace(0, np.nan)
    elif op == 'volume_ratio':
        value = panels['volume']/panels['volume'].rolling(n).mean().replace(0, np.nan)
    elif op == 'valuation_yield':
        v = panels['valuation:'+p['field']]; value = 1/v.where(v.gt(0))
    elif op == 'valuation_ratio':
        a = panels['valuation:'+p['numerator']]; b = panels['valuation:'+p['denominator']]
        value = a.where(a.gt(0))/b.where(b.gt(0))
    else: raise ValueError('Unknown research feature: '+op)
    return (value*spec.get('sign', 1)).replace([np.inf, -np.inf], np.nan)


def market_states(index, close, eligible, cfg):
    c = index['close'].reindex(close.index)
    fast = c.rolling(cfg['fast']).mean(); slow = c.rolling(cfg['slow']).mean()
    risk = c.rolling(cfg['risk_period']).mean()
    dd = c/c.rolling(cfg['drawdown_period']).max()-1
    breadth = close.gt(close.rolling(cfg['breadth_period']).mean()).where(eligible).mean(axis=1)
    known = fast.notna() & slow.notna() & risk.notna() & dd.notna() & breadth.notna()
    danger = (c.lt(risk)&fast.lt(slow)) | dd.lt(cfg['strong_risk_drawdown'])
    trend = c.gt(slow) & fast.gt(slow)
    ma_gate = trend.rolling(cfg['confirm_days']).sum().eq(cfg['confirm_days'])
    offense = trend & breadth.ge(cfg['trend_breadth'])
    return pd.DataFrame(dict(ma_gate=ma_gate.astype(float).where(known),
        regime_gate=(~danger).astype(float).where(known), offensive=offense.astype(float).where(known),
        breadth=breadth, index_drawdown=dd), index=close.index)


def causal_graph_features(close, eligible, ranks, cfg):
    """Top positive correlation graph updated using only trailing observed returns.

    This is graph diffusion over temporal factors, not a graph neural network.
    Sparse neighbor arrays keep each daily propagation O(stocks * neighbors).
    """
    returns = close.pct_change(fill_method=None).to_numpy(dtype=float)
    mask = eligible.to_numpy(); size = close.shape[1]
    k = min(cfg['neighbors'], max(0, size-1))
    neighbors = np.zeros((size, k), dtype=int); weights = np.zeros((size, k))
    output = {name: np.full(close.shape, np.nan, dtype=np.float32) for name in cfg['features']}
    raw = {name: ranks[name].to_numpy() for name in cfg['features']}; updates = []
    for i, day in enumerate(close.index):
        if i < cfg['window']-1: continue
        if (i-(cfg['window']-1)) % cfg['update_days'] == 0:
            past = pd.DataFrame(returns[max(0, i-cfg['window']+1):i+1])
            corr = past.corr(min_periods=cfg['min_observations']).to_numpy()
            corr[~np.isfinite(corr)] = 0
            np.fill_diagonal(corr, 0)
            corr[:, ~mask[i]] = 0; corr[~mask[i], :] = 0
            neighbors = np.argsort(-corr, axis=1, kind='stable')[:, :k]
            weights = np.take_along_axis(corr, neighbors, axis=1)
            weights[weights < cfg['min_correlation']] = 0
            denominator = weights.sum(axis=1, keepdims=True)
            weights = np.divide(weights, denominator, out=np.zeros_like(weights), where=denominator>0)
            updates.append(dict(date=str(day), history_start=str(close.index[max(0, i-cfg['window']+1)]),
                history_end=str(day), nodes_with_edges=int((denominator[:, 0]>0).sum()),
                edges=int((weights>0).sum())))
        for name in output:
            observed = raw[name][i, neighbors]
            valid = np.isfinite(observed); mass = (weights*valid).sum(axis=1)
            v = (np.nan_to_num(observed)*weights).sum(axis=1)
            good = (mass >= cfg['min_available_weight']) & mask[i]
            output[name][i, good] = v[good]/mass[good]
    return {'graph_'+name: pd.DataFrame(a, index=close.index, columns=close.columns)
            for name, a in output.items()}, updates


def label_panel(opening, eligible, horizon):
    target = opening.shift(-(horizon+1))/opening.shift(-1)-1
    return target.where(eligible).rank(axis=1, pct=True)-.5


def training_positions(dates, start, end, horizon, every):
    positions = np.arange(len(dates)); exits = positions+horizon+1
    allowed = (dates >= start) & (exits < len(dates))
    label_end = np.minimum(exits, len(dates)-1)
    allowed &= dates[label_end] <= end
    return positions[allowed][::every]


def fit_preprocessing(features, names, eligible, dates, start, end, cfg, horizon=0, every=5, target=None):
    pos = training_positions(dates, start, end, horizon, every)
    x = np.stack([features[n].iloc[pos].to_numpy(dtype=np.float32) for n in names], axis=2)
    valid = eligible.iloc[pos].to_numpy() & np.isfinite(x).all(axis=2)
    if target is not None: valid &= np.isfinite(target.iloc[pos].to_numpy())
    flat = np.flatnonzero(valid.ravel())
    if len(flat) > cfg['max_training_samples']:
        flat = np.sort(np.random.default_rng(cfg['seed']).choice(flat, cfg['max_training_samples'], replace=False))
    if not len(flat): raise ValueError('No complete training rows in discovery.')
    sample = x.reshape(-1, len(names))[flat]
    spec = {k:v for k,v in cfg.items() if k not in ('max_training_samples', 'seed')}
    transformer = make_transformer(spec); transformer.fit(sample)
    audit = describe(transformer, spec, names)
    used_positions = pos[flat//eligible.shape[1]]
    audit.update(samples=len(sample), first_feature_date=str(dates[used_positions.min()]),
        last_feature_date=str(dates[used_positions.max()]),
        last_outcome_date=str(dates[used_positions.max()+horizon+1]),
        fit_end=end, selection_uses_validation=False)
    y = target.iloc[pos].to_numpy().ravel()[flat] if target is not None else None
    return transformer, transformer.transform(sample), y, audit


def make_model(name, params, seed):
    from sklearn.linear_model import Ridge
    from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
    p = deepcopy(params)
    if name == 'ridge': return Ridge(**p)
    p['random_state'] = seed
    if name == 'extra_trees': return ExtraTreesRegressor(**p)
    if name == 'hist_gradient_boosting': return HistGradientBoostingRegressor(**p)
    raise ValueError('Unknown model '+name)


def predict_frozen(model, transformer, features, names, eligible, start, batch_days=20):
    result = np.full(eligible.shape, np.nan, dtype=np.float32)
    positions = np.flatnonzero(eligible.index>=start)
    for offset in range(0, len(positions), batch_days):
        pos = positions[offset:offset+batch_days]
        x = np.stack([features[n].iloc[pos].to_numpy(dtype=np.float32) for n in names], axis=2)
        good = eligible.iloc[pos].to_numpy() & np.isfinite(x).all(axis=2)
        values = np.full(good.shape, np.nan, dtype=np.float32)
        if good.any(): values[good] = model.predict(transformer.transform(x[good]))
        result[pos] = values
    return pd.DataFrame(result, index=eligible.index, columns=eligible.columns)


def blended(features, weights, selected, eligible):
    usable = {name:weight for name,weight in weights.items() if name in selected}
    if len(usable) < 2: raise ValueError('Blend reduced to fewer than two independent factors.')
    return (sum(features[n]*w for n,w in usable.items())/sum(usable.values())).where(eligible), usable


def timed_score(offense, defense, states, mode):
    if mode == 'none': return offense, None
    if mode == 'ma_gate': gate=states.ma_gate; score=offense
    elif mode == 'regime_switch':
        gate=states.regime_gate
        score=offense.where(states.offensive.eq(1), defense, axis=0)
    else: raise ValueError('Unknown timing mode '+mode)
    return score.where(gate.eq(1), axis=0), gate
