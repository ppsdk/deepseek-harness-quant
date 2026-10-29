"""Remote market results only: no DailyCache, aggregation or local indicators."""
import json
import os
from urllib.parse import urlsplit

import requests


def settings():
    from factors.research_library import read_config
    return read_config('market_cloud')


def _endpoint(kind,cfg):
    url=os.environ.get(cfg[kind+'_url_env']) or cfg.get(kind+'_url','')
    if not url:
        raise ValueError('尚未配置云端'+('大盘分析' if kind=='analysis' else '指数图表')+'接口；请设置 '+cfg[kind+'_url_env']+'。当前页面不会使用本地行情计算或旧分析结果代替。')
    parts=urlsplit(url)
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError('云端行情接口需要不含用户名、密码或片段的 HTTP(S) 地址。')
    return url


def request(kind,query=None,payload=None):
    cfg=settings();url=_endpoint(kind,cfg);headers={'Accept':'application/json'}
    token=os.environ.get(cfg['token_env'])
    if token:headers['Authorization']='Bearer '+token
    method='GET' if payload is None else 'POST'
    try:
        with requests.Session() as session:
            session.trust_env=bool(cfg.get('trust_env',True))
            with session.request(method,url,params=query or {},json=payload,headers=headers,timeout=cfg['timeout_seconds'],stream=True,allow_redirects=False) as response:
                if response.status_code!=200:raise ValueError('云端行情服务返回 HTTP '+str(response.status_code)+'；未切换为本地计算。')
                chunks=[];size=0
                for chunk in response.iter_content(65536):
                    size+=len(chunk)
                    if size>cfg['max_response_bytes']:raise ValueError('云端行情响应超过配置的大小上限。')
                    chunks.append(chunk)
                value=json.loads(b''.join(chunks).decode('utf-8-sig'),parse_constant=lambda _: (_ for _ in ()).throw(ValueError('云端行情含非法数值。')))
    except requests.RequestException:
        # Never include response bodies, URLs or tokens in user-facing errors.
        raise ValueError('无法连接云端行情服务，请检查服务地址、网络和授权；未切换为本地计算。') from None
    except (UnicodeError,json.JSONDecodeError):
        raise ValueError('云端行情服务未返回有效的 UTF-8 JSON。') from None
    if not isinstance(value,dict) or value.get('ok') is False:raise ValueError('云端行情服务未返回成功的数据对象。')
    if kind=='analysis':
        if 'record' not in value or not isinstance(value.get('history',[]),list):raise ValueError('云端大盘响应需要 record 和 history 字段。')
        if value['record'] is not None and not isinstance(value['record'],dict):raise ValueError('云端大盘 record 字段格式不正确。')
        value.setdefault('history',[]);value['capabilities']=cfg['capabilities']
    elif not isinstance(value.get('bars'),list):raise ValueError('云端指数图表响应需要 bars 数组。')
    else:value.setdefault('summary',dict(date=None,close=None,daily_return=None,returns={}))
    return {**value,'ok':True,'data_source':'cloud','local_calculation':False}


def analysis(query=None):
    if settings().get('provider')=='public':
        from data.market_online import analysis as online
        return online(query)
    return request('analysis',query=query)


def build(payload=None):
    payload=payload or {}
    if payload.get('ai') and not settings()['capabilities'].get('ai'):
        raise ValueError('当前云端服务未配置 AI 分析能力。')
    if settings().get('provider')=='public':
        from data.market_online import analysis as online
        return online(refresh=bool(payload.get('refresh')))
    return request('analysis',payload=payload)


def chart(query):
    if settings().get('provider')=='public':
        from data.market_online import chart as online
        return online(query)
    return request('chart',query=query)
