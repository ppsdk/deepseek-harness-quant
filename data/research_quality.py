"""Explicit missing-date/field audits and source-backed repair through DailyCache."""
from contextlib import closing,nullcontext
from datetime import datetime,date,timedelta
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

BASE=Path(__file__).resolve().parent.parent
REPORT=BASE/'output/research_data_quality.json'
CALENDAR=BASE/'output/research_trade_calendar.json'


def settings():
    from factors.research_library import read_config
    return read_config('research_workflow')['quality']


def closed_day():
    now=datetime.now(ZoneInfo('Asia/Shanghai'))
    return (now.date() if now.hour>=16 else now.date()-timedelta(days=1)).isoformat()


def local_calendar(start,end):
    if CALENDAR.exists():
        data=json.loads(CALENDAR.read_text(encoding='utf-8'));days=data.get('dates',[])
        if days and days[0]<=start and days[-1]>=end:
            return [d for d in days if start<=d<=end],data['source']
    from data.cache import DailyCache
    from factors.research_library import read_config
    code=read_config('research_services')['timing']['benchmark']['code']
    frame=DailyCache().get_daily(code,start=start,end=end,adjust='none')
    if frame is not None and len(frame):return sorted(set(frame['date'])),'本地指数实际行情日期（可能尚未更新）'
    return [],'缺少交易日历；不使用周一至周五冒充交易日'


def refresh_calendar():
    import akshare as ak
    from data.fetcher_akshare import _no_proxy
    cfg=settings()
    with _no_proxy(timeout=cfg['calendar_timeout']):frame=ak.tool_trade_date_hist_sina()
    days=sorted(set(pd.to_datetime(frame['trade_date']).dt.strftime('%Y-%m-%d')))
    if not days:raise ValueError('交易日历为空。')
    from data.akshare_loader import save_json
    save_json(CALENDAR,dict(dates=days,source='AKShare 新浪交易日历',updated_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()))
    return days


def inspect_frame(frame,start,end,calendar,ipo=None):
    cfg=settings();columns=cfg['fields']
    if frame is None:frame=pd.DataFrame(columns=['date',*columns])
    frame=frame[frame['date'].between(start,end)].copy().sort_values('date')
    dates=set(frame['date']);first=frame['date'].min() if len(frame) else None
    left=max(start,ipo) if ipo else max(start,first) if first else start
    expected=[d for d in calendar if left<=d<=end]
    missing=sorted(set(expected)-dates)
    fields={}
    for f in columns:
        s=pd.to_numeric(frame[f],errors='coerce') if f in frame else pd.Series(np.nan,index=frame.index)
        bad=~np.isfinite(s)
        bad|=s.le(0) if f in cfg['price_fields'] else s.lt(0)
        if f=='turn':bad&=frame['date'].ge('2019-01-01')
        fields[f]=dict(count=int(bad.sum()),dates=frame.loc[bad,'date'].tolist())
    return dict(rows=len(frame),first=first,last=frame['date'].max() if len(frame) else None,
                missing_dates=missing,missing_count=len(missing),fields=fields,
                field_missing_count=sum(v['count'] for v in fields.values()),
                zero_volume_days=int(pd.to_numeric(frame.get('volume',pd.Series(dtype=float)),errors='coerce').eq(0).sum()),
                ipo_date=ipo,start_unverified=not bool(ipo),
                unverified_prefix=[d for d in calendar if start<=d<(first or end)] if not ipo else [])


def universe(payload):
    from data.research_services import stock_snapshot
    from data.cache import CACHE_DIR
    rows,cov=stock_snapshot();by_code={r['code']:r for r in rows};codes=payload.get('codes')
    if isinstance(codes,str):
        import re
        codes=[c.upper() for c in re.split(r'[,，\s]+',codes.strip()) if c]
    if not codes:codes=sorted(cov)
    if not isinstance(codes,list) or not 1<=len(codes)<=settings()['max_codes']:raise ValueError('请提供有效的股票范围。')
    import re
    if any(not isinstance(c,str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',c) for c in codes):raise ValueError('股票代码使用完整市场后缀。')
    ipos={};db=CACHE_DIR/'stock_basic.db'
    if db.exists():
        with closing(sqlite3.connect(db)) as con:
            for c,d in con.execute('SELECT code,ipo_date FROM stock_basic'):
                if d:
                    try:ipos[c]=pd.Timestamp(d).strftime('%Y-%m-%d')
                    except (ValueError,TypeError):pass
    return list(dict.fromkeys(codes)),by_code,ipos


def audit(payload=None,*,persist=True,progress=None):
    from data.cache import DailyCache
    payload=payload or {};start=payload.get('start') or settings()['start'];end=min(payload.get('end') or closed_day(),closed_day())
    start=date.fromisoformat(start).isoformat();end=date.fromisoformat(end).isoformat()
    if start>end:raise ValueError('开始日期不能晚于已收盘日期。')
    codes,names,ipos=universe(payload);calendar,source=local_calendar(start,end);cache=DailyCache();rows=[];batch=settings()['batch_size']
    for i in range(0,len(codes),batch):
        if progress:progress('检查历史日期与字段：'+str(i)+' / '+str(len(codes))+'只')
        frames=cache.get_daily_batch(codes[i:i+batch],start=start,end=end,adjust='qfq',fields=settings()['fields'])
        for code in codes[i:i+batch]:
            row=inspect_frame(frames.get(code),start,end,calendar,ipos.get(code))
            rows.append(dict(code=code,name=names.get(code,{}).get('name',''),**row))
    result=dict(ok=True,start=start,end=end,calendar_source=source,calendar_days=len(calendar),
                generated_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),stocks=rows,
                summary=dict(stocks=len(rows),missing_dates=sum(r['missing_count'] for r in rows),
                    missing_fields=sum(r['field_missing_count'] for r in rows),
                    affected=sum(bool(r['missing_count'] or r['field_missing_count'] or not r['rows']) for r in rows),
                    unknown_ipo=sum(r['start_unverified'] for r in rows)),
                message='缺日是按参考交易日历发现的待核对项，可能包含停牌；缺少上市日期时不把首条记录之前当作已核实完整。0成交量单独列出，缺值不填0；历史ST等未知资料不在本检查的完整性结论内。')
    if persist:
        from data.akshare_loader import save_json
        save_json(REPORT,result)
    return result


def latest():
    if not REPORT.exists():return dict(ok=True,state='empty',stocks=[],message='还没有缺口检查，点击检查后按日期和字段列出结果。')
    return dict(json.loads(REPORT.read_text(encoding='utf-8')),state='ready')


def merge_compatible(primary,other):
    """Only combine full-window sources whose overlapping OHLC prices agree."""
    if primary is None:return other.copy(),True
    a,b=primary.set_index('date'),other.set_index('date');overlap=a.index.intersection(b.index)
    if not len(overlap):return primary,False
    cols=settings()['price_fields'];x=a.loc[overlap,cols].to_numpy(float);y=b.loc[overlap,cols].to_numpy(float)
    if not np.isclose(x,y,rtol=settings()['source_price_rtol'],atol=1e-6,equal_nan=False).all():return primary,False
    return a.combine_first(b).sort_index().reset_index(),True


def repair(payload,*,progress=None,lock_held=False):
    from data.cache import DailyCache
    from data.fetcher_akshare import fetch_daily
    from data.akshare_loader import audit_bars,load_config,save_json,download_lock
    cfg=load_config();calendar_error=None
    with nullcontext() if lock_held else download_lock():
        if progress:progress('更新交易日历，随后检查缺口…')
        try:refresh_calendar()
        except Exception as e:calendar_error=str(e)[:300]
        before=audit(payload,persist=False,progress=progress);calendar,_=local_calendar(before['start'],before['end']);cache=DailyCache();details=[]
        targets=[r for r in before['stocks'] if r['missing_count'] or r['field_missing_count'] or not r['rows'] or r['unverified_prefix']]
        for index,row in enumerate(targets):
            if progress:progress('重新获取并核对缺口：'+str(index+1)+' / '+str(len(targets))+'只 · '+row['code'])
            code=row['code'];best=None;errors=[];sources=[];conflicts=[]
            for backend in settings()['repair_backends']:
                try:
                    fetched=fetch_daily(code,start=before['start'],end=before['end'],adjust='qfq',backend=backend,timeout=cfg['request_timeout'],retries=cfg['retries'])
                    audit_bars(fetched)
                    combined,compatible=merge_compatible(best,fetched)
                    if not compatible:conflicts.append(backend);continue
                    best=combined;sources.append(backend)
                    quality=inspect_frame(best,before['start'],before['end'],calendar,row['ipo_date'])
                    if calendar and not quality['missing_count'] and not quality['field_missing_count']:break
                except Exception as e:errors.append(dict(backend=backend,error=str(e)[:300]))
            old=cache.get_daily(code,start=before['start'],end=before['end'],adjust='qfq')
            restored_dates=restored_fields=0;unverified_old=[]
            if best is not None:
                old_dates=set(old['date']) if old is not None else set()
                restored_dates=len(set(best['date'])-old_dates)
                if old is not None:
                    # Keep already valid local fields only when the same-date price version agrees.
                    overlap_old=old[old['date'].isin(best['date'])]
                    if len(overlap_old):
                        best,_=merge_compatible(best,overlap_old)
                oldq=inspect_frame(old,before['start'],before['end'],calendar,row['ipo_date'])
                newq=inspect_frame(best,before['start'],before['end'],calendar,row['ipo_date'])
                restored_fields=max(0,oldq['field_missing_count']-newq['field_missing_count'])
                unverified_old=sorted(old_dates-set(best['date']))
                # Entire fetched window is updated together; never paste a newly-adjusted single day.
                cache.put_daily(code,best,adjust='qfq',source='akshare')
            details.append(dict(code=code,restored_dates=restored_dates,restored_fields=restored_fields,sources=sources,
                                conflicting_sources=conflicts,unverified_old_dates=unverified_old,errors=errors))
        after=audit(payload,persist=False,progress=progress)
        after['repair']=dict(attempted=len(targets),restored_dates=sum(d['restored_dates'] for d in details),
                             restored_fields=sum(d['restored_fields'] for d in details),details=details,calendar_error=calendar_error)
        after['message']+=' 已重新拉取有缺口股票的完整复权窗口，核对后仍有缺口的记录保留说明。不同源复权价格不一致时不混合补字段。'
        save_json(REPORT,after)
        return after
