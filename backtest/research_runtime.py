"""Record the actual local implementation and dependency versions for new experiments."""
from functools import lru_cache
import hashlib
from importlib.metadata import version,PackageNotFoundError
from pathlib import Path
import platform

BASE=Path(__file__).resolve().parent.parent


@lru_cache(maxsize=4)
def _snapshot(stamps):
    digest=hashlib.sha256()
    for filename,_,_ in stamps:
        path=Path(filename)
        digest.update(str(path.relative_to(BASE)).replace('\\','/').encode())
        digest.update(path.read_bytes())
    dependencies={}
    for name in ('numpy','pandas','PyYAML','scikit-learn'):
        try:dependencies[name]=version(name)
        except PackageNotFoundError:dependencies[name]='未安装'
    return dict(source_hash=digest.hexdigest(),python=platform.python_version(),dependencies=dependencies,
                note='代码与配置摘要用于检查实现是否变化；行情指纹单独保存，摘要不包含原始行情。')


def snapshot():
    files=sorted({p for folder in ('backtest','factors','data') for p in (BASE/folder).glob('research*.py')} |
                 {p for name in ('research*','factor_library','alpha101','backtest_report')
                  for p in (BASE/'config').glob(name+'.yaml*')} |
                 {BASE/p for p in ('factors/alpha101_engine.py','factors/factor_engine.py',
                                  'data/cache.py','data/akshare_loader.py','data/fetcher_akshare.py','backtest/bt_report.py')})
    stamps=tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) for p in files)
    return _snapshot(stamps)
