"""Stock and index charts from DailyCache, with indicators on the selected bar period."""
from datetime import date,timedelta
from bisect import bisect_left
import json
import pandas as pd

from data.cache import DailyCache
from factors.research_library import read_config,factor_definitions,raw_factor,pattern_requirements


def aggregate(frame,rule):
    if not rule:return frame.sort_values('date').copy()
    ix=frame.copy();ix.index=pd.to_datetime(ix['date'])
    rows=[]
    for _,part in ix.resample(rule):
        if part.empty:continue
        row={'date':str(part['date'].iloc[-1]),'open':part['open'].iloc[0],
             'high':part['high'].max(),'low':part['low'].min(),'close':part['close'].iloc[-1]}
        for f in ('volume','amount'):
            if f in part:row[f]=part[f].sum(min_count=1)
        # Turnover rates are not added across days; no weekly/monthly turnover is inferred.
        if 'turn' in part:row['turn']=None
        rows.append(row)
    return pd.DataFrame(rows)


def trade_annotations(run, code, history, bars, start, end):
    """Match actual fills to daily/weekly/monthly bars without inventing fills."""
    from factors.research_manager import experiment_detail
    result = experiment_detail(run,with_report=False)
    fills = [t for t in result.get('trades', [])
             if t['code'] == code and start <= t['date'] <= end]
    known = set(history['date']) if history is not None else set()
    bar_dates = [b['date'] for b in bars]
    from backtest.research_report import report_config
    labels = report_config()['trade_chart']['marker_labels']
    markers = []
    for fill in fills:
        index = bisect_left(bar_dates, fill['date'])
        if fill['date'] not in known or index >= len(bar_dates):
            continue
        markers.append(dict(fill, bar_date=bar_dates[index], label=labels.get(fill['side'], fill['side'])))
    return dict(run=run, title=result.get('title', run), markers=markers,
                total_fills=len(fills), missing_bar_fills=len(fills)-len(markers),
                message='买卖标记来自这次回测的实际成交；信号日期与成交日期分别列出。K线使用当前缓存，成交价和数量以回测归档口径为准。')


def _overlay_label(spec,params,fields):
    """把模板名里的默认周期换成新周期（20日均线→30日均线）；换不了的参数附在括号里。"""
    import re
    label,defaults,extra=spec['label'],spec.get('params',{}),[]
    for k,v in params.items():
        d=defaults.get(k)
        if d==v or isinstance(d,bool) or not isinstance(d,(int,float)):continue
        pattern=r'(?<![\d.])'+re.escape(f'{d:g}')+r'(?![\d.])'
        if re.search(pattern,label):label=re.sub(pattern,f'{v:g}',label,count=1)
        else:extra.append(fields[k]['label'].split('（')[0]+' '+f'{v:g}')
    return label+('（'+'，'.join(extra)+'）' if extra else '')


def overlay_specs(query,definitions):
    """同一技术指标按不同参数叠加：overlays=[{"base":"ma_20","params":{"n":30}},…]。"""
    import math
    raw=query.get('overlays') or '[]'
    try:items=json.loads(raw) if isinstance(raw,str) else raw
    except ValueError:raise ValueError('叠加指标的参数格式不正确。') from None
    limit=int(read_config('research_services')['chart'].get('max_overlays',8))
    if not isinstance(items,list) or len(items)>limit:raise ValueError('一张图最多叠加'+str(limit)+'条参数不同的指标。')
    fields=read_config('research_controls')['parameter_fields'];out={}
    for i,item in enumerate(items):
        spec=definitions.get(item.get('base')) if isinstance(item,dict) else None
        if not spec or spec.get('kind')!='technical' or not spec.get('chart'):raise ValueError('只能叠加图表支持的技术指标。')
        params=dict(spec.get('params',{}));given=item.get('params') or {}
        if not isinstance(given,dict) or set(given)-set(params):raise ValueError(spec['label']+'没有这些参数。')
        for k,v in given.items():
            f=fields.get(k)
            if (f is None or isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)
                    or not f['min']<=v<=f['max'] or (f.get('integer') and not float(v).is_integer())):
                raise ValueError(spec['label']+'的'+(f['label'] if f else k)+'超出允许范围。')
            params[k]=int(v) if f.get('integer') else float(v)
        if 'fast' in params and 'slow' in params and params['fast']>=params['slow']:raise ValueError('短周期必须小于长周期。')
        out[item['base']+'#'+str(i+1)]=dict(spec,params=params,label=_overlay_label(spec,params,fields),overlay_of=item['base'])
    return out


def chart_data(code,name,query,adjust='qfq',kind='stock',definitions=None,pattern_configuration=None):
    settings=read_config('research_services')['chart']
    period=query.get('timeframe',settings['default_timeframe'])
    if period not in settings['timeframes']:raise ValueError('请选择日线、周线或月线。')
    spec=settings['timeframes'][period]
    try:
        start=date.fromisoformat(query.get('start') or read_config('research')['parameters']['start']['default'])
        end=date.fromisoformat(query.get('end') or date.today().isoformat())
    except (TypeError,ValueError):raise ValueError('行情日期应为YYYY-MM-DD。') from None
    if start>end:raise ValueError('行情开始日期不能晚于结束日期。')
    requested=list(dict.fromkeys(x for x in query.get('indicators','').split(',') if x))
    if len(requested)>settings['max_indicators']:raise ValueError('一张图最多显示'+str(settings['max_indicators'])+'项指标。')
    definitions=factor_definitions() if definitions is None else definitions
    if any(x not in definitions or not definitions[x].get('chart') for x in requested):
        raise ValueError('请选择图表支持的技术指标。')
    # 叠加实例只用于本次图表计算；参数编辑器的分组设置只作用于原始指标，不覆盖叠加实例。
    base_definitions=definitions
    stacked=overlay_specs(query,definitions)
    overlay_days=0
    if stacked:
        definitions={**definitions,**stacked};requested=requested+list(stacked)
        longest=max([v for s in stacked.values() for v in s['params'].values() if isinstance(v,int)]+[0])
        overlay_days=longest*{'day':2,'week':8,'month':35}.get(period,2)
    from factors.research_controls import validate as validate_indicators,apply as apply_indicators,warmup_days,labels
    snapshot={}
    if query.get('run'):
        from factors.research_manager import experiment_detail
        snapshot=experiment_detail(query['run'],with_report=False)
    parameter_values=validate_indicators(query.get('indicator_params',snapshot.get('params',{}).get('indicator_params',{})))
    use_saved_parameters='indicator_params' not in query or parameter_values==snapshot.get('params',{}).get('indicator_params',{})
    extra_days,_=pattern_requirements(requested,definitions)
    warmup=(start-timedelta(days=max(spec['warmup_days'],extra_days,warmup_days(parameter_values),overlay_days))).isoformat()
    history=DailyCache().get_daily(code,start=warmup,end=end.isoformat(),adjust=adjust)
    bars=[];indicators={}; pattern_events=[]; pattern_segments=[]; pattern_studies=[]
    if history is not None and not history.empty:
        frame=aggregate(history,spec['rule'])
        visible=frame[frame['date'].between(start.isoformat(),end.isoformat())]
        cols=[f for f in ('date','open','high','low','close','volume','turn') if f in visible]
        bars=json.loads(visible[cols].to_json(orient='records'))
        ix=frame.set_index('date')
        panels={f:ix[[f]].rename(columns={f:code}) for f in ('open','high','low','close','volume','amount','turn') if f in ix}
        daily=history.set_index('date')
        daily_panels={f:daily[[f]].rename(columns={f:code}) for f in ('open','high','low','close','volume','amount','turn') if f in daily}
        from factors.research_library import attach_extra_panels
        for panel in (panels,daily_panels):attach_extra_panels(panel,[code],warmup,end.isoformat(),[{'name':k} for k in requested],DailyCache(),definitions)
        daily_panels['_pattern_config']=pattern_configuration if pattern_configuration is not None else snapshot.get('pattern_configuration',{}) if use_saved_parameters else {}
        for panel in (panels,daily_panels):
            panel['_indicator_specs']=snapshot.get('indicator_configuration',{}) if use_saved_parameters else {}
            apply_indicators(panel,parameter_values,base_definitions)
        for key in requested:
            item=definitions[key]
            if item.get('kind')=='pattern':
                from factors.research_patterns import chart_study
                study=chart_study(item,daily_panels,code,list(visible['date']),start.isoformat(),end.isoformat())
                indicators.update(study['lines']);pattern_events.extend(study['events']);pattern_segments.extend(study['segments'])
                pattern_studies.append(dict(id=key,label=item['label'],note=study['note']))
                continue
            line=raw_factor(key,panels,definitions).reindex(visible['date'])[code]
            label=item['label']
            if period!='day':label=label.replace('日','根K线')
            indicators[key]={'label':label,'overlay':item.get('overlay',False) if item.get('kind')=='expression' else item.get('operation') in settings['overlay_operations'],
                             'values':json.loads(line.to_json())}
            if item.get('operation','').startswith('kdj_'):indicators[key]['panel']='kdj'
            if item.get('operation','').startswith('macd_') and item.get('operation')!='macd_cross':indicators[key]['panel']='macd'
            if item.get('overlay_of'):
                # 叠加实例与原指标同一副图（RSI6/RSI14 同一窗口）；MACD/KDJ 不同参数另开一组，避免和原线混读。
                indicators[key]['stacked']=True
                if indicators[key].get('panel'):indicators[key]['panel']+=':'+key
                elif not indicators[key]['overlay']:indicators[key]['panel']=item['overlay_of']
    annotations = trade_annotations(query['run'], code, history, bars, start.isoformat(), end.isoformat()) if query.get('run') and kind=='stock' else None
    return dict(ok=True,state='ready' if bars else 'no_data',code=code,name=name,adjust=adjust,kind=kind,
                indicator_params=parameter_values,parameter_summary=labels(parameter_values),
                timeframe=period,timeframe_label=spec['label'],volume_unit='股' if kind=='stock' else '数据源单位',
                bars=bars,indicators=indicators,trade_annotations=annotations,
                patterns=dict(studies=pattern_studies,events=pattern_events,segments=pattern_segments,
                              page_size=read_config('research_patterns')['chart_event_page_size']),
                message='本地缓存'+('前复权' if kind=='stock' else '指数')+spec['label']+'；指标按当前K线周期计算，缺失日期不补齐。')


def market(query):
    cfg=read_config('research_services')['market']
    code=query.get('index') or cfg['default_index']
    if code not in cfg['indices']:raise ValueError('请选择支持的市场指数。')
    result=chart_data(code,cfg['indices'][code]['name'],query,adjust='none',kind='index')
    # These summaries always use daily closes, independent of the chart period.
    df=DailyCache().get_daily(code,end=query.get('end') or date.today().isoformat(),adjust='none')
    summary=dict(date=None,close=None,daily_return=None,returns={})
    if df is not None and not df.empty:
        df=df.sort_values('date');c=df['close']
        summary.update(date=str(df['date'].iloc[-1]),close=float(c.iloc[-1]))
        def ret(n):return float(c.iloc[-1]/c.iloc[-n-1]-1) if len(c)>n and pd.notna(c.iloc[-n-1]) and c.iloc[-n-1]>0 and pd.notna(c.iloc[-1]) else None
        summary['daily_return']=ret(1)
        summary['returns']={str(n):ret(n) for n in cfg['return_windows']}
    result['summary']=summary
    return result
