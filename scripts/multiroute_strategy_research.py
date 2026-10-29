"""Run reproducible route research with nested chronological selection and real ledgers."""
import argparse
import gc
import hashlib
import json
from pathlib import Path
import sys
import time
import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from factors.research_library import read_config
from backtest import research_runner as runner
from backtest.research_multiroute import (feature, market_states, causal_graph_features,
    label_panel, fit_preprocessing, make_model, predict_frozen, blended, timed_score)
from scripts.mainboard_repair_research import load_cached_snapshot, canonical_hash
from scripts.four_axis_factor_mining import valuation_panels
from scripts.factor_parameter_portfolios import assess
from scripts.mainboard_strategy_research import write_json, log
from data.research_fundamentals import coverage
from data.cache import DailyCache


SOURCES=['backtest/research_multiroute.py','scripts/multiroute_strategy_research.py',
    'backtest/research_portfolio.py','backtest/research_preprocessing.py',
    'backtest/research_selection.py','scripts/factor_parameter_portfolios.py',
    'scripts/mainboard_repair_research.py','scripts/mainboard_strategy_research.py',
    'backtest/bt_report.py']


def research_identity(cfg, signature):
    return canonical_hash(dict(config=cfg, snapshot=signature,
        code={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}))


def load_or_save(path, value, index, columns):
    if not path.exists(): np.save(path, value().to_numpy(dtype=np.float32))
    return pd.DataFrame(np.load(path, mmap_mode='r'), index=index, columns=columns)


def prepare(cfg, base, out):
    panels, eligible, signature, audit=load_cached_snapshot(base)
    value_cfg=read_config(cfg['valuation_config']); values, sample_codes=valuation_panels(panels, value_cfg)
    cov=coverage(); audit.update(financial_coverage=cov,
        historical_financial_observations_available=any(v['first_date']<=base['data_end'] for v in cov.values()),
        valuation_stocks=len(sample_codes), valuation_source=value_cfg['valuation_source'],
        valuation_vintages_verified=False, valuation_scope='512-code fixed survivor sample; historical ratios, not audited financial vintages')
    identity=research_identity(cfg, signature)
    manifest=out/'experiment_manifest.json'
    if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8'))['identity']!=identity:
        raise ValueError('Experiment identity changed. Preserve existing evidence and use a distinct configured output directory.')
    write_json(manifest, dict(identity=identity, snapshot_hash=signature, config=cfg,
        source_hashes={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in SOURCES},
        validation_is_fresh_blind=False, selection_uses_validation=False,
        reproduced_command='python scripts/multiroute_strategy_research.py --config multiroute_strategy_research'))
    write_json(out/'data_audit.json', audit)
    ranks={}; cache=out/'cache'; cache.mkdir(exist_ok=True)
    for name,spec in cfg['features'].items():
        view=values if spec['operation'].startswith('valuation') else panels
        codes=list(view['close'].columns)
        ranks[name]=load_or_save(cache/(name+'.npy'),
            lambda s=spec,v=view,c=codes:feature(v,s).where(eligible[c]).rank(axis=1,pct=True),
            eligible.index,codes)
    # Exact daily same-pool controls also require usable historical valuations.
    sample_mask=eligible[sample_codes].copy()
    for name in cfg['valuation_features']: sample_mask &= ranks[name].notna()
    masks=dict(mainboard=eligible, valuation_sample=sample_mask)
    frame=DailyCache().get_daily(cfg['execution']['timing_index'],adjust='none').set_index('date').sort_index()
    states=market_states(frame,panels['close'],eligible,cfg['timing'])
    states.to_csv(out/'market_states.csv',encoding='utf-8-sig')
    graph_file=cache/'graph_updates.json'
    graph_names=['graph_'+n for n in cfg['graph']['features']]
    if not all((cache/(n+'.npy')).exists() for n in graph_names):
        g,updates=causal_graph_features(panels['close'][sample_codes], sample_mask,
            {n:ranks[n][sample_codes] for n in cfg['graph']['features']},cfg['graph'])
        for n,v in g.items(): np.save(cache/(n+'.npy'),v.to_numpy(dtype=np.float32))
        write_json(graph_file,dict(updates=updates,config=cfg['graph'],causal=True))
        del g;gc.collect()
    for n in graph_names:
        ranks[n]=pd.DataFrame(np.load(cache/(n+'.npy'),mmap_mode='r'),index=eligible.index,columns=sample_codes)
    counts={scope:dict(stocks=len(mask.columns),latest=int(mask.iloc[-1].sum()),
        first_eligible=str(mask.index[mask.any(axis=1)][0])) for scope,mask in masks.items()}
    audit['route_coverage']=counts; write_json(out/'data_audit.json',audit)
    log('FEATURES READY '+json.dumps(counts))
    return panels,masks,ranks,states,signature,identity


def assessment_config(cfg):
    e=cfg['execution'];pref=cfg['research_preferences']
    return dict(portfolio=dict(primary_topn=e['topn'],max_drawdown=pref['drawdown_reference'],
        max_annual_one_way_turnover=pref['annual_turnover_reference'],
        min_trade_interval_days=e['min_trade_interval_days'],max_traded_codes_per_day=e['max_traded_codes_per_day'],
        min_average_exposure=pref['min_average_exposure'],min_trade_dates=pref['min_trade_dates'],min_conclusion_days=252))


def selection_rank(row,cfg):
    m=row['metrics'];pref=cfg['research_preferences']
    active=row['average_exposure']>=pref['min_average_exposure'] and row['trade_dates']>=pref['min_trade_dates']
    return (active,m['annual_return']>0,m['max_drawdown']>=pref['drawdown_reference'],
        m['annual_return']/max(abs(m['max_drawdown']),.01),m['sharpe'],-row['annual_one_way_turnover'])


def ledger(cfg,rd,route,key,offense,defense,mode,cadence,period,phase,stress,
           panels,mask,states,identity,folder,results):
    name=f'{route}__{key}__{mode}__R{cadence}__{phase}'+('_stress' if stress else '')
    file=folder/'runs'/(name+'.json')
    stamp=canonical_hash(dict(identity=identity,round=rd['id'],run=name,period=period))
    if file.exists():
        value=json.loads(file.read_text(encoding='utf-8'))
        if value['identity']!=stamp: raise ValueError('Ledger identity mismatch '+name)
        row={k:v for k,v in value.items() if k not in ('dates','nav','trades','daily_account','rejected_orders','final_positions')}
        results.append(row);return row
    score,gate=timed_score(offense,defense,states,mode)
    columns=list(mask.columns)
    view={k:v[columns] for k,v in panels.items() if isinstance(v,pd.DataFrame)}
    view['_selection_policy']=cfg['selection_policy']
    if gate is not None:
        view['_timing']=pd.DataFrame(np.broadcast_to(gate.to_numpy()[:,None],score.shape),index=score.index,columns=columns)
    p=dict(cfg['execution'],**period,rebalance_days=cadence,timing_rule=mode)
    if stress:p.update(cfg['stress'])
    started=time.monotonic();sim=runner.simulate(view,score,p)
    row=dict(id=name,identity=stamp,round_id=rd['id'],route=route,label=cfg['routes'][route]['label'],
        candidate=key,timing=mode,cadence=cadence,phase=phase,period=period,stress=stress,
        params=p,scope=cfg['routes'][route]['scope'],archived=True,
        elapsed_seconds=round(time.monotonic()-started,2),**assess(sim,p,assessment_config(cfg)))
    # Preference gates and engineering invariants have different meanings.
    row['research_reference_pass']=row.pop('constraints_pass')
    row['execution_invariants_pass']=bool(row['checks']['frequency'] and row['checks']['holdings'] and row['checks']['traded_codes'])
    row['metrics']['calmar']=row['metrics']['annual_return']/abs(row['metrics']['max_drawdown']) if row['metrics']['max_drawdown']<0 else 0
    row['annual_return_reference_met']=bool(row['metrics']['annual_return']>=cfg['research_preferences']['annual_return_reference'])
    write_json(file,dict(**row,dates=list(sim['nav_series'].index.strftime('%Y-%m-%d')),nav=list(sim['nav_series'].values),
        trades=sim['trades'],daily_account=sim['daily_account'],rejected_orders=sim['rejections'],final_positions=sim['position_details']))
    if not row['execution_invariants_pass']:raise AssertionError('Execution invariant failure '+name)
    results.append(row)
    log(f"{rd['id']} {name}: annual={row['metrics']['annual_return']:.2%} dd={row['metrics']['max_drawdown']:.2%} turnover={row['annual_one_way_turnover']:.2f}")
    return row


def model_scores(cfg,rd,route,model_name,stage,features,names,mask,panels,folder):
    stem=f'{route}__{model_name}__{stage}'
    path=folder/'cache'/(stem+'.npy');audit_path=folder/(stem+'_fit.json')
    if path.exists() and audit_path.exists():
        return pd.DataFrame(np.load(path,mmap_mode='r'),index=mask.index,columns=mask.columns),json.loads(audit_path.read_text(encoding='utf-8'))
    end=rd['inner_training_end'] if stage=='inner' else rd['discovery']['end']
    start=rd['selection']['start'] if stage=='inner' else rd['validation']['start']
    target=label_panel(panels['open'][list(mask.columns)],mask,cfg['ml']['horizon'])
    prep,x,y,audit=fit_preprocessing(features,names,mask,mask.index,rd['discovery']['start'],end,
        cfg['preprocessing'],cfg['ml']['horizon'],cfg['ml']['sample_every_days'],target)
    if len(y)<cfg['ml']['min_training_rows']:raise ValueError('Insufficient training labels')
    model=make_model(model_name,cfg['models'][model_name],cfg['preprocessing']['seed'])
    log('FIT '+rd['id']+' '+stem+f' rows={len(y)} features={x.shape[1]}')
    with threadpool_limits(limits=2):
        model.fit(x,y)
        score=predict_frozen(model,prep,features,names,mask,start)
    # Signal on preceding close is necessary to trade at the first validation open.
    prev=np.flatnonzero(mask.index<start)
    if len(prev):
        first=mask.index[prev[-1]]
        with threadpool_limits(limits=2):
            prior=predict_frozen(model,prep,features,names,mask.loc[:first],first)
        score.loc[first]=prior.iloc[-1]
    np.save(path,score.to_numpy(dtype=np.float32))
    audit.update(stage=stage,model=model_name,model_params=cfg['models'][model_name],
        prediction_start=str(score.index[score.notna().any(axis=1)][0]),
        prediction_end=str(score.index[-1]),fit_end=end,validation_refit=False,feature_names=names)
    joblib.dump(dict(model=model,transformer=prep,names=names,fit_end=end),folder/'cache'/(stem+'.joblib'))
    write_json(audit_path,audit)
    return score,audit


def route_round(cfg,rd,route,panels,masks,ranks,states,identity,folder,results):
    spec=cfg['routes'][route];mask=masks[spec['scope']];codes=list(mask.columns)
    available={n:v[codes] for n,v in ranks.items() if set(codes)<=set(v.columns)}
    fit_audits={};candidates={};failures=[]
    if spec.get('models'):
        defensive_weights=cfg['ml']['defensive_weights']
        _,_,_,defensive_audit=fit_preprocessing(available,list(defensive_weights),mask,mask.index,
            rd['discovery']['start'],rd['inner_training_end'],cfg['preprocessing'])
        defensive_score,effective_defensive=blended(available,defensive_weights,defensive_audit['selected_features'],mask)
        write_json(folder/(route+'_defensive_features.json'),dict(audit=defensive_audit,weights=effective_defensive))
        names=list(cfg['technical_features'])
        if spec.get('graph'):names+=['graph_'+n for n in cfg['graph']['features']]
        for name in spec['models']:
            score,audit=model_scores(cfg,rd,route,name,'inner',available,names,mask,panels,folder)
            fit_audits[name]=audit;candidates[name]=(score,defensive_score)
        cadences=cfg['ml']['candidate_cadences']
        selection_period=rd['selection']
    else:
        cadences=cfg['cadences'];selection_period=rd['discovery']
        for key in spec['blends']:
            blend=cfg['blends'][key];names=list(dict.fromkeys([*blend['offensive'],*blend['defensive']]))
            try:
                prep,_,_,audit=fit_preprocessing(available,names,mask,mask.index,rd['discovery']['start'],rd['inner_training_end'],cfg['preprocessing'])
                selected=audit.get('selected_features',names)
                offensive,ow=blended(available,blend['offensive'],selected,mask)
                defensive,dw=blended(available,blend['defensive'],selected,mask)
                audit.update(offensive_weights=ow,defensive_weights=dw,
                    pca_used_for_return_prediction=False,fit_uses_only_inner_discovery=True)
                candidates[key]=(offensive,defensive);fit_audits[key]=audit
            except ValueError as exc:failures.append(dict(candidate=key,error=str(exc)))
        write_json(folder/(route+'_features.json'),dict(audits=fit_audits,failures=failures))
    if not candidates:raise ValueError('No usable candidates in '+route)
    # One route has at most 18 execution/model combinations, not a giant joint grid.
    if len(candidates)*len(cadences)*len(cfg['timing_modes'])>20:raise ValueError('Route search exceeds bounded 20-combination budget')
    training=[]
    for key,(offense,defense) in candidates.items():
        for mode in cfg['timing_modes']:
            for cadence in cadences:
                training.append(ledger(cfg,rd,route,key,offense,defense,mode,cadence,selection_period,
                    'discovery_selection',False,panels,mask,states,identity,folder,results))
    winner=max(training,key=lambda r:selection_rank(r,cfg))
    choice=dict(candidate=winner['candidate'],timing=winner['timing'],cadence=winner['cadence'])
    # Freeze the best execution variant for each candidate as well as the route winner.
    # All such challengers get the same validation replay, exposing selection instability.
    challengers=[max([r for r in training if r['candidate']==key],key=lambda r:selection_rank(r,cfg)) for key in candidates]
    freeze=folder/(route+'_frozen.json')
    contract=dict(identity=identity,round=rd,route=route,selection_period=selection_period,choice=choice,
        selected_discovery_ledger=winner['id'],challengers=[dict(candidate=r['candidate'],timing=r['timing'],cadence=r['cadence']) for r in challengers],
        candidates_evaluated=len(training),selection_uses_validation=False,validation_is_fresh_blind=False,
        fit_audits=fit_audits,failures=failures,
        ranking=[dict(candidate=r['candidate'],timing=r['timing'],cadence=r['cadence'],annual_return=r['metrics']['annual_return'],
            max_drawdown=r['metrics']['max_drawdown'],turnover=r['annual_one_way_turnover']) for r in sorted(training,key=lambda r:selection_rank(r,cfg),reverse=True)])
    if freeze.exists():
        old=json.loads(freeze.read_text(encoding='utf-8'))
        if old!=contract:raise ValueError('Frozen contract changed '+route)
    else:write_json(freeze,contract)
    frozen_hash=hashlib.sha256(freeze.read_bytes()).hexdigest()
    del candidates;gc.collect()
    chosen_scores={}
    def final_score(key):
        if key not in chosen_scores:
            if spec.get('models'):
                score,audit=model_scores(cfg,rd,route,key,'final',available,names,mask,panels,folder)
                chosen_scores[key]=(score,defensive_score)
            else:
                b=cfg['blends'][key];selected=fit_audits[key]['selected_features']
                chosen_scores[key]=(blended(available,b['offensive'],selected,mask)[0],blended(available,b['defensive'],selected,mask)[0])
        return chosen_scores[key]
    # Challengers cannot alter the already-written route winner or its parameters.
    for r in challengers:
        off,defense=final_score(r['candidate'])
        for stress in (False,True):
            ledger(cfg,rd,route,r['candidate'],off,defense,r['timing'],r['cadence'],rd['validation'],
                'validation' if r['candidate']==choice['candidate'] else 'validation_challenger',stress,
                panels,mask,states,identity,folder,results)
    off,defense=final_score(choice['candidate'])
    continuous=dict(start=rd['validation']['start'],end=cfg['observation']['end'])
    for phase,period in [('continuous_forward',continuous),('observation',cfg['observation'])]:
        for stress in (False,True):
            ledger(cfg,rd,route,choice['candidate'],off,defense,choice['timing'],choice['cadence'],period,
                phase,stress,panels,mask,states,identity,folder,results)
    # Fixed technical models may also be inspected over the full historical path.
    if not spec.get('models'):
        for stress in (False,True):
            ledger(cfg,rd,route,choice['candidate'],off,defense,choice['timing'],choice['cadence'],
                dict(start=rd['discovery']['start'],end=cfg['observation']['end']),'overall_including_discovery',stress,
                panels,mask,states,identity,folder,results)
    assert frozen_hash==hashlib.sha256(freeze.read_bytes()).hexdigest()
    return dict(route=route,round_id=rd['id'],choice=choice,frozen_sha256=frozen_hash)


def deliver(cfg,out,results,contracts):
    records=[dict(**{k:r[k] for k in ('id','round_id','route','candidate','timing','cadence','phase','stress','scope')},
        **r['metrics'],turnover=r['annual_one_way_turnover'],exposure=r['average_exposure'],
        max_holdings=r['max_holdings'],max_traded_codes=r['max_traded_codes'],
        trade_count=r['trade_count'],minimum_regular_after_any_gap=r['minimum_regular_after_any_gap'],
        research_reference_pass=r['research_reference_pass']) for r in results]
    pd.DataFrame(records).to_csv(out/'results.csv',index=False,encoding='utf-8-sig')
    write_json(out/'results.json',dict(config=cfg,contracts=contracts,records=results,
        validation_is_fresh_blind=False,selection_uses_validation=False))
    audit=json.loads((out/'data_audit.json').read_text(encoding='utf-8'))
    lines=['# 最多10只股票的多路线策略研究','',
        f"研究日期：{cfg['as_of']}；价格截止：2026-10-23；完整主板快照 {audit['codes']} 只。",
        '所有验证年份已被早前研究查看，本报告属于时间隔离历史回放，不能称为新盲测。','',
        '## 数据与执行','',
        '- 已废弃的10日换手率波动单因子不参与候选、选股、择时和模型特征。',
        '- 最多10只持仓、单只目标权重10%；普通交易最少间隔5个交易日；强风险允许次日开盘紧急清仓。',
        '- 周频和月频比较；最多每日交易20个不同代码，允许卖10只再买10只，持仓始终不超过10只。',
        '- 佣金3bp、单笔最低5元、卖出税10bp、滑点10bp；压力成本为佣金5bp、滑点30bp。税率是保守固定历史假设。',
        '- T+1开盘、停牌和一字板筛查，使用平台真实资金份额账本；一字板筛查依赖成交日完整日线，属于保守事后筛查。',
        '- 30%回撤、20倍年单边换手为研究排序参考，非用户确认的新硬门槛。20%年化仅报告是否达到，不强行倒推仓位。',
        '- 财报库最早可用日期2026-10-27，未参与历史回测。估值路线使用512只固定样本的历史PE/PB/PCF/PS，PB/PE仅为盈利能力近似，非已审计历史ROE。',
        '- 同池技术、同池无图ML与时空图路线使用相同每日资格遮罩。不得将不同股票池的收益差解释为模型收益。',
        '- 当前股票池存在幸存者偏差，历史ST/退市不完整；估值原始财报修订版本未核验；不构成实盘验收。','',
        '## 发现与冻结','',
        '第一轮发现2019—2022，机器学习内层训练至2021年末，2022年选择，冻结后用发现期全量训练并回放2023—2024。',
        '第二轮扩展发现2019—2024，机器学习内层训练至2023年末，2024年选择，冻结后用发现期全量训练并回放2025。',
        '验证、后续连续持仓和2026观察均不重训；标签终点不得越过训练截止。因子去相关、标准化和PCA只拟合训练数据。',
        '手工多因子也只在各轮内部训练段拟合相关性筛选；PCA只作信息保留审计，手工收益分数按剩余独立因子加权。',
        '图模型为120日历史相关图上的时间特征传播，再接预测模型；不宣称深度时空神经网络。','']
    for rd in cfg['rounds']:
        lines += [f"## {rd['id']}：验证 {rd['validation']['start']} 至 {rd['validation']['end']}",'',
            '|路线|发现期冻结候选|择时/调仓|年化|相对沪深300年化差|回撤|单边年换手|压力年化/回撤|持仓上限|',
            '|---|---|---|---:|---:|---:|---:|---:|---:|']
        for route in cfg['routes']:
            rows=[r for r in results if r['round_id']==rd['id'] and r['route']==route and r['phase']=='validation']
            base=next(r for r in rows if not r['stress']);stress=next(r for r in rows if r['stress']);m=base['metrics']
            lines.append(f"|{base['label']}|{base['candidate']}|{base['timing']}/{base['cadence']}日|{m['annual_return']:.2%}|{m['annual_excess_return']*100:+.2f}pp|{m['max_drawdown']:.2%}|{base['annual_one_way_turnover']:.2f}|{stress['metrics']['annual_return']:.2%}/{stress['metrics']['max_drawdown']:.2%}|{base['max_holdings']}|")
        lines+=['','### 连续持仓至2026-10-23（不在跨年时重置）','',
            '|路线|年化|回撤|单边年换手|平均仓位|总成交笔数|普通成交最小间隔|',
            '|---|---:|---:|---:|---:|---:|---:|']
        for r in results:
            if r['round_id']==rd['id'] and r['phase']=='continuous_forward' and not r['stress']:
                lines.append(f"|{r['label']}|{r['metrics']['annual_return']:.2%}|{r['metrics']['max_drawdown']:.2%}|{r['annual_one_way_turnover']:.2f}|{r['average_exposure']:.2%}|{r['trade_count']}|{r['minimum_regular_after_any_gap']}|")
        lines+=['']
    lines+=['## 解释边界与复现','',
        '收益差为策略年化减沪深300价格指数年化（百分点），未计指数分红，不能称严格全收益净超额。',
        '2026不足一年，观察期表格的年化仅为描述统计；2025不足252交易日，单年结果不升级正式正向结论。',
        '全部发现组合、冻结候选、落选挑战者、压力成本和连续路径均保留，不能按验证表现回头更换本轮赢家。',
        '比较单一随机种子，不提供统计显著性结论；跨年度稳定性优先于某一段最高收益。',
        '运行 `.venv/Scripts/python.exe scripts/multiroute_strategy_research.py`；审计 `validation/audit_multiroute_ledgers.py`。',
        '详见 results.csv、各轮 *_frozen.json、*_fit.json、runs/ 账本以及 independent_ledger_audit.json。','']
    (out/'多路线策略研究.md').write_text('\n'.join(lines),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='multiroute_strategy_research')
    parser.add_argument('--stage',choices=['all','prepare','research','deliver'],default='all');args=parser.parse_args()
    cfg=read_config(args.config);base=read_config(cfg['base_config']);out=ROOT/cfg['output_dir'];out.mkdir(parents=True,exist_ok=True)
    if args.stage=='deliver':
        old=json.loads((out/'results.json').read_text(encoding='utf-8'));deliver(cfg,out,old['records'],old['contracts']);return
    panels,masks,ranks,states,signature,identity=prepare(cfg,base,out)
    if args.stage=='prepare':return
    results=[];contracts=[]
    for rd in cfg['rounds']:
        folder=out/rd['id'];(folder/'runs').mkdir(parents=True,exist_ok=True);(folder/'cache').mkdir(exist_ok=True)
        for route in cfg['routes']:
            log('BEGIN '+rd['id']+' '+route)
            contracts.append(route_round(cfg,rd,route,panels,masks,ranks,states,identity,folder,results))
            write_json(out/'partial_results.json',dict(identity=identity,records=results,contracts=contracts))
            gc.collect()
    deliver(cfg,out,results,contracts)
    log('COMPLETE '+str(out/'多路线策略研究.md'))


if __name__=='__main__':main()
