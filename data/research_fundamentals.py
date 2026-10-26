"""Dated financial observations. New web snapshots never backfill old signals."""
from datetime import date, timedelta
from contextlib import closing, contextmanager
import csv
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DB = BASE / 'data/cache/research_fundamentals.db'
LOCK = threading.Lock()
JOB = {'state': 'idle', 'message': '尚未获取财报。可以获取 AKShare 财报，或导入含可用日期的历史数据。'}


def settings():
    from factors.research_library import library_config
    return library_config()['fundamentals']


def metrics():
    from factors.research_library import factor_definitions
    return {m['metric'] for m in factor_definitions().values() if m.get('kind') == 'fundamental'}


@contextmanager
def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=20)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('''CREATE TABLE IF NOT EXISTS observations (
        code TEXT, metric TEXT, value REAL, available_date TEXT, report_date TEXT,
        source TEXT, observed_date TEXT,
        PRIMARY KEY(code, metric, available_date, report_date))''')
    try:
        with con:
            yield con
    finally:
        con.close()


def store(rows):
    allowed, checked = metrics(), []
    for r in rows:
        code, metric = str(r.get('code', '')).upper().strip(), r.get('metric')
        if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', code) or metric not in allowed:
            raise ValueError('财务数据的代码或指标名无效：' + code + ' / ' + str(metric))
        try:
            value = float(r['value'])
            available = date.fromisoformat(r['available_date']).isoformat()
            report = date.fromisoformat(r['report_date']).isoformat()
        except (ValueError, TypeError, KeyError):
            raise ValueError('财务数值或日期无效；日期应为 YYYY-MM-DD。') from None
        if not math.isfinite(value) or available < report:
            raise ValueError('财务值必须有限，可用日期不能早于报告期末。')
        checked.append((code, metric, value, available, report, str(r.get('source', 'CSV'))[:200], date.today().isoformat()))
    with connect() as con:
        # Immutable vintages: re-importing a key cannot rewrite previously known values.
        con.executemany('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?)', checked)
    return len(checked)


def import_csv(payload):
    text = payload.get('csv', '')
    if not isinstance(text, str) or len(text.encode('utf-8')) > settings()['max_csv_bytes']:
        raise ValueError('CSV 为空或超过允许大小。')
    if payload.get('confirm_historical_dates') is not True:
        raise ValueError('请确认 available_date 是这些数值当时已可获得的日期，而非报告期。')
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not {'code', 'metric', 'value', 'available_date', 'report_date'} <= set(reader.fieldnames or []):
        raise ValueError('CSV 需包含 code,metric,value,available_date,report_date。')
    rows = []
    for r in reader:
        rows.append(r)
        if len(rows) > settings()['max_csv_rows']:
            raise ValueError('CSV 行数超过上限。')
    if not rows:
        raise ValueError('CSV 没有数据行。')
    count = store(rows)
    return {'ok': True, 'rows': count, 'message': f'已处理 {count} 行；重复日期保留先前记录。请检查数据提供者的历史版本是否可靠。'}


def coverage():
    if not DB.exists():
        return {}
    with closing(sqlite3.connect(DB.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        return {metric: {'rows': n, 'stocks': stocks, 'first_date': first, 'last_date': last}
                for metric, n, stocks, first, last in con.execute(
                    'SELECT metric,count(*),count(DISTINCT code),min(available_date),max(available_date) FROM observations GROUP BY metric')}


def status():
    cfg = settings()
    with LOCK:
        job = dict(JOB)
    cov = coverage()
    if job['state'] == 'idle' and cov:
        job['message'] = f'本机已有 {len(cov)} 项财务指标，可用日期见下表。'
    return {'ok': True, **job, 'coverage': cov, 'quarters': cfg['quarters'],
            'default_quarter': cfg['default_quarter'],
            'metrics': sorted(metrics()),
            'message_data': '新抓的报表从抓取日的次日起可用，不用于更早日期。历史回测请导入带真实可用日期的历史版本；百分比填 10 表示 10%。'}


def panels(index, codes, wanted):
    result = {metric: pd.DataFrame(float('nan'), index=index, columns=codes) for metric in wanted}
    if not DB.exists() or not len(index) or not wanted:
        return result
    with closing(sqlite3.connect(DB.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        rows = con.execute('SELECT code,metric,value,available_date,report_date FROM observations WHERE available_date<=? ORDER BY available_date,report_date', (str(index[-1]),)).fetchall()
    buckets, code_set = {}, set(codes)
    for code, metric, value, available, report in rows:
        if code in code_set and metric in wanted:
            buckets.setdefault((code, metric), []).append((available, report, value))
    cfg = settings()
    max_age = cfg['max_age_days']
    for (code, metric), records in buckets.items():
        i, report, value = 0, '', float('nan')
        age_limit = cfg['freshness_days'].get(metric, max_age)
        for day in index:
            while i < len(records) and records[i][0] <= day:
                available, report_new, v = records[i]
                # A late correction to an older report must not displace a newer report.
                if report_new >= report:
                    report, value = report_new, v
                i += 1
            if report and (date.fromisoformat(day)-date.fromisoformat(report)).days <= age_limit:
                result[metric].at[day, code] = value
    return result


def _fetch(quarter):
    import akshare as ak
    from data.fetcher_akshare import _no_proxy
    # The source also returns OTC and B shares. Match the downloaded A-share directory.
    from data.cache import CACHE_DIR
    universe_db = CACHE_DIR / 'stock_basic.db'
    if not universe_db.exists():
        raise ValueError('请先获取 A 股股票目录，再获取财报。')
    with closing(sqlite3.connect(universe_db.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        codes = {code.split('.')[0]: code for (code,) in con.execute('SELECT code FROM stock_basic')}
    with _no_proxy(timeout=25):
        df = ak.stock_yjbb_em(date=quarter)
    today = date.today()
    rows, mapping = [], settings()['akshare_fields']
    report = date.fromisoformat(quarter[:4]+'-'+quarter[4:6]+'-'+quarter[6:])
    for _, r in df.iterrows():
        code = str(r.get('股票代码', '')).zfill(6)
        if code not in codes:
            continue
        announcement = pd.to_datetime(r.get('最新公告日期'), errors='coerce')
        if pd.isna(announcement):
            continue
        available = max(today + timedelta(days=1), announcement.date() + timedelta(days=1))
        for field, metric in mapping.items():
            v = pd.to_numeric(r.get(field), errors='coerce')
            if pd.notna(v) and math.isfinite(v):
                rows.append(dict(code=codes[code], metric=metric, value=float(v), report_date=report.isoformat(),
                                 available_date=available.isoformat(), source='AKShare stock_yjbb_em / 东方财富最新版本'))
    return {'rows': store(rows), 'stocks': len({r['code'] for r in rows}), 'available_date': (today+timedelta(days=1)).isoformat()}


def sync(payload):
    quarter = payload.get('quarter', settings()['default_quarter'])
    if quarter not in settings()['quarters']:
        raise ValueError('请从财报季度列表中选择。')
    with LOCK:
        if JOB['state'] == 'running':
            return {'ok': True, **JOB}
        JOB.update(state='running', message=f'正在通过 AKShare 获取 {quarter} 财报…', quarter=quarter)
    def worker():
        try:
            proc = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'data.research_fundamentals', '--fetch', quarter],
                                  cwd=BASE, capture_output=True, text=True, encoding='utf-8', timeout=180,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if proc.returncode:
                raise ValueError('数据源暂时无法访问或返回异常，请稍后重试。')
            info = json.loads(proc.stdout.strip().splitlines()[-1])
            if not info['rows']:
                raise ValueError('该季度未返回有效报表，请选择已披露完毕的季度。')
            with LOCK:
                JOB.update(state='done', message=f"已取得 {info['stocks']} 家公司财报，最早从 {info['available_date']} 起参与选股。", **info)
        except Exception as e:
            with LOCK:
                JOB.update(state='failed', message='获取失败：' + (str(e) if isinstance(e, ValueError) else '请求超过等待时间，请稍后重试。'))
    threading.Thread(target=worker, daemon=True, name='research-finance').start()
    return {'ok': True, **JOB}


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--fetch':
        print(json.dumps(_fetch(sys.argv[2])))
