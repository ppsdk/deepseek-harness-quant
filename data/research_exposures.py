"""Dated exposure labels; current industry classifications never fill history."""
import hashlib
import numpy as np
import pandas as pd


def attach(panels):
    if '_risk_exposures' in panels:
        return panels['_risk_exposures']
    from factors.research_library import BASE, read_config
    cfg = read_config('research_diversification')['exposures']
    close = panels['close']
    labels = {'industry': pd.DataFrame(None, index=close.index, columns=close.columns, dtype=object)}
    provenance = {'industry': 'unavailable', 'styles': {}}
    path = BASE / cfg['history_file']
    if path.exists():
        records = pd.read_csv(path, dtype=str, keep_default_na=False)
        fields = {'code', 'dimension', 'bucket', 'valid_from', 'valid_to', 'available_date'}
        if not fields.issubset(records.columns):
            raise ValueError('历史暴露 CSV 缺少字段：' + ','.join(sorted(fields)))
        for row in records.to_dict('records'):
            if not row['bucket'] or row['dimension'] != 'industry' and not row['dimension'].startswith('style:'):
                raise ValueError('暴露记录必须声明 industry 或 style:名称 和非空 bucket。')
            for field in ('valid_from', 'valid_to', 'available_date'):
                if field == 'valid_to' and not row[field]:
                    continue
                if pd.Timestamp(row[field]).strftime('%Y-%m-%d') != row[field]:
                    raise ValueError('暴露日期必须为 YYYY-MM-DD。')
            if row['valid_to'] and row['valid_from'] > row['valid_to']:
                raise ValueError('暴露生效区间倒置。')
            dim = row['dimension']
            if dim not in labels:
                labels[dim] = pd.DataFrame(None, index=close.index, columns=close.columns, dtype=object)
            if row['code'] not in close.columns:
                continue
            days = close.index.astype(str)
            mask = (days >= max(row['valid_from'], row['available_date'])) & (days <= (row['valid_to'] or '9999-12-31'))
            target = labels[dim].loc[mask, row['code']]
            if target.notna().any():
                raise ValueError('历史暴露区间重叠，请提供无歧义的时点记录。')
            labels[dim].loc[mask, row['code']] = row['bucket']
        provenance['industry'] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    for name, spec in cfg['styles'].items():
        dim = 'style:' + name
        if dim in labels:  # Explicit historical labels take precedence.
            provenance['styles'][name] = 'dated_history'
            continue
        n = spec['window']
        if isinstance(n, bool) or not isinstance(n, int) or n < 1 or not 0 < spec['lower'] < spec['upper'] < 1:
            raise ValueError('风格窗口或分组分位点无效。')
        if spec['source'] == 'momentum':
            raw = close.pct_change(n, fill_method=None)
        elif spec['source'] == 'volatility':
            raw = close.pct_change(fill_method=None).rolling(n, min_periods=n).std()
        elif spec['source'] == 'cap':
            raw = panels.get('cap', panels.get('fund:cap'))
        else:
            raise ValueError('未知风格数据源。')
        if raw is None:
            provenance['styles'][name] = 'unavailable'
            labels[dim] = pd.DataFrame(None, index=close.index, columns=close.columns, dtype=object)
            continue
        ranks = raw.replace([np.inf, -np.inf], np.nan).where(close.notna()).rank(axis=1, pct=True)
        bucket = pd.DataFrame('middle', index=close.index, columns=close.columns)
        bucket = bucket.mask(ranks.le(spec['lower']), 'low').mask(ranks.gt(spec['upper']), 'high').where(ranks.notna())
        labels[dim] = bucket
        provenance['styles'][name] = dict(source=spec['source'], window=n, normalization='daily_cross_section_rank')
    panels['_risk_exposures'] = labels
    panels['_risk_exposure_provenance'] = provenance
    return labels


def concentration(weights, labels, day):
    """NAV weights, including unknown exposure mass; cash is not a sector."""
    output = {}
    for dim, panel in labels.items():
        groups, unknown = {}, 0.0
        for code, weight in weights.items():
            value = panel.at[day, code] if day in panel.index and code in panel.columns else None
            if pd.isna(value):
                unknown += float(weight)
            else:
                groups[str(value)] = groups.get(str(value), 0.) + float(weight)
        known = sum(groups.values())
        output[dim] = dict(weights=groups, unknown_weight=unknown, known_weight=known,
                           max_weight=max(groups.values(), default=0.),
                           hhi=sum((v/known)**2 for v in groups.values()) if known else None)
    return output
