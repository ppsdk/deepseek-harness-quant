"""One catalogue connects executable strategies to articles, tutorials and projects."""
def catalog():
    from backtest.research_runner import research_config
    from factors.research_library import read_config
    cfg=research_config();refs=read_config('research_references');entries=[]
    # Read archived ledgers once so every strategy, including a newly added
    # config strategy, gets the same optional evaluation block.
    latest={}
    try:
        import json
        from pathlib import Path
        from backtest.research_runner import ARCHIVES
        for path in ARCHIVES.glob('research_*.json'):
            try:
                item=json.loads(path.read_text(encoding='utf-8'))
                key=item.get('strategy')
                if key and (key not in latest or item.get('generated_at','') > latest[key].get('generated_at','')):
                    latest[key]=item
            except (OSError, ValueError, TypeError):
                continue
    except OSError:
        latest={}
    from factors.research_evaluation import strategy_evaluation
    for key,m in cfg['strategies'].items():
        conditions=[dict(f,label=cfg['factor_catalog'][f['name']]['label']) for f in m['factor_list']]
        sources=[r for r in refs.get('strategies',[]) if r['id']==key]
        articles=[r for r in refs.get('articles',[]) if key in r.get('strategy_ids',[])]
        archived=latest.get(key) or {}
        ev=strategy_evaluation({**archived,'evaluated_runs':1 if archived else 0})
        entries.append(dict(id=key,kind='选股策略',title=m['name'],category=m['category'],description=m['desc'],
                            status=m['data_note'],available=m['available'],strategy_ids=[key],conditions=conditions,
                            research_status=ev['state'],evaluation=ev,
                            sources=[dict(title=r['title'],url=refs['sources'][r['source']]['url']) for r in sources]+[dict(title=r['title'],url=r['url']) for r in articles]))
    # Reference material remains metadata (and is not rendered as an
    # executable strategy card), while counts expose the complete catalogue
    # to audits and future pages without duplicating frontend structures.
    counts={k:sum(e['kind']==k for e in entries) for k in dict.fromkeys(e['kind'] for e in entries)}
    counts['社区文章参考']=len(refs.get('articles', []))
    counts['开源项目与教程']=len(refs.get('tutorials', []))
    return dict(ok=True,entries=entries,reference_entries={
        '社区文章参考': refs.get('articles', []),
        '开源项目与教程': refs.get('tutorials', []),
    },counts=counts)
