"""Report views derived from the archived ledger; never rerun a strategy."""
from collections import defaultdict
import math


def report_config():
    from factors.research_library import read_config
    return read_config('backtest_report')


def build_report(result):
    """Compound monthly returns and include initial capital in every drawdown.

    Old archives have no daily account snapshots. Leave that series empty;
    reconstructing positions from final holdings would invent historical data.
    """
    cfg = report_config()
    for key, spec in cfg.get('benchmark_metrics', {}).items():
        if key in result.get('metrics', {}):cfg['metrics'][key]=spec
    for key, label in result.get('benchmark', {}).get('series_labels', {}).items():
        if key in cfg['series'] and isinstance(label,str):cfg['series'][key]['label']=label
    from factors.research_library import factor_definitions
    cfg['chart_indicators'] = {key: item['label'] for key, item in factor_definitions().items()
                               if item.get('chart')}
    from factors.research_library import read_config
    cfg['chart_presets']=read_config('research_patterns')['chart_presets']
    from factors.research_controls import catalogue as parameter_catalogue,labels
    controls=read_config('research_controls')
    cfg['indicator_controls']=parameter_catalogue()
    cfg['trigger_labels']=controls['trigger_labels']
    cfg['position_note']=controls['position_note']
    fields={**read_config('research')['parameters'],**controls['parameters']}
    parameter_rows=[]
    for key,value in result.get('params',{}).items():
        field=fields.get(key)
        if field and not field.get('hidden'):
            parameter_rows.append(dict(key=key,label=field['label'],value=field.get('options',{}).get(value,value) if field['type']=='select' else value))
    dates, nav = result.get('dates', []), result.get('nav', [])
    if len(dates) != len(nav) or dates != sorted(set(dates)):
        raise ValueError('回测净值与日期数量不一致，或日期重复、未排序。')
    if any(not isinstance(v, (float, int)) or not math.isfinite(v) or v <= 0 for v in nav):
        raise ValueError('回测净值含缺失、非正值或无穷值，无法生成报告。')
    monthly, annual = {}, {}
    daily, drawdown = [], []
    peak = previous = 1.0
    worst, peak_date, trough_date, recovery_date = 0., None, None, None
    current_peak_date = None  # None means initial capital, before the first day.
    for day, value in zip(dates, nav):
        ret = value / previous - 1
        daily.append(dict(date=day, ret=ret))
        for groups, key in ((monthly, day[:7]), (annual, day[:4])):
            groups[key] = groups.get(key, 1.) * (1 + ret)
        if value >= peak:
            peak, current_peak_date = value, day
        dd = value / peak - 1
        drawdown.append(dd)
        if dd < worst:
            worst, peak_date, trough_date, recovery_date = dd, current_peak_date, day, None
        elif trough_date is not None and recovery_date is None and dd >= 0:
            recovery_date = day
        previous = value
    month_rows = defaultdict(dict)
    for month, value in monthly.items():
        month_rows[month[:4]][month[5:]] = value - 1
    values = [d['ret'] for d in daily]
    histogram = []
    if values:
        bins = max(1, min(80, int(cfg.get('histogram_bins', 16))))
        low, high = min(values), max(values)
        if high == low:
            histogram = [dict(low=low, high=high, count=len(values))]
        else:
            step = (high - low) / bins
            counts = [0] * bins
            for value in values:
                counts[min(bins - 1, int((value - low) / step))] += 1
            histogram = [dict(low=low + i * step, high=low + (i + 1) * step, count=count)
                         for i, count in enumerate(counts)]
    costs = result.get('costs', {})
    cost_rows = [dict(key=key, label=label, value=float(costs.get(key, 0)))
                 for key, label in cfg.get('cost_labels', {}).items()]
    reasons = defaultdict(int)
    for item in result.get('rejections', []):
        reasons[item.get('reason', 'unknown')] += 1
    account = result.get('daily_account', [])
    return dict(version=1, settings=cfg, drawdown=drawdown, daily_returns=daily,
                months=[dict(year=year, months=months, total=annual[year] - 1)
                        for year, months in month_rows.items()],
                years=[dict(year=year, ret=value - 1) for year, value in annual.items()],
                histogram=histogram, costs=cost_rows,
                cash_fees=float(costs.get('commission', 0)) + float(costs.get('stamp_tax', 0)),
                max_drawdown=dict(value=worst, peak_date=peak_date, trough_date=trough_date,
                                  recovery_date=recovery_date),
                rejections=[dict(reason=key, label=cfg.get('rejection_reasons', {}).get(key, key), count=n)
                            for key, n in sorted(reasons.items())],
                account_available=bool(account), account=account,
                parameters=parameter_rows,indicator_parameters=labels(result.get('params',{}).get('indicator_params',{}),cfg['indicator_controls']),
                position_detail_available='position_details' in result,
                positions=result.get('position_details',[]),realized_pnl=result.get('realized_pnl',{}),
                provenance=result.get('provenance'))
