"""Small bounded caches for metadata only; never reuse backtest price panels."""
from copy import deepcopy
import threading
import time

_CACHE = {}
_LOCK = threading.RLock()


def file_stamp(path):
    result = []
    for target in (path, path.with_name(path.name+'-wal')):
        try:
            stat = target.stat()
            result.append((str(target),stat.st_mtime_ns,stat.st_size))
        except OSError:
            result.append((str(target),None,None))
    return tuple(result)


def metadata(namespace, stamp, loader, *, copy_result=True):
    from factors.research_library import read_config
    ttl = read_config('research_controls')['metadata_cache_seconds']
    key = (namespace,stamp)
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and now-cached[0] < ttl:
            return deepcopy(cached[1]) if copy_result else cached[1]
        value = loader()
        # Each namespace keeps only its newest snapshot.
        for old in [k for k in _CACHE if k[0]==namespace]:
            _CACHE.pop(old,None)
        _CACHE[key] = (now,value)
        return deepcopy(value) if copy_result else value
