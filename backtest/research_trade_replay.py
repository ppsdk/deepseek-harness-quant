"""Serve materialized frozen-run explanations without fitting or simulating."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from factors.research_library import read_config

ROOT=Path(__file__).resolve().parents[1]


@lru_cache(maxsize=12)
def _json(path,mtime,size):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load(path):
    s=path.stat()
    return _json(str(path),s.st_mtime_ns,s.st_size)


@lru_cache(maxsize=12)
def _digest(path,mtime,size):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(path):
    s=path.stat()
    return _digest(str(path),s.st_mtime_ns,s.st_size)


def safe_file(folder,name):
    path=(folder/name).resolve()
    if not path.is_relative_to(folder.resolve()):raise ValueError('复盘文件路径无效。')
    return path


def get(query):
    cfg=read_config('research_trade_replay');entries=[];locations={}
    for key,spec in cfg['studies'].items():
        study=read_config(spec['config']);folder=ROOT/study['output_dir']/spec['output_subdir']
        if not (folder/'index.json').exists():continue
        for entry in load(folder/'index.json')['entries']:
            entries.append(dict(run=entry['run'],title=entry['title'],url=entry['url'],round_id=entry['round_id']))
            locations[entry['run']]=(folder,entry)
    key=query.get('id')
    if not key:return dict(ok=True,entries=entries,title=cfg['title'])
    if not re.fullmatch(r'research_[a-z0-9_]+_[0-9a-f]{32}',key):raise ValueError('回测编号无效。')
    if key not in locations:return dict(ok=True,available=False,message=cfg['unavailable_note'])
    folder,entry=locations[key]
    summary=load(safe_file(folder,entry['summary']))
    from backtest.research_runner import ARCHIVES
    archive=ARCHIVES/(key+'.json')
    if not archive.exists() or fingerprint(archive)!=summary['archive_sha256']:
        return dict(ok=True,available=False,state='stale',message=cfg['stale_note'])
    day=query.get('date')
    if day:
        if day not in summary['dates']:raise ValueError('日期不在这份回放账本内。')
        detail=load(safe_file(folder,entry['days']))[day]
        return dict(ok=True,available=True,run=key,detail=detail)
    ui={k:cfg[k] for k in ['title','note','signal_note','holdings_note','factor_note','trigger_labels','status_labels','feature_labels','page_size']}
    return dict(summary,ui=ui)
