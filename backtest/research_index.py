"""Index constituent lists — shared utility used by research_runner and other modules.
Replaces the equivalent function that lived in dual_momentum.py.
"""
import json
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    except Exception:
        return None


def _write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')


def constituents(symbol, cfg):
    """Return current index constituent list (CSINDEX), cached locally for max_age_days.
    cfg must contain a 'constituents' block with cache_dir, max_age_days, exchanges, source.
    Returns (item_dict, warning_or_None).
    """
    c = cfg['constituents']
    folder = BASE / c['cache_dir']
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (symbol + '.json')
    cached = _read(path)
    if cached:
        try:
            age = (date.today() - date.fromisoformat(cached['fetched_at'][:10])).days
            if age < c['max_age_days']:
                return cached, None
        except (KeyError, ValueError):
            pass
    try:
        import akshare as ak
        from data.fetcher_akshare import _no_proxy
        with _no_proxy(timeout=25):
            df = ak.index_stock_cons_weight_csindex(symbol=symbol)
        codes = sorted({
            str(r['成分券代码']).zfill(6) + '.' + c['exchanges'][str(r['交易所']).strip()]
            for _, r in df.iterrows()
            if str(r.get('交易所', '')).strip() in c['exchanges']
        })
        if not codes:
            raise ValueError('数据源没有返回成分股。')
        item = dict(symbol=symbol, codes=codes, as_of=str(df['日期'].max())[:10],
                    source=c['source'], fetched_at=datetime.now().isoformat(timespec='seconds'))
        _write(path, item)
        return item, None
    except Exception as error:
        if cached:
            return cached, '成分股名单更新失败，使用 %s 取得的旧名单：%s' % (
                cached['fetched_at'][:10], str(error)[:120])
        raise ValueError('无法取得指数成分股：' + str(error)[:160]) from None
