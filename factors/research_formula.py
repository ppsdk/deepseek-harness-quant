"""Safe formula compilation, visual condition construction and local previews."""
import json
import re
import numpy as np


def settings():
    from factors.research_library import read_config
    return read_config('research_workflow')['formula']


def compile_formula(formula, definitions=None, key=None):
    from factors.research_manager import expression_names
    from factors.research_library import factor_definitions,pattern_requirements
    from factors.alpha101_engine import parse
    definitions=factor_definitions() if definitions is None else definitions
    names=expression_names(formula)
    fields={'open','high','low','close','volume','amount','turn','vwap','raw_close','cap'}
    advances={n for n in names if re.fullmatch(r'adv[1-9][0-9]*',n)}
    dependencies=names-fields-{'returns'}-advances
    from factors.factor_engine import FACTOR_FUNCS
    unknown=dependencies-(set(definitions)|set(FACTOR_FUNCS)|{'turn_20d'})
    if unknown:raise ValueError('公式引用了未知指标：'+', '.join(sorted(unknown)))
    required=names&fields|({'close'} if 'returns' in names else set())
    if advances:required.add('amount')
    def requirements(n,seen):
        if n==key or n in seen:raise ValueError('因子之间不能循环引用。')
        m=definitions.get(n,{})
        out=set(m.get('requires',['turn'] if n=='turn_20d' else ['close']))
        for dep in m.get('dependencies',m.get('recipe',{}).get('features',[])):out|=requirements(dep,seen|{n})
        return out
    for dep in dependencies:required|=requirements(dep,set())
    temporal={'ma','ema','ref','rsi','count','delay','delta','ts_min','ts_max','sum','stddev',
              'product','ts_argmax','ts_argmin','ts_rank','decay_linear','correlation','covariance'}
    maximum=settings()['max_window']
    def warm(node):
        kind=node[0]
        if kind=='number':return 0
        if kind=='name':
            if node[1] in advances:return int(node[1][3:])
            dependency=definitions.get(node[1],{})
            if dependency.get('kind')=='expression':return warm(parse(dependency['formula']))
            if dependency:return max(int(dependency.get('warmup_calendar_days',0)/3),sum(int(v) for v in dependency.get('params',{}).values() if isinstance(v,int)))
            return 1 if node[1]=='returns' else 0
        if kind=='call':
            name,args=node[1:];base=max((warm(c) for c in args),default=0)
            if name in temporal or (name in ('min','max') and args[1][0]=='number'):
                value=args[-1]
                if value[0]!='number' or not 1<=value[1]<=maximum or not value[1].is_integer():
                    raise ValueError(name+' 的周期须为1至'+str(maximum)+'的整数，不能引用未来。')
                base+=int(value[1])
            return base+int(name=='cross')
        return max((warm(c) for c in node[1:] if isinstance(c,tuple)),default=0)
    periods=warm(parse(formula))
    extra,_=pattern_requirements(dependencies,definitions)
    for dep in dependencies:extra=max(extra,int(definitions.get(dep,{}).get('warmup_calendar_days',0)))
    return dict(kind='expression',formula=formula.strip(),dependencies=sorted(dependencies),requires=sorted(required),
                warmup_calendar_days=max(extra,periods*3+30),chart=True,overlay=False)


def build_conditions(payload):
    from factors.research_library import read_config
    cfg=settings();rows=payload.get('conditions');join=payload.get('join','all')
    if join not in cfg['joins'] or not isinstance(rows,list) or not 1<=len(rows)<=read_config('research_workflow')['signals']['max_conditions']:
        raise ValueError('请添加条件并选择全部满足或任一满足。')
    formulas=[]
    for row in rows:
        if not isinstance(row,dict):raise ValueError('条件格式不正确。')
        left,right=row.get('left',''),row.get('right','');operator=row.get('operator')
        if operator not in cfg['comparisons']:raise ValueError('请选择比较方式。')
        compile_formula(left);compile_formula(right)
        spec=cfg['comparisons'][operator]
        if 'function' in spec:
            if spec.get('reverse'):left,right=right,left
            formulas.append(spec['function']+'(('+left+'), ('+right+'))')
        else:formulas.append('('+left+') '+spec['symbol']+' ('+right+')')
    formula=(' && ' if join=='all' else ' || ').join('('+f+')' for f in formulas)
    return dict(ok=True,formula=formula,spec=compile_formula(formula))


def preview(payload):
    from backtest.research_runner import research_config,validate_params,load_panels
    from factors.research_library import factor_definitions,raw_factor
    from data.research_charts import chart_data
    cfg=research_config();definitions=factor_definitions();key='formula_preview'
    while key in definitions:key+='x'
    spec=compile_formula(payload.get('formula',''),definitions)
    spec.update(label=payload.get('label') or '未保存指标',overlay=bool(payload.get('overlay',False)))
    definitions={**definitions,key:spec}
    cfg['factor_catalog'][key]=spec
    cfg['strategies'][key]={'factor_list':[{'name':key}]}
    cfg['warmup_calendar_days']=max(cfg['warmup_calendar_days'],spec['warmup_calendar_days'])
    values={k:v for k,v in payload.items() if k in cfg['parameters']}
    try:stocks=int(values.get('stocks',1))
    except (ValueError,TypeError):raise ValueError('预览股票数量须为整数。') from None
    if not 1<=stocks<=settings()['preview_max_stocks']:raise ValueError('请把预览范围限制为1至'+str(settings()['preview_max_stocks'])+'只。')
    values.update(strategy=key,stocks=stocks,topn=1)
    p=validate_params(values,cfg)
    if not p['codes'].strip():raise ValueError('预览请填写1至'+str(settings()['preview_max_stocks'])+'只完整股票代码。')
    # Extra fields use the same dependency graph as factor tests/backtests.
    # Avoid patching module globals in a threaded server; load_panels accepts explicit definitions.
    panels,data=load_panels(p,cfg,definitions=definitions)
    raw=raw_factor(key,panels,definitions);visible=raw.loc[p['start']:p['end']]
    rows=[dict(date=day,code=code,value=float(value) if np.isfinite(value) else None)
          for day,line in visible.iterrows() for code,value in line.items()]
    chart=chart_data(data['selected_codes'][0],'指标预览',dict(start=p['start'],end=p['end'],indicators=key,
                    indicator_params=json.dumps(p['indicator_params'])),definitions=definitions)
    return dict(ok=True,spec=spec,params=p,data=data,chart=chart,rows=rows[-settings()['preview_max_rows']:],
                valid_values=int(visible.notna().sum().sum()),total_values=int(visible.size),
                message='预览使用本地真实数据，空值不会补0。横截面排名取决于本次所选范围；预览不代替因子检验。')
