"""Named, immutable code lists saved locally and reused by research strategies."""
from contextlib import closing
from datetime import date,datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

BASE=Path(__file__).resolve().parent.parent
DB=BASE/'data/cache/research_stock_pools.db'


def connect():
    DB.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(DB,timeout=15)
    con.execute('CREATE TABLE IF NOT EXISTS pools(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
    con.commit()
    return con


def get_pool(key):
    if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{32}',key):raise ValueError('保存股票池的编号无效。')
    if not DB.exists():raise ValueError('没有找到保存的股票池。')
    with closing(connect()) as con:row=con.execute('SELECT payload FROM pools WHERE id=?',(key,)).fetchone()
    if not row:raise ValueError('没有找到保存的股票池。')
    return json.loads(row[0])


def list_pools(query=None):
    query=query or {}
    if query.get('id'):return dict(ok=True,**get_pool(query['id']))
    rows=[]
    if DB.exists():
        with closing(connect()) as con:rows=[json.loads(r[0]) for r in con.execute('SELECT payload FROM pools')]
    term=query.get('q','').lower()
    rows=[r for r in rows if term in json.dumps(r,ensure_ascii=False).lower()]
    return dict(ok=True,pools=sorted(rows,key=lambda r:r['created_at'],reverse=True))


def save_pool(payload):
    from factors.research_library import read_config
    if not isinstance(payload,dict):raise ValueError('股票池必须为JSON对象。')
    name=payload.get('name','')
    if not isinstance(name,str) or not name.strip() or len(name)>80:raise ValueError('请填写不超过80字的股票池名称。')
    codes=payload.get('codes',[])
    if isinstance(codes,str):codes=[c for c in re.split(r'[\s,，;；]+',codes.strip()) if c]
    if not isinstance(codes,list) or not codes:raise ValueError('这次没有选出的股票，不能保存空股票池。')
    if any(not isinstance(c,str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',c.strip().upper()) for c in codes):
        raise ValueError('股票代码使用000001.SZ这样的完整形式。')
    codes=list(dict.fromkeys(c.strip().upper() for c in codes))
    if len(codes)>read_config('research_services')['stock_pools']['max_codes']:raise ValueError('股票池中的代码数量超过限制。')
    signal=payload.get('signal_date') or None
    if signal:
        try:signal=date.fromisoformat(signal).isoformat()
        except (ValueError,TypeError):raise ValueError('选股日期应为YYYY-MM-DD。') from None
    source=payload.get('source_strategy','');params=payload.get('source_params',{})
    if not isinstance(source,str) or len(source)>64 or not isinstance(params,dict):raise ValueError('选股来源格式不正确。')
    if len(json.dumps(params,ensure_ascii=False))>20000:raise ValueError('来源参数过长。')
    item=dict(id=uuid4().hex,name=name.strip(),codes=codes,count=len(codes),signal_date=signal,
              source_strategy=source,source_params=params,created_at=datetime.now().isoformat(timespec='microseconds'),
              code_hash=hashlib.sha256(','.join(sorted(codes)).encode()).hexdigest())
    with closing(connect()) as con,con:con.execute('INSERT INTO pools VALUES(?,?)',(item['id'],json.dumps(item,ensure_ascii=False,allow_nan=False)))
    return dict(ok=True,**item,message='已保存股票代码，可在回测页选择任意策略使用这个股票池。')


def apply_pool(p):
    key=p.get('pool_id','')
    if not key:return
    pool=get_pool(key)
    if pool['signal_date'] and p['start']<=pool['signal_date']:
        raise ValueError('这个股票池在'+pool['signal_date']+'收盘后才选出，回测开始日必须晚于选股日，避免使用未来才知道的股票名单。')
    if len(pool['codes'])>p['stocks']:raise ValueError('股票数上限小于保存的股票池数量，请提高股票数上限。')
    if p['topn']>len(pool['codes']):raise ValueError('持仓上限超过这个股票池的股票数，请调小持仓上限。')
    p['codes']=','.join(pool['codes'])
