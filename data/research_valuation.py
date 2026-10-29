"""Research valuation history: exact dated joins, no current-snapshot backfill."""
from contextlib import closing
from pathlib import Path
import sqlite3
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
FIELDS=('pe','pb','ps','pcf','peg','pe_static')


def settings():
    from factors.research_library import read_config
    cfg=read_config('four_axis_factor_mining')['valuation_source']
    return ROOT/cfg['db'],cfg['source']


def coverage():
    path,source=settings()
    if not path.exists():return {}
    output={}
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=20)) as con:
        existing={r[1] for r in con.execute('PRAGMA table_info(valuations)')}
        for field in FIELDS:
            if field not in existing:continue
            count,stocks,first,last=con.execute(f'SELECT count(*),count(DISTINCT code),min(date),max(date) FROM valuations WHERE source=? AND {field} IS NOT NULL',(source,)).fetchone()
            if count:output[field]=dict(rows=count,stocks=stocks,first_date=first,last_date=last,source=source,original_financial_vintage_verified=False)
    return output


def panels(index,codes,wanted):
    result={field:pd.DataFrame(float('nan'),index=index,columns=codes) for field in wanted}
    path,source=settings()
    fields=sorted(set(wanted)&set(FIELDS))
    if not path.exists() or not len(index) or not codes or not fields:return result
    rows=[]
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=20)) as con:
        for i in range(0,len(codes),400):
            chunk=list(codes[i:i+400]);marks=','.join('?' for _ in chunk)
            rows.extend(con.execute('SELECT code,date,'+','.join(fields)+' FROM valuations WHERE source=? AND date>=? AND date<=? AND code IN ('+marks+')',
                                    (source,str(index[0]),str(index[-1]),*chunk)).fetchall())
    if rows:
        frame=pd.DataFrame(rows,columns=['code','date',*fields])
        for field in fields:result[field]=frame.pivot(index='date',columns='code',values=field).reindex(index=index,columns=codes)
    return result
