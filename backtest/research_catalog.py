"""One catalogue connects executable strategies to articles, tutorials and projects."""
def catalog():
    from backtest.research_runner import research_config
    from factors.research_library import read_config
    cfg=research_config();refs=read_config('research_references');entries=[]
    for key,m in cfg['strategies'].items():
        conditions=[dict(f,label=cfg['factor_catalog'][f['name']]['label']) for f in m['factor_list']]
        sources=[r for r in refs.get('strategies',[]) if r['id']==key]
        articles=[r for r in refs.get('articles',[]) if key in r.get('strategy_ids',[])]
        entries.append(dict(id=key,kind='选股策略',title=m['name'],category=m['category'],description=m['desc'],
                            status=m['data_note'],available=m['available'],strategy_ids=[key],conditions=conditions,
                            sources=[dict(title=r['title'],url=refs['sources'][r['source']]['url']) for r in sources]+[dict(title=r['title'],url=r['url']) for r in articles]))
    dual=read_config('dual_momentum')
    if dual.get('catalog'):
        c=dual['catalog']
        entries.append(dict(id=c['id'],kind='选股策略',title=c['title'],category=c['category'],description=c['description'],status=c['status'],
                            available=True,strategy_ids=[],conditions=[],runner='#dual-momentum',
                            sources=[dict(title=r['title'],url=r['url']) for r in dual.get('references',[])]))
    return dict(ok=True,entries=entries,counts={k:sum(e['kind']==k for e in entries) for k in dict.fromkeys(e['kind'] for e in entries)})
