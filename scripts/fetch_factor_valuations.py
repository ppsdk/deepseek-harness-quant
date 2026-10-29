"""Download dated free valuation history into a research-only immutable cache."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config


def fetch_eastmoney(codes,cfg,spec,folder,con):
    import akshare as ak
    from data.fetcher_akshare import _no_proxy
    source=spec['source']
    fetch_function=ak.stock_value_em  # Resolve lazy imports before starting workers.
    columns={r[1] for r in con.execute('PRAGMA table_info(valuations)')}
    for column in ('peg','pe_static'):
        if column not in columns:con.execute('ALTER TABLE valuations ADD COLUMN '+column+' REAL')
    con.execute('CREATE TABLE IF NOT EXISTS fetch_runs(code TEXT,start TEXT,end TEXT,source TEXT,rows INTEGER,PRIMARY KEY(code,start,end,source))');con.commit()
    completed={c for (c,) in con.execute('SELECT code FROM fetch_runs WHERE start=? AND end=? AND source=?',(cfg['data_start'],cfg['data_end'],source))}&set(codes)
    todo=iter([code for code in codes if code not in completed]);failures=[];done=len(completed)
    def fetch(code):
        start=time.monotonic()
        df=fetch_function(symbol=code.split('.')[0])
        df=df[(df['数据日期'].astype(str)>=cfg['data_start'])&(df['数据日期'].astype(str)<=cfg['data_end'])]
        time.sleep(max(0,spec['pause_seconds']-(time.monotonic()-start)))
        return code,df
    with _no_proxy(timeout=spec['timeout_seconds']),ThreadPoolExecutor(max_workers=spec.get('workers',2)) as pool:
        pending={}
        for _ in range(spec.get('workers',2)):
            code=next(todo,None)
            if code:
                pending[pool.submit(fetch,code)]=code
                time.sleep(spec.get('submit_interval_seconds',.5))
        while pending:
            ready,_=wait(pending,return_when=FIRST_COMPLETED)
            for future in ready:
                code=pending.pop(future)
                try:
                    _,df=future.result();rows=[];observed=datetime.now(timezone.utc).isoformat()
                    for row in df.to_dict('records'):
                        vals=[row.get(f) for f in ['PE(TTM)','市净率','市销率','市现率','PEG值','PE(静)']]
                        vals=[float(v) if pd.notna(v) and np.isfinite(v) else None for v in vals]
                        rows.append((code,str(row['数据日期']),*vals[:4],source,observed,*vals[4:]))
                    con.executemany('INSERT OR IGNORE INTO valuations(code,date,pe,pb,ps,pcf,source,observed_at,peg,pe_static) VALUES(?,?,?,?,?,?,?,?,?,?)',rows)
                    con.execute('INSERT OR REPLACE INTO fetch_runs VALUES(?,?,?,?,?)',(code,cfg['data_start'],cfg['data_end'],source,len(rows)));con.commit();done+=1
                except Exception as error:
                    failures.append(dict(code=code,error_type=type(error).__name__,detail=str(error)[:180]))
                    if len(failures)<=2:
                        import traceback
                        traceback.print_exc(limit=5)
                if (done+len(failures))%20==0:
                    status=dict(state='running',completed=done,requested=len(codes),failed=len(failures),failures=failures,source=source)
                    (folder/'valuation_download.json').write_text(json.dumps(status,indent=2),encoding='utf-8');print('VALUATIONS '+str(status),flush=True)
                nxt=next(todo,None)
                if nxt:
                    pending[pool.submit(fetch,nxt)]=nxt
                    time.sleep(spec.get('submit_interval_seconds',.5))
    status=dict(state='completed',completed=done,requested=len(codes),failed=len(failures),failures=failures,source=source,
                availability_basis='provider_dated_history_at_close_not_original_financial_vintage',data_start=cfg['data_start'],data_end=cfg['data_end'])
    (folder/'valuation_download.json').write_text(json.dumps(status,indent=2),encoding='utf-8');print('VALUATIONS COMPLETE '+str(done),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='four_axis_factor_mining');args=parser.parse_args()
    cfg=read_config(args.config);spec=cfg['valuation_source'];folder=ROOT/cfg['output_dir'];folder.mkdir(parents=True,exist_ok=True)
    snapshot=json.loads((ROOT/cfg['snapshot_dir']/'cache/snapshot.json').read_text(encoding='utf-8'))
    codes=sorted(snapshot['codes']);codes=[codes[i] for i in np.linspace(0,len(codes)-1,min(spec['sample_stocks'],len(codes)),dtype=int)]
    (folder/'valuation_universe.json').write_text(json.dumps(dict(selection='deterministic_even_code_sample_before_outcomes',codes=codes,snapshot_hash=snapshot['signature']),indent=2),encoding='utf-8')
    path=ROOT/spec['db'];path.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(path)
    con.execute('CREATE TABLE IF NOT EXISTS valuations(code TEXT,date TEXT,pe REAL,pb REAL,ps REAL,pcf REAL,source TEXT,observed_at TEXT,PRIMARY KEY(code,date,source))')
    con.execute('CREATE TABLE IF NOT EXISTS completed(code TEXT,start TEXT,end TEXT,rows INTEGER,PRIMARY KEY(code,start,end))')
    try:
        if spec.get('provider')!='eastmoney':raise ValueError('Unsupported historical valuation provider')
        fetch_eastmoney(codes,cfg,spec,folder,con)
    finally:con.close()


if __name__=='__main__':main()
