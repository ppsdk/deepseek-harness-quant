"""Read-only, credential-free research context and editable AI question drafts.

Auto-send: draft() builds the prompt and, when send=True, forwards it to HARNESS
via the deck proxy at /api/proxy/quant/chat2 (port 8787 → 3080).
"""
from datetime import datetime
import json


def context(query=None):
    from backtest.research_runner import research_config
    from backtest.research_pools import list_pools,get_pool
    from factors.research_library import read_config
    from factors.research_manager import experiment_list,experiment_detail
    from data.research_services import timing_status
    query=query or {};cfg=research_config();settings=read_config('research_services');limits=settings['assistant']['limits']
    key=query.get('strategy') or cfg['default_strategy']
    if key not in cfg['strategies']:raise ValueError('请选择目录中的策略。')
    meta=cfg['strategies'][key]
    selected_pool=get_pool(query['pool_id']) if query.get('pool_id') else None
    if selected_pool:selected_pool=dict(selected_pool,codes=selected_pool['codes'][:limits['pool_codes']],codes_truncated=selected_pool['count']>limits['pool_codes'])
    runs=experiment_list({})['runs'][:limits['recent_runs']]
    selected=None
    if query.get('run'):
        r=experiment_detail(query['run'],with_report=False);selected={k:r.get(k) for k in ['name','strategy','params','strategy_spec','metrics','costs','warnings','data','provenance']}
        selected['final_positions']=r.get('position_details',[])[:limits['pool_codes']]
        selected['trade_count']=len(r.get('trades',[]));selected['rejection_count']=len(r.get('rejections',[]))
        selected['data']={k:r['data'].get(k) for k in ['snapshot_hash','price_adjustment','rows','stock_pool']}
        pool=selected['data'].get('stock_pool')
        if pool:selected['data']['stock_pool']=dict(pool,codes=pool['codes'][:limits['pool_codes']],codes_truncated=pool['count']>limits['pool_codes'])
    timing=timing_status({'index':query.get('index') or settings['timing']['benchmark']['code']})
    snapshot=dict(generated_at=datetime.now().isoformat(timespec='seconds'),strategy={**meta,'id':key},
                  factors={f['name']:cfg['factor_catalog'][f['name']] for f in meta['factor_list']},
                  selected_pool=selected_pool,selected_backtest=selected,
                  timing=dict(index=timing['benchmark'],signals=timing['signals'],execution_options=timing['executions']),
                  articles=read_config('research_references').get('articles',[]),
                  execution='前收盘信号，下一交易日开盘尝试成交；包含费用与滑点，停牌和一字行情拒绝成交。',
                  boundaries='本机当前缓存可能缺历史退市股票；财务只使用可用日期后的记录；ETF净值、IOPV、分钟撮合和历史行业中性化尚未接入。')
    return dict(ok=True,snapshot=snapshot,strategies={k:v['name'] for k,v in cfg['strategies'].items()},
                pools=[{k:p[k] for k in ['id','name','count','signal_date']} for p in list_pools()['pools'][:limits['recent_pools']]],
                runs=[{k:r.get(k) for k in ['id','title','generated_at']} for r in runs])


def draft(payload):
    """Build an AI prompt and optionally send it to HARNESS automatically.

    payload keys:
      task, question, strategy, pool_id, run, index  — same as before
      send  – if truthy, forward the prompt to HARNESS via the deck proxy;
               returns ok=True and accepted=True when the queue accepted it.
      session_id – optional HARNESS session id (default: first root agent)
    """
    from factors.research_library import read_config
    if not isinstance(payload,dict):raise ValueError('请求必须为JSON对象。')
    cfg=read_config('research_services')['assistant'];task=payload.get('task')
    if task not in cfg['tasks']:raise ValueError('请选择研究助手任务。')
    question=payload.get('question','')
    if not isinstance(question,str) or len(question)>3000:raise ValueError('补充问题请控制在3000字以内。')
    data=context(payload);text=json.dumps(data['snapshot'],ensure_ascii=False,indent=2,allow_nan=False)
    prompt=cfg['instruction']+'\n\n'+cfg['tasks'][task]['prompt']+'\n'+question+'\n\n【本机研究上下文】\n'+text
    if len(prompt)>cfg['limits']['max_prompt_chars']:raise ValueError('所选上下文太长，请缩小范围或清空回测选择后重试。')
    result=dict(ok=True,prompt=prompt,context_text=text,
                message='已生成可编辑的提问；点击对话发送按钮后才调用AI。',
                accepted=False,sent=False)
    if payload.get('send'):
        from data.ai_bridge import send
        result.update(send(prompt,payload.get('session_id')))
    return result

