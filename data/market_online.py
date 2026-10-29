"""Keyless online quotes and provider-computed MA; never read local prices."""
from copy import deepcopy
from datetime import datetime
import json
import math
import re
import threading
import time
from zoneinfo import ZoneInfo
import requests
from data.market_cloud import settings

_lock=threading.Lock()
_cache={}

def number(value):
    try:value=float(value)
    except (TypeError,ValueError):return None
    return value if math.isfinite(value) else None

def _get(url,params=None):
    cfg=settings()
    try:
        with requests.Session() as session:
            session.trust_env=bool(cfg.get('trust_env',True))
            response=session.get(url,params=params,timeout=cfg['timeout_seconds'])
            if response.status_code!=200:raise ValueError('在线行情源返回 HTTP '+str(response.status_code))
            if len(response.content)>cfg['max_response_bytes']:raise ValueError('在线行情响应过大。')
            return response
    except requests.RequestException:
        raise ValueError('在线行情源暂时无法连接；未切换为本地行情。') from None

def _memo(key,load,refresh=False):
    cfg=settings()
    with _lock:
        item=_cache.get(key)
        if not refresh and item and time.monotonic()-item[0]<cfg['cache_seconds']:return deepcopy(item[1])
        value=load();_cache[key]=(time.monotonic(),value)
        # Bound only the ephemeral request cache; no downloaded quote database.
        while len(_cache)>cfg['cache_entries']:_cache.pop(next(iter(_cache)))
        return deepcopy(value)

def kline(symbol,timeframe,periods=(),refresh=False):
    cfg=settings()['public'];periods=tuple(sorted(set(int(n) for n in periods)))
    def load():
        rows={}
        batches=[periods[i:i+cfg['ma_per_request']] for i in range(0,len(periods),cfg['ma_per_request'])] or [()]
        for batch in batches:
            response=_get(cfg['kline_url'],dict(symbol=symbol,scale=cfg['scales'][timeframe],ma=','.join(map(str,batch)) or 'no',datalen=cfg['max_bars']))
            text=response.text
            try:part=json.loads(text[text.index('['):text.rindex(']')+1])
            except (ValueError,TypeError):raise ValueError('新浪云端K线返回格式不正确。') from None
            if not isinstance(part,list) or not part:raise ValueError('新浪云端未返回K线。')
            for row in part:
                if isinstance(row,dict) and row.get('day'):rows.setdefault(row['day'],{}).update(row)
        return [rows[key] for key in sorted(rows)]
    return _memo(('kline',symbol,timeframe,periods),load,refresh)

def _quotes(symbols,refresh):
    def load():
        cfg=settings()['public'];response=_get(cfg['quote_url']+','.join(symbols));response.encoding=cfg['quote_encoding']
        out={}
        for symbol,body in re.findall(r'v_(\w+)="([^"]*)"',response.text):
            fields=body.split('~');meta=cfg['quote_fields']
            if len(fields)<=max(meta.values()):continue
            timestamp=fields[meta['timestamp']]
            try:quote_time=datetime.strptime(timestamp,'%Y%m%d%H%M%S').isoformat(sep=' ')
            except ValueError:continue
            pct=number(fields[meta['change_pct']])
            out[symbol]=dict(close=number(fields[meta['price']]),daily_return=pct/100 if pct is not None else None,date=quote_time[:10],quote_time=quote_time)
        if not out:raise ValueError('腾讯云端未返回有效指数报价。')
        return out
    return _memo(('quotes',tuple(symbols)),load,refresh)

def analysis(query=None,refresh=False):
    from factors.research_library import read_config
    cfg=settings();market=read_config('research_services')['market'];codes=read_config('market_analysis')['indices']
    quotes=_quotes([market['indices'][code]['symbol'] for code in codes],refresh)
    rows=[];warnings=[]
    for code in codes:
        spec=market['indices'][code];row=dict(code=code,name=spec['name'],available=spec['symbol'] in quotes,**quotes.get(spec['symbol'],{}))
        try:
            data=kline(spec['symbol'],'day',cfg['public']['ma_periods'],refresh);last=data[-1]
            row['ma_date']=str(last['day'])[:10]
            for n in cfg['public']['ma_periods']:
                row['ma'+str(n)]=number(last.get('ma_price'+str(n)))
                if row['ma'+str(n)] is None:warnings.append(spec['name']+'：数据源未返回 MA'+str(n)+'，不作本地补算。')
        except ValueError as error:warnings.append(spec['name']+'：'+str(error))
        rows.append(row)
    day=max(r['date'] for r in rows if r.get('date'))
    if (query or {}).get('date') not in (None,'',day):raise ValueError('当前免费在线报价不提供历史分析快照；不会读取本地旧分析。')
    return dict(ok=True,data_source='cloud',local_calculation=False,capabilities=cfg['capabilities'],history=[],message='已获取在线指数报价和数据源计算的均线。',record=dict(view='online_quotes',date=day,generated_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),indices=rows,index_columns=cfg['public']['columns'],notes=cfg['public']['notes'],warnings=warnings,sources=['腾讯财经','新浪财经']))

def chart(query):
    from datetime import date
    from factors.research_library import read_config,factor_definitions
    from factors.research_controls import validate,catalogue
    from data.research_charts import overlay_specs
    market=read_config('research_services')['market'];cfg=read_config('research_services')['chart'];public=settings()['public']
    code=query.get('index') or market['default_index']
    if code not in market['indices']:raise ValueError('请选择配置中的市场指数。')
    period=query.get('timeframe') or cfg['default_timeframe']
    if period not in public['scales']:raise ValueError('云端不支持所选K线周期。')
    start=date.fromisoformat(query.get('start') or '1900-01-01').isoformat();end=date.fromisoformat(query.get('end') or date.today().isoformat()).isoformat()
    if start>end:raise ValueError('开始日期不能晚于结束日期。')
    if public.get('chart_indicator_mode')=='workbench':
        import pandas as pd
        from data.research_charts import market as workbench_market
        rows=kline(market['indices'][code]['symbol'],'day',(),query.get('refresh') in ('1','true',True))
        normalized=[]
        for row in rows:
            values={key:number(row.get(key)) for key in ('open','high','low','close','volume')}
            if any(values[key] is None or values[key]<=0 for key in ('open','high','low','close')):continue
            day=str(row['day'])[:10]
            try:day=date.fromisoformat(day).isoformat()
            except ValueError:continue
            normalized.append(dict(date=day,**values,amount=float('nan'),turn=float('nan')))
        if not normalized:raise ValueError('云端没有返回有效的指数K线。')
        history=pd.DataFrame(normalized).sort_values('date').drop_duplicates('date',keep='last')
        keys=('index','timeframe','start','end','indicators','indicator_params','overlays')
        values={k:v for k,v in query.items() if k in keys};values.setdefault('indicators',','.join(cfg['default_indicators']))
        result=workbench_market(values,price_history=history)
        result.update(data_source='cloud',price_source='新浪财经在线K线',indicator_source='研究工作台指标引擎',local_calculation=True,local_price_access=False,warnings=[],
                      price_coverage=dict(first=history.date.iloc[0],last=history.date.iloc[-1],rows=len(history)),
                      message='新浪在线K线；指标复用研究工作台参数引擎，按所选日/周/月K线计算。未读取本地行情库；数据源未提供成交额、换手率时相关指标留空。')
        if start<history.date.iloc[0]:result['message']+=' 免费源可用历史从 '+history.date.iloc[0]+' 起，未从本地补齐。'
        return result
    defs=factor_definitions();groups=catalogue(defs);params=validate(query.get('indicator_params') or {},defs)
    ids=[x for x in query.get('indicators',','.join(cfg['default_indicators'])).split(',') if x]
    if len(ids)>cfg['max_indicators']:raise ValueError('选择的图表指标超过配置上限。')
    if any(x not in defs or not defs[x].get('chart') for x in ids):raise ValueError('所选图表指标不存在。')
    specs={}
    for key in ids:
        group=next((g for g,v in groups.items() if key in v['factors']),None)
        specs[key]={**defs[key],'params':{**defs[key].get('params',{}),**params.get(group,{})}}
    specs.update(overlay_specs(query,defs))
    periods=[item['params']['n'] for item in specs.values() if item.get('operation')=='sma']
    rows=kline(market['indices'][code]['symbol'],period,periods,query.get('refresh') in ('1','true',True))
    bars=[]
    for row in rows:
        day=str(row['day'])[:10]
        if not start<=day<=end:continue
        values={key:number(row.get(key)) for key in ('open','high','low','close','volume')}
        if any(values[key] is None or values[key]<=0 for key in ('open','high','low','close')):continue
        bars.append(dict(date=day,**values,amount=None))
    indicators={};warnings=[]
    for key,spec in specs.items():
        if spec.get('operation') not in public['cloud_operations']:
            warnings.append(spec['label']+'：免费云端未提供参数化指标，未本地计算。');continue
        n=spec['params']['n'];field='ma_price'+str(n)
        values={str(row['day'])[:10]:number(row.get(field)) for row in rows if start<=str(row['day'])[:10]<=end}
        if not any(v is not None for v in values.values()):warnings.append(spec['label']+'('+str(n)+')：云端未返回该周期，未本地补算。');continue
        indicators[key]=dict(label=spec['label']+' ('+str(n)+')',overlay=True,values=values)
    return dict(ok=True,data_source='cloud',local_calculation=False,code=code,name=market['indices'][code]['name'],kind='index',adjust='none',state='ready' if bars else 'no_data',timeframe=period,timeframe_label=cfg['timeframes'][period]['label'],bars=bars,indicators=indicators,indicator_params=params,parameter_summary=[],patterns=dict(studies=[],events=[],segments=[]),volume_unit='数据源单位',summary=dict(date=bars[-1]['date'] if bars else None,close=bars[-1]['close'] if bars else None,daily_return=None,returns={}),warnings=warnings,message='新浪在线K线；MA为数据源直接返回值。'+(' '.join(warnings) if warnings else '')+' 免费源返回窗口有限，不从本地补齐历史。')
