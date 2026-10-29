"""Explicit, one-shot local jobs; no scheduled monitor and no silent retries."""
import json
import hashlib
import os
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

BASE=Path(__file__).resolve().parent.parent
JOBS=BASE/'output/research_jobs'
LOCK=threading.Lock()
ACTIVE={}
RECORDS={}


def _write_disk(path,data):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    for attempt in range(12):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt==11:raise
            time.sleep(.05*(attempt+1))


def _path(key):
    if not re.fullmatch('[0-9a-f]{32}',key):raise ValueError('任务编号无效。')
    return JOBS/(key+'.json')


def status(key,with_result=False):
    p=_path(key)
    if key in RECORDS:d=dict(RECORDS[key])
    elif p.exists():d=json.loads(p.read_text(encoding='utf-8'))
    else:raise ValueError('任务不存在。')
    if d['state']=='running' and (d.get('pid')!=os.getpid() or key not in ACTIVE.values()):
        # A complete result can outlive a failed Windows metadata rename.
        result=JOBS/(key+'.result.json')
        try:
            saved=json.loads(result.read_text(encoding='utf-8'))
            d.update(state='done',message=saved.get('message','已从保存的结果恢复。'),result_url='/api/studio/jobs/'+key+'/result')
        except (OSError,ValueError):d.update(state='failed',message='服务已重启，这次任务中断。请重新运行。')
    if with_result:
        result=JOBS/(key+'.result.json')
        if not result.exists():raise ValueError('任务还没有结果。')
        return json.loads(result.read_text(encoding='utf-8'))
    return d


def start(payload,compute_lock):
    from factors.research_manager import analyse
    from backtest.research_ml import run_ml
    from backtest.research_runner import run_research
    from backtest.research_timing import compare
    from data.research_services import update_papers,update_stocks,update_directory,update_benchmark
    from data.research_quality import audit,repair
    from factors.research_formula import preview
    from backtest.research_signals import check,create_candidates
    functions={'factor_analysis':analyse,'ml':run_ml,'backtest':run_research,'timing_compare':compare,
               'papers':lambda _:update_papers(),'stocks':update_stocks,
               'directory':lambda _:update_directory(),'benchmark':lambda p:update_benchmark(p.get('index')),
               'formula_preview':preview,'signal_check':check,'candidate_screen':create_candidates,'data_audit':audit,'data_repair':repair}
    action=payload.get('action');args=payload.get('params',{})
    if action not in functions or not isinstance(args,dict):raise ValueError('任务类型或参数不正确。')
    compute=action in ('factor_analysis','ml','backtest','timing_compare','formula_preview','signal_check','candidate_screen','data_audit','data_repair')
    signature=hashlib.sha256(json.dumps(args,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    with LOCK:
        if action in ACTIVE:
            existing=status(ACTIVE[action])
            if existing.get('parameter_hash')==signature:return existing
            raise ValueError('同类任务正在运行，且参数不同。请等它完成后再提交，避免把其他实验的结果当成本次结果。')
        if compute and not compute_lock.acquire(blocking=False):raise ValueError('已有研究计算运行中，请等它完成后再试。')
        key=uuid4().hex;ACTIVE[action]=key
    JOBS.mkdir(parents=True,exist_ok=True)
    d=dict(ok=True,id=key,pid=os.getpid(),action=action,parameter_hash=signature,state='running',started_at=time.strftime('%Y-%m-%d %H:%M:%S'),message='正在执行…')
    def write(data):
        RECORDS[key]=dict(data)
        _write_disk(_path(key),data)
    try:write(d)
    except Exception:
        if compute:compute_lock.release()
        with LOCK:ACTIVE.pop(action,None)
        RECORDS.pop(key,None)
        raise
    def worker():
        try:
            def progress(message):
                d['message']=message
                write(d)
            result=functions[action](args,progress=progress) if action in ('data_audit','data_repair') else functions[action](args)
            (JOBS/(key+'.result.json')).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8')
            d.update(state='done',message=result.get('message','已完成。'),result_url='/api/studio/jobs/'+key+'/result')
        except Exception as error:d.update(state='failed',message=str(error)[:700])
        finally:
            d['finished_at']=time.strftime('%Y-%m-%d %H:%M:%S')
            try:write(d)
            except OSError:
                # Serve the completed state from memory; recovery uses the result file.
                RECORDS[key]=dict(d,status_warning='状态文件暂时不可写，当前结果仍可查看。')
            finally:
                if compute:compute_lock.release()
                with LOCK:ACTIVE.pop(action,None)
    threading.Thread(target=worker,daemon=True,name='studio-'+action).start()
    return dict(d)
