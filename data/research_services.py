"""Local stock catalogue, manual data updates and paper metadata retrieval."""
from contextlib import closing
from datetime import date,datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET

import pandas as pd

BASE=Path(__file__).resolve().parent.parent
PAPERS=BASE/'output/research_papers.json'


def settings():
    from factors.research_library import read_config
    return read_config('research_services')


def pid_alive(pid):
    """Read process state. Windows os.kill(pid, 0) can terminate the target."""
    try:pid=int(pid)
    except (TypeError,ValueError):return False
    if pid<=0:return False
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:return ctypes.get_last_error()==5  # Access denied still indicates a process.
        try:
            code=wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
        finally:kernel.CloseHandle(handle)
    return Path('/proc',str(pid)).exists()


def stock_snapshot():
    from data.cache import CACHE_DIR,DailyCache
    from data.research_cache import metadata,file_stamp
    path=CACHE_DIR/'stock_basic.db';cache=DailyCache()
    databases=[Path(db) for db in [cache.db_path,*cache._inc_paths()]]
    stamp=tuple(file_stamp(p) for p in [path,*databases])
    def load():
        rows=[];coverage={}
        if path.exists():
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as con:
                rows=[{'code':c,'name':n} for c,n in con.execute('SELECT code,name FROM stock_basic ORDER BY code')]
        for db in databases:
            if not db.exists():continue
            with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as con:
                for code,start,end,n in con.execute("SELECT code,start_date,end_date,rows FROM bar_meta WHERE adjust='qfq'"):
                    old=coverage.get(code,{})
                    coverage[code]={'start':min(start,old.get('start',start)),'end':max(end,old.get('end',end)),'rows':max(n or 0,old.get('rows',0))}
        if not rows:rows=[{'code':c,'name':''} for c in sorted(coverage)]
        return rows,coverage
    # Internal read-only snapshot: stock_list builds fresh dictionaries before returning.
    return metadata('stock_directory',stamp,load,copy_result=False)


def stock_list(query):
    rows,coverage=stock_snapshot()
    term=str(query.get('q','')).strip().lower()
    rows=[{**r,**coverage.get(r['code'],{}),'downloaded':r['code'] in coverage} for r in rows
          if term in r['code'].lower() or term in (r['name'] or '').lower()]
    if query.get('cached')=='1':rows=[r for r in rows if r['downloaded']]
    rows.sort(key=lambda r:(not(r['code'].lower().startswith(term) or (r['name'] or '').lower().startswith(term)),not r['downloaded'],r['code']))
    try:offset=max(0,int(query.get('offset',0)));limit=min(100,max(1,int(query.get('limit',30))))
    except ValueError:raise ValueError('页码和每页数量应为整数。') from None
    return {'ok':True,'total':len(rows),'offset':offset,'stocks':rows[offset:offset+limit]}


def paper_list():
    if not PAPERS.exists():return dict(ok=True,papers=[],message='还没有论文记录，点击“更新量化论文”从 arXiv 获取题目、摘要和原文链接。')
    return {'ok':True,**json.loads(PAPERS.read_text(encoding='utf-8'))}


def update_papers():
    import requests
    cfg=settings()['updates']
    response=requests.get(cfg['papers_url'],params={'search_query':cfg['papers_query'],'sortBy':'submittedDate','sortOrder':'descending','max_results':cfg['papers_limit']},
                          timeout=cfg['timeout'],headers={'User-Agent':'QuantResearch/1.0 local literature reader'})
    response.raise_for_status();root=ET.fromstring(response.content);ns={'a':'http://www.w3.org/2005/Atom'}
    papers=[]
    for entry in root.findall('a:entry',ns):
        url=entry.findtext('a:id',default='',namespaces=ns).replace('http://','https://')
        if not url.startswith('https://arxiv.org/abs/'):continue
        papers.append(dict(id=url.rsplit('/',1)[-1],title=' '.join(entry.findtext('a:title','',ns).split()),
                           abstract=' '.join(entry.findtext('a:summary','',ns).split()),
                           authors=[e.findtext('a:name','',ns) for e in entry.findall('a:author',ns)],
                           published=entry.findtext('a:published','',ns),updated=entry.findtext('a:updated','',ns),url=url,
                           categories=[e.attrib.get('term','') for e in entry.findall('a:category',ns)]))
    result=dict(papers=papers,updated_at=datetime.now().isoformat(timespec='seconds'),source='arXiv',query=cfg['papers_query'],
                message='按提交时间排序的论文元数据，未经同行评审或有效性筛选；摘要保留原文，不编造结论。')
    PAPERS.parent.mkdir(parents=True,exist_ok=True);temp=PAPERS.with_suffix('.tmp');temp.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8');os.replace(temp,PAPERS)
    return dict(ok=True,count=len(papers),message=f'已更新 {len(papers)} 篇论文。')


def update_stocks():
    from data.akshare_loader import load_config,read_status
    report=read_status(summary=True)
    if report.get('state')=='running' and pid_alive(report.get('pid')):
        return dict(ok=True,existing=True,message='已有股票下载任务正在运行，本次不重复启动。',completed=report.get('completed',0),total=report.get('requested_stocks',0))
    log=BASE/'output/akshare_manual.log';log.parent.mkdir(parents=True,exist_ok=True)
    start=settings()['updates']['price_start']
    with log.open('ab') as handle:
        proc=subprocess.Popen([sys.executable,'-u','-m','data.akshare_loader','--universe','all_a','--all','--start',start,'--end',date.today().isoformat(),'--refresh','--verify-gaps'],cwd=BASE,
                              stdout=handle,stderr=handle,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return dict(ok=True,pid=proc.pid,message='已启动日线更新，完成后继续检查缺日与缺字段并尝试补齐。前复权按完整窗口刷新；仍缺数据会显示，进度与补齐报告见数据页。')


def update_directory():
    from data.akshare_loader import load_config,load_universe,persist_universe
    from data.cache import CACHE_DIR
    cfg=load_config();cfg['universe']='all_a';rows=load_universe(cfg);persist_universe(rows,CACHE_DIR)
    metadata=update_listing_dates()
    return dict(ok=True,count=len(rows),listing_dates=metadata,message=f'已更新 {len(rows)} 只股票的名称和代码；上市日期写入 {metadata["updated"]} 条。'+(' 部分来源未完成：'+'；'.join(metadata['errors']) if metadata['errors'] else ''))


def update_listing_dates():
    import akshare as ak
    from data.fetcher_akshare import _no_proxy
    from data.cache import CACHE_DIR
    from factors.research_library import read_config
    db=CACHE_DIR/'stock_basic.db'
    if not db.exists():return dict(updated=0,errors=['请先获取股票目录'])
    updates={};errors=[]
    for spec in read_config('research_workflow')['directory_sources']:
        try:
            with _no_proxy(timeout=settings()['updates']['timeout']):frame=getattr(ak,spec['function'])(**spec['kwargs'])
            for _,r in frame.iterrows():
                value=pd.to_datetime(r[spec['ipo']],errors='coerce')
                if pd.notna(value) and value.date()<=date.today():
                    code=str(r[spec['code']]).strip().zfill(6)+'.'+spec['market']
                    updates[code]=value.strftime('%Y-%m-%d')
        except Exception as e:errors.append(spec['function']+' '+str(e)[:180])
    with closing(sqlite3.connect(db)) as con,con:
        before=con.total_changes
        con.executemany('UPDATE stock_basic SET ipo_date=? WHERE code=?',[(d,c) for c,d in updates.items()])
        count=con.total_changes-before
    return dict(updated=count,errors=errors)


def update_benchmark(index=None):
    import akshare as ak
    from data.fetcher_akshare import _no_proxy
    from data.cache import DailyCache
    cfg=settings()
    code=index or cfg['timing']['benchmark']['code']
    if code not in cfg['market']['indices']:raise ValueError('请选择支持的市场指数。')
    item=dict(cfg['market']['indices'][code],code=code)
    with _no_proxy(timeout=settings()['updates']['timeout']):df=ak.stock_zh_index_daily(symbol=item['symbol'])
    if df is None or df.empty:raise ValueError('数据源没有返回指数日线。')
    df['date']=pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d');df['code']=item['code']
    for col in ('open','high','low','close','volume'):df[col]=pd.to_numeric(df[col],errors='coerce')
    if df['close'].notna().sum()<60:raise ValueError('指数有效历史不足 60 天。')
    n=DailyCache().put_daily(item['code'],df,adjust='none',source='akshare')
    return dict(ok=True,rows=n,message=f"已更新 {item['name']} 指数日线。")


def timing_status(query=None):
    from data.cache import DailyCache
    from data.research_charts import chart_data
    from backtest.research_timing import evaluate
    query=query or {};services=settings();cfg=services['timing'];indices=services['market']['indices']
    code=query.get('index') or cfg['benchmark']['code'];key=query.get('rule') or cfg['default_rule']
    if code not in indices or key not in cfg['rules']:raise ValueError('请选择支持的市场指数和择时规则。')
    chart=chart_data(code,indices[code]['name'],dict(query,timeframe='day'),adjust='none',kind='index')
    df=DailyCache().get_daily(code,end=query.get('end') or date.today().isoformat(),adjust='none')
    signals=[];history=[];indicators={}
    if df is not None and not df.empty:
        ix=df.sort_values('date').set_index('date');visible={b['date'] for b in chart['bars']}
        for name,rule in cfg['rules'].items():
            if name=='none':continue
            g=evaluate(ix,name,cfg);last=g.iloc[-1]
            signals.append(dict(id=name,label=rule['label'],date=str(ix.index[-1]),allow=None if pd.isna(last) else bool(last)))
            if name==key:
                subset=g[g.index.isin(visible)]
                history=[dict(date=str(d),allow=None if pd.isna(v) else bool(v)) for d,v in subset.items()]
                indicators['market_gate']={'label':'是否允许持有：1允许，0退出','overlay':False,'values':json.loads(subset.to_json())}
    chart['indicators'].update(indicators)
    known=[x for x in history if x['allow'] is not None]
    return dict(ok=True,benchmark=dict(indices[code],code=code),indices=indices,rules=cfg['rules'],signals=signals,
                selected_rule=key,history=history,chart=chart,
                allowed_days=sum(x['allow'] for x in known),known_days=len(known),missing_days=len(history)-len(known),
                executions=cfg['executions'],default_execution=cfg['default_execution'],
                message='信号按当日收盘计算，下一交易日开盘执行；1允许持有，0要求退出。恢复后是否买入仍取决于策略换股日。')
