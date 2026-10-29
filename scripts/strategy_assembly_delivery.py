"""Export frozen strategy bundles, verified platform archives and a Chinese report."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import time
import numpy as np
import pandas as pd
from factors.research_library import read_config
from factors.research_controls import group_for,policy as control_policy,catalogue as control_catalogue
from backtest import research_runner as runner
from backtest.research_assembly import signals
from backtest.research_timing import evaluate
from scripts.mainboard_repair_research import load_cached_snapshot,canonical_hash
from scripts.mainboard_strategy_research import write_json,benchmark_snapshot
from scripts.factor_parameter_portfolios import read_execution
from data.cache import DailyCache

ROOT=Path(__file__).resolve().parents[1]


def contribution_audit(name):
    result=json.loads((runner.ARCHIVES/(name+'.json')).read_text(encoding='utf-8'))
    cashflows={}
    for trade in result['trades']:
        amount=trade['amount']-trade['commission']-trade['stamp_tax'] if trade['side']=='sell' else -trade['amount']-trade['commission']
        cashflows[trade['code']]=cashflows.get(trade['code'],0.)+amount
    for position in result['position_details']:cashflows[position['code']]=cashflows.get(position['code'],0.)+position['market_value']
    ending=result['final_cash']+sum(p['market_value'] for p in result['position_details'])
    profit=ending-result['params']['initial_capital']
    if abs(sum(cashflows.values())-profit)>1e-5:raise AssertionError('Stock contributions do not reconcile to account profit')
    ordered=sorted(cashflows.items(),key=lambda item:item[1],reverse=True)
    return dict(archive=name,net_profit=profit,traded_stocks=len(ordered),includes_terminal_mark_to_market=True,
        top1_share_of_net_profit=ordered[0][1]/profit if ordered and profit>0 else None,
        contributions=[dict(code=code,net_pnl=value,initial_capital_contribution=value/result['params']['initial_capital']) for code,value in ordered])


def archive(row,composition,variant,contract,panels,cfg,source,base,out,app_cfg,policy,signature):
    key=hashlib.sha256((row['identity']+'platform_archive').encode()).hexdigest()[:32]
    name='research_assembled_'+key;target=runner.ARCHIVES/(name+'.json')
    bundle_id=row['round_id']+'_'+row['composition'];bundle=out/'strategy_bundles'/(bundle_id+'.json')
    bundle.parent.mkdir(exist_ok=True)
    write_json(bundle,dict(identity=row['identity'],strategy=row,composition=composition,execution=variant,
        universe_policy=base['universe'],snapshot_hash=signature,research_identity=contract['identity'],source_config=cfg,
        reproduction='python scripts/assemble_mainboard_strategies.py --stage all',validation_is_fresh_blind=False))
    if not target.exists():
        codes=composition['codes'];view={k:v[codes] for k,v in panels.items()}
        score=pd.DataFrame(np.load(ROOT/composition['score_path'],mmap_mode='r'),index=panels['close'].index,columns=codes)
        view.update(signals(view,variant));view['_selection_policy']={'retain_rank':int(math.ceil(source['portfolio']['primary_topn']*source['portfolio']['candidate_buffer']))}
        if variant['timing_rule']!='none':
            frame=DailyCache().get_daily(policy['execution']['timing_index'],adjust='none').set_index('date').sort_index()
            gate=evaluate(frame,variant['timing_rule'],dict(benchmark=policy['benchmark'],rules=policy['timing_rules'])).reindex(score.index)
            view['_timing']=pd.DataFrame(np.broadcast_to(gate.to_numpy()[:,None],score.shape),index=score.index,columns=codes);score=score.where(gate.eq(1),axis=0)
        factor_list=[];effective={};overrides={};groups=control_catalogue();controls=control_policy()
        for slot,record in composition['records'].items():
            factor_list.append(dict(name=record['factor_key'],sign=record['direction'],weight=composition['weights'][slot]))
            effective[record['factor_key']]=dict(record['definition'],kind='technical')
            group=group_for(record['factor_key'],record['definition'],controls)
            if group in groups:overrides[group]={k:v for k,v in record['definition']['params'].items() if k in groups[group]['fields']}
        view['_indicator_specs']=effective
        p={**{k:v['default'] for k,v in app_cfg['parameters'].items()},**row['params'],
           'strategy':'assembled','stocks':len(codes),'codes':','.join(codes),'indicator_params':overrides}
        meta=dict(id='assembled',name=row['label']+' · '+str(row['target_exposure_pct'])+'%仓位上限',category='组建策略研究',factor_list=factor_list,
                  factors=[dict(f,label=app_cfg['factor_catalog'][f['name']]['label']) for f in factor_list])
        data=dict(available_stocks=len(codes),selected_codes=codes,selection=composition['scope'],snapshot_hash=signature,price_adjustment='qfq')
        result=runner.build_result(view,data,score,p,app_cfg,meta,time.monotonic(),archive=False)
        if abs(result['metrics']['annual_return']-row['metrics']['annual_return'])>1e-10:raise AssertionError('Archive replay annual return differs')
        if abs(result['metrics']['max_drawdown']-row['metrics']['max_drawdown'])>1e-10:raise AssertionError('Archive replay drawdown differs')
        result.update(name=name,run_id=key,strategy_bundle=str(bundle.relative_to(ROOT)),benchmark=policy['benchmark'])
        reference=benchmark_snapshot(policy['benchmark']['code'],tuple(result['dates']))
        result['bench_nav']=reference['nav'];result['excess_nav']=(np.asarray(result['nav'])/np.asarray(reference['nav'])).tolist()
        result['metrics'].update({k:row['metrics'][k] for k in ['benchmark_annual_return','annual_excess_return']})
        result['metrics']['annual_excess_pp']=row['metrics']['annual_excess_return']*100
        result['excess_annual']=row['metrics']['annual_excess_return']
        result['research_audit']=dict(selection_uses_validation=False,validation_is_fresh_blind=False,formal_acceptance=False,
            strategy_bundle=str(bundle.relative_to(ROOT)),explanation='发现期组建后冻结；历史已被观察，缺完整退市和ST数据。',
            exact_reproduction='python scripts/assemble_mainboard_strategies.py --stage all')
        result['warnings'].insert(0,'本策略含专用股票池、持仓缓冲、组合和均线规则；本页用于查看账本，精确复现请运行组建研究脚本及对应冻结参数包。')
        if composition['scope']=='valuation_sample':result['warnings'].insert(0,'估值仅固定代码样本，未核验原始财报版本，属于探索性对照。')
        from backtest.research_report import build_report
        result['report']=build_report(result);runner.save_result(result)
    return dict(name=name,url='/backtests?run='+name,strategy_bundle=str(bundle.relative_to(ROOT)))


def deliver(cfg,source,base,out):
    data=json.loads((out/'results.json').read_text(encoding='utf-8'));rows=data['records'];policy=read_execution(source)
    panels,eligible,signature,audit=load_cached_snapshot(base)
    if signature!=data['snapshot_hash']:raise ValueError('Delivery snapshot differs from research')
    app_cfg=runner.research_config();entries=[];archives=[];contributions=[]
    for contract in data['contracts']:
        rd=contract['round'];folder=out/rd['id'];defs=contract['composition_definitions']
        for selected in contract['chosen']:
            key=selected['composition'];variant=selected['variant'];compo=defs[key]
            find=lambda phase,stress:next(r for r in rows if r['round_id']==rd['id'] and r['composition']==key and r['variant']==variant and r['phase']==phase and r['stress']==stress)
            forward=find('continuous_forward',False);pressure=find('continuous_forward',True)
            full=find('overall',False);full_stress=find('overall',True);validation=find('validation',False)
            research_pass=bool(all(r['constraints_pass'] for r in [selected['base'],selected['pressure'],forward,pressure,full,full_stress]) and not selected['calibration']['below_minimum'])
            archived=archive(forward,compo,cfg['execution_variants'][variant],contract,panels,cfg,source,base,out,app_cfg,policy,signature);archives.append(archived)
            attribution=contribution_audit(archived['name']);contributions.append(attribution)
            reference=next(r for r in rows if r['round_id']==rd['id'] and r['phase']=='reference_continuous_forward' and not r['stress'])
            matched_phase='matched_continuous_forward' if compo['scope']=='valuation_sample' else 'matched_'+key+'_continuous_forward'
            matched=next((r for r in rows if r['round_id']==rd['id'] and r['phase']==matched_phase and not r['stress']),forward)
            entry=dict(round_id=rd['id'],composition=key,variant=variant,label=forward['label'],scope=compo['scope'],target_exposure_pct=forward['target_exposure_pct'],
                period=forward['period'],annual_return=forward['metrics']['annual_return'],max_drawdown=forward['metrics']['max_drawdown'],turnover=forward['annual_one_way_turnover'],
                pressure_annual_return=pressure['metrics']['annual_return'],pressure_max_drawdown=pressure['metrics']['max_drawdown'],
                average_exposure=forward['average_exposure'],overall_annual_return=full['metrics']['annual_return'],overall_max_drawdown=full['metrics']['max_drawdown'],
                validation_annual_return=validation['metrics']['annual_return'],research_pass=research_pass,formal_acceptance=False,
                reference_annual_return=reference['metrics']['annual_return'],reference_max_drawdown=reference['metrics']['max_drawdown'],
                matched_core_annual_return=matched['metrics']['annual_return'],matched_core_max_drawdown=matched['metrics']['max_drawdown'],
                top1_share_of_net_profit=attribution['top1_share_of_net_profit'],traded_stocks=attribution['traded_stocks'],
                features={slot:dict(factor_key=r['factor_key'],parameter=r['parameter'],value=r['value'],direction=r['direction'],weight=compo['weights'][slot]) for slot,r in compo['records'].items()},
                **archived)
            entries.append(entry)
    write_json(out/'candidate_strategies.json',dict(snapshot_hash=signature,as_of=cfg['as_of'],data_end=base['data_end'],selection_uses_validation=False,
        validation_is_fresh_blind=False,entries=entries,ledger_count=len(rows),note='已观察历史上的迭代组建；技术全段含发现期；估值样本与全主板分开比较。'))
    write_json(out/'platform_archives.json',archives)
    write_json(out/'stock_contributions.json',contributions)
    sources=json.loads((ROOT/source['output_dir']/'source_register.json').read_text(encoding='utf-8'))
    sources['sources'].append(dict(source_id='S_ASSEMBLY',title='低相关多因子与均线策略组建',evidence_label='实测历史回放',path=str(out),
        checked_at=datetime.now(timezone.utc).isoformat(),snapshot_hash=signature,validation_is_fresh_blind=False))
    write_json(out/'source_register.json',sources)
    write_json(out/'implementation_hashes.json',{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['backtest/research_assembly.py','scripts/assemble_mainboard_strategies.py','scripts/strategy_assembly_delivery.py','backtest/research_portfolio.py']})
    lines=['# 主板低频策略组建研究',
        f"\n执行日{cfg['as_of']}，Asia/Shanghai；冻结股票数据截止{base['data_end']}。主板价格池{len(panels['close'].columns)}只，估值仅原固定样本。组合与规则在本轮发现期选定后冻结，但此前历史已经被观察，因此所有结论都是时间隔离回放，不是新盲测。[S_ASSEMBLY]",
        '\n## 组建方法',
        '\n以配置指定的换手率波动参数低值优先为核心；补充因子来自上一轮各发现期内通过稳健性检验的参数。技术与估值使用各自相同股票池做相关性检查，不以全主板收益直接对比估值样本收益。',
        f"\n因子按当日合资格股票百分位归一化，在发现期选择 |r|≤{cfg['max_abs_factor_correlation']} 的补充项，再用配置权重组合。PCA仅用于训练数据归一化/降维审计，累计解释方差至少{cfg['pca_explained_variance']:.0%}；没有把主成分自动解释成收益因子，也没有训练机器学习收益模型。",
        '\n本配置比较的执行规则：'+ '、'.join(v['label'] for v in cfg['execution_variants'].values())+'。事件模式只在有空位且买入条件成立时进入，技术退出、止损止盈都按前收盘决定；指数强风险退出允许例外，恢复条件也只使用已知历史。普通交易间隔和单日股票数沿用源研究约束。',
        '\n仓位上限依据各配置的发现期回撤与换手校准，再重新检验基础和压力成本下的训练账本。验证前将组合、权重、均线规则与仓位保存到frozen_strategies.json。技术无固定年化收益要求；沿用10%回撤、低换手和活跃度门槛。',
        '\n## 训练期选定的低相关输入', '\n| 轮次 / 股票池 | 因子参数 | 最大绝对相关 | PCA | 累计解释方差 |','|---|---|---:|---|---:|']
    for key,spec in cfg['compositions'].items():
        if spec.get('core_pool_size'):lines.insert(lines.index('\n## 训练期选定的低相关输入'),f"\n{spec['label']}：先按核心方向限定最稳定的{spec['core_pool_size']}只，再按组合权重排序；不使用未来收益决定候选名单。")
    for contract in data['contracts']:
        f=json.loads((out/contract['round']['id']/'frozen_features.json').read_text(encoding='utf-8'))
        for scope,a in f['audits'].items():
            if scope not in {s['scope'] for s in cfg['compositions'].values()}:continue
            p=a['pca'];lines.append(f"|{contract['round']['id']} / {scope}|{', '.join(a['selected_parameters'])}|{a['max_abs_factor_correlation']:.3f}|{p['input_dimensions']}→{p['components']}|{p['cumulative_explained_variance']:.2%}|")
    lines+=['\n以上相关性与解释方差是训练期数据上的描述，后续时期可能变化；低相关通常需要保留更多主成分，不能强行规定降至某个维数。',
        '\n## 冻结策略连续回放',
        '\n| 轮次 / 组合与规则 | 仓位上限 | 区间 | 年化 | 压力年化 | 回撤 | 压力回撤 | 单边年换手 | 平均仓位 | 数值研究门槛 |','|---|---:|---|---:|---:|---:|---:|---:|---:|---|']
    for e in entries:lines.append(f"|{e['round_id']} / {e['label']}|{e['target_exposure_pct']}%|{e['period']['start']}—{e['period']['end']}|{e['annual_return']:.2%}|{e['pressure_annual_return']:.2%}|{e['max_drawdown']:.2%}|{e['pressure_max_drawdown']:.2%}|{e['turnover']:.2f}|{e['average_exposure']:.1%}|{'通过' if e['research_pass'] else '未通过'}|")
    lines+=['\n数值研究门槛同时检查发现期和后续连续/全段的基础及压力成本、回撤、持仓数量、交易间隔、换手和活跃度；不等于正式数据质量验收。估值行仍属于财报版本未核验的探索性对照。',
        '\n## 技术全段回放与同窗核心对照',
        '\n技术全段按同一套冻结规则从原始研究起点运行至数据末，包含发现期，不能称为样本外。相同窗口的核心45%上限作为对照，评价多因子是否真正增益。',
        '\n| 轮次 / 技术组合 | 全段年化 | 全段回撤 | 后续连续年化 | 同窗核心45%年化 | 后续连续回撤 |','|---|---:|---:|---:|---:|---:|']
    for e in entries:
        if e['scope']=='mainboard':lines.append(f"|{e['round_id']} / {e['label']}|{e['overall_annual_return']:.2%}|{e['overall_max_drawdown']:.2%}|{e['annual_return']:.2%}|{e['reference_annual_return']:.2%}|{e['max_drawdown']:.2%}|")
    lines+=['\n## 相同股票池、规则与仓位的因子增益对照',
        '\n仓位校准可能使组合比核心持有更多现金，因此另设完全相同股票池、交易规则和仓位上限的核心对照，分离增加因子的影响。',
        '\n| 轮次 / 组合 | 仓位上限 | 组合年化 | 同条件核心年化 | 组合回撤 | 同条件核心回撤 |','|---|---:|---:|---:|---:|---:|']
    for e in entries:
        if e['composition']!='core':lines.append(f"|{e['round_id']} / {e['label']}|{e['target_exposure_pct']}%|{e['annual_return']:.2%}|{e['matched_core_annual_return']:.2%}|{e['max_drawdown']:.2%}|{e['matched_core_max_drawdown']:.2%}|")
    lines+=['\n## 核心策略执行规则的发现期拆解',
        '\n仅展示发现期比较，未用下列规则的验证表现重新选择策略。事件条件与指数退出规则均按冻结配置执行；低仓位或交易不足也会导致未通过。',
        '\n| 轮次 / 执行规则 | 校准仓位 | 训练年化 | 压力训练年化 | 回撤 | 单边年换手 | 平均仓位 | 两套成本研究门槛 |','|---|---:|---:|---:|---:|---:|---:|---|']
    for r in rows:
        if r['composition']!='core' or r['phase']!='discovery_budget' or r['stress']:continue
        s=next(x for x in rows if x['round_id']==r['round_id'] and x['composition']=='core' and x['variant']==r['variant'] and x['phase']==r['phase'] and x['stress'])
        lines.append(f"|{r['round_id']} / {cfg['execution_variants'][r['variant']]['label']}|{r['target_exposure_pct']}%|{r['metrics']['annual_return']:.2%}|{s['metrics']['annual_return']:.2%}|{r['metrics']['max_drawdown']:.2%}|{r['annual_one_way_turnover']:.2f}|{r['average_exposure']:.1%}|{'通过' if r['constraints_pass'] and s['constraints_pass'] else '未通过'}|")
    lines+=['\n## 估值组合的同池对照', '\n| 轮次 | 估值组合年化 | 同股票池/规则/仓位核心年化 | 估值组合回撤 | 核心回撤 |','|---|---:|---:|---:|---:|']
    for e in entries:
        if e['scope']!='valuation_sample':continue
        m=next(r for r in rows if r['round_id']==e['round_id'] and r['phase']=='matched_continuous_forward' and not r['stress'])
        lines.append(f"|{e['round_id']}|{e['annual_return']:.2%}|{m['metrics']['annual_return']:.2%}|{e['max_drawdown']:.2%}|{m['metrics']['max_drawdown']:.2%}|")
    technical=[e for e in entries if e['scope']=='mainboard' and e['composition']!='core']
    lines+=['\n## 核心候选收益集中度',
        '\n按每只股票净买卖现金流加期末持仓估值归因，逐项核对到组合净利润；包含未平仓估值，不是实盘已兑现利润。以下仅作回放归因，不是买入名单。',
        '\n| 轮次 | 全期交易股票数 | 最大单股占净利润 | 贡献最高的五只代码 |','|---|---:|---:|---|']
    for e in entries:
        if e['composition']!='core':continue
        a=next(x for x in contributions if x['archive']==e['name'])
        lines.append(f"|{e['round_id']}|{a['traded_stocks']}|{'不适用' if a['top1_share_of_net_profit'] is None else format(a['top1_share_of_net_profit'],'.1%')}|{', '.join(x['code'] for x in a['contributions'][:5])}|")
    lines+=['\n## 本轮结论与使用',
        f"\n共归档{len(rows)}份原始/校准/验证/压力/连续/全段账本，形成{len(entries)}个冻结候选及对应平台图形报告。技术组合中{sum(e['annual_return']>e['reference_annual_return'] for e in technical)}/{len(technical)}个后续连续收益高于同窗核心45%对照。保留失败与劣化结果，不把增加因子数量本身当成改进。",
        '\n当前缓存含幸存者偏差，未补齐历史退市与ST状态。估值缺原始财报版本核验。账本使用前复权小数份额，未实现100股整手、盘口排队和成交量参与率；一字板过滤使用当日完整日线，属于保守事后筛查。期末不强制出售；现金不计利息。',
        '\n普通交易间隔与单边年换手是不同指标。本轮单边年换手3倍、平均仓位10%是额外工作门槛。2025单年不足252交易日，不升级正向等级；连续和全段窗口另行评价。',
        '\n运行 `python scripts/assemble_mainboard_strategies.py --stage all` 复现；修改规则应另建实验配置和输出目录，保留旧结果。平台“策略管理”的组建研究卡片可查看归档账本。通用回测页直接填几个因子不能完整复现股票池、缓冲和事件规则。',
        '\n关键文件：policy.json、round_*/frozen_features.json、frozen_strategies.json、runs/*.json、strategy_bundles/*.json、candidate_strategies.json、platform_archives.json、source_register.json、implementation_hashes.json。原始第三方数据仅本地使用，不再分发。']
    (out/'主板策略组建研究.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    write_json(out/'factor_summaries.json',[dict(factor_key=e['composition'],backtest_id=e['name'],verdict=runner._grade_verdict({'annual_return':e['annual_return'],'max_drawdown':e['max_drawdown'],'sharpe':next(r['metrics']['sharpe'] for r in rows if r['round_id']==e['round_id'] and r['composition']==e['composition'] and r['phase']=='continuous_forward' and not r['stress'])}),
        annual_return=e['annual_return'],max_drawdown=e['max_drawdown'],data_range=e['period'],notes='组合策略而非新增指标；历史已观察、非盲测') for e in entries])
    print(out/'主板策略组建研究.md')
