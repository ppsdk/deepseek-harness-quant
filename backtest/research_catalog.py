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
    for r in refs.get('tutorials',[]):
        entries.append(dict(r,kind='开源项目与教程',status='外部学习资料；本项目未运行其完整引擎',description=r['description']+' 学完可尝试：'+r['next_step'],strategy_ids=[],conditions=[],sources=[dict(title=r['repository'],url=r['url'])]))
    for r in refs.get('articles',[]):
        entries.append(dict(r,kind='社区文章参考',status=r['data_note'],description=r['description']+' 本地处理：'+r['adaptation'],conditions=[],sources=[dict(title='原文 · '+r['author'],url=r['url'])]))
    for key,r in refs.get('sources',{}).items():
        entries.append(dict(id='source_'+key,kind='文档与项目参考',title=r['label'],category='研究工具',description=r['note'],status='原始文档参考',strategy_ids=[],conditions=[],sources=[dict(title='查看来源',url=r['url'])]))
    return dict(ok=True,entries=entries,counts={k:sum(e['kind']==k for e in entries) for k in dict.fromkeys(e['kind'] for e in entries)})
