"""Separate, training-calibrated exposure experiment after the parameter study."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from backtest import research_runner as runner
from backtest.research_factor_search import raw_parameter
from backtest.research_timing import evaluate
from data.cache import DailyCache
from scripts.factor_parameter_portfolios import assess,read_execution
from scripts.four_axis_factor_mining import valuation_panels
from scripts.mainboard_repair_research import load_cached_snapshot,canonical_hash
from scripts.mainboard_strategy_research import write_json,log


def calibrate(training,cfg,budget):
    if training['phase']!='discovery':raise ValueError('Exposure calibration requires a discovery ledger')
    dd=abs(training['metrics']['max_drawdown']);turn=training['annual_one_way_turnover'];limits=cfg['portfolio']
    scales=[budget['maximum_exposure_pct']/100]
    if dd:scales.append(abs(limits['max_drawdown'])*budget['drawdown_headroom']/dd)
    if turn:scales.append(limits['max_annual_one_way_turnover']*budget['turnover_headroom']/turn)
    percentage=math.floor(min(scales)*100/budget['exposure_step_pct'])*budget['exposure_step_pct']
    return dict(exposure_pct=max(budget['minimum_exposure_pct'],percentage),
                below_minimum=percentage<budget['minimum_exposure_pct'],unrounded_pct=min(scales)*100,
                calibration_phase=training['phase'],calibration_ledger_id=training['id'],uses_validation=False)


def main():
    budget=read_config('factor_parameter_budget');cfg=read_config(budget['source_config']);base=read_config(cfg['base_config'])
    source=ROOT/cfg['output_dir'];out=ROOT/budget['output_dir'];out.mkdir(parents=True,exist_ok=True);(out/'runs').mkdir(exist_ok=True)
    old=json.loads((source/'portfolio_results.json').read_text(encoding='utf-8'));policy=read_execution(cfg)
    source_manifest=json.loads((source/'experiment_manifest.json').read_text(encoding='utf-8'))
    panels,eligible,signature,_=load_cached_snapshot(base);values,codes=valuation_panels(panels,base)
    if old['snapshot_hash']!=signature:raise ValueError('Source study and exposure experiment use different snapshots')
    allrows=[];frozen=[]
    for rd in cfg['rounds']:
        contract=json.loads((source/rd['id']/'frozen_portfolios.json').read_text(encoding='utf-8'))
        for selected in contract['chosen']:
            training=next(r for r in old['records'] if r['round_id']==rd['id'] and r['id']==selected['discovery_ledger'])
            calibration=calibrate(training,cfg,budget)
            frozen.append(dict(round_id=rd['id'],family=selected['family'],selection=selected,calibration=calibration))
    write_json(out/'frozen_budget.json',dict(budget=budget,source_identity=source_manifest['identity'],snapshot_hash=signature,
        proposed_after_validation_observed=True,calibration_uses_validation=False,selection_uses_validation=False,records=frozen))
    for frozen_row in frozen:
        selected=frozen_row['selection'];row=selected['row'];cal=frozen_row['calibration'];rd=next(r for r in cfg['rounds'] if r['id']==frozen_row['round_id'])
        raw=raw_parameter(row,values if row['category']=='valuation' else panels);score=(raw*row['direction']).where(eligible[raw.columns])
        view={k:v.reindex(columns=raw.columns) for k,v in panels.items()}
        view['_selection_policy']={'retain_rank':int(math.ceil(cfg['portfolio']['primary_topn']*cfg['portfolio']['candidate_buffer']))}
        if selected['timing_rule']!='none':
            frame=DailyCache().get_daily(policy['execution']['timing_index'],adjust='none').set_index('date').sort_index()
            gate=evaluate(frame,selected['timing_rule'],dict(benchmark=policy['benchmark'],rules=policy['timing_rules'])).reindex(raw.index)
            view['_timing']=pd.DataFrame(np.broadcast_to(gate.to_numpy()[:,None],raw.shape),index=raw.index,columns=raw.columns);score=score.where(gate.eq(1),axis=0)
        periods=[('discovery',rd['discovery'],False),('validation',rd['validation'],False),('validation',rd['validation'],True),
                 ('continuous_forward',dict(start=rd['validation']['start'],end=cfg['observation']['end']),False),
                 ('continuous_forward',dict(start=rd['validation']['start'],end=cfg['observation']['end']),True)]
        for phase,period,stress in periods:
            ident=f"{rd['id']}_{row['id']}__E{cal['exposure_pct']}_{phase}"+('_stress' if stress else '')
            stamp=canonical_hash(dict(budget=budget,selected=selected,calibration=cal,phase=phase,period=period,stress=stress,snapshot=signature))
            file=out/'runs'/(ident+'.json')
            cached=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}
            if cached.get('identity')==stamp:
                allrows.append({k:v for k,v in cached.items() if k not in ('nav','dates','trades','daily_account','rejected_orders')});continue
            p={**training_params(source_rows=old['records'],selected=selected,rd=rd),**period,
                'target_exposure_pct':cal['exposure_pct']}
            if stress:p.update(cfg['stress'])
            sim=runner.simulate(dict(view),score,p)
            result=dict(id=ident,identity=stamp,round_id=rd['id'],family_id=row['family_id'],label=row['label'],parameter_id=row['id'],
                phase=phase,period=period,stress=stress,params=p,topn=p['topn'],target_exposure_pct=cal['exposure_pct'],
                proposed_after_validation_observed=True,calibration_uses_validation=False,archived=True,**assess(sim,p,cfg))
            write_json(file,dict(**result,dates=list(sim['nav_series'].index.strftime('%Y-%m-%d')),nav=list(sim['nav_series'].values),
                trades=sim['trades'],daily_account=sim['daily_account'],rejected_orders=sim['rejections']))
            allrows.append(result);m=result['metrics'];log(f"BUDGET {ident}: CAGR={m['annual_return']:.2%} DD={m['max_drawdown']:.2%} TURN={result['annual_one_way_turnover']:.2f}")
    write_json(out/'results.json',dict(snapshot_hash=signature,proposed_after_validation_observed=True,calibration_uses_validation=False,records=allrows))
    pd.DataFrame([dict(id=r['id'],round=r['round_id'],family=r['family_id'],phase=r['phase'],stress=r['stress'],exposure_pct=r['target_exposure_pct'],
        **r['metrics'],turnover=r['annual_one_way_turnover'],average_exposure=r['average_exposure'],constraints_pass=r['constraints_pass']) for r in allrows]).to_csv(out/'results.csv',encoding='utf-8-sig',index=False)
    lines=['# 发现期校准仓位的追加对照',
        '\n此方案在原参数研究验证结果已被观察之后提出，不是新盲测。对全部冻结组合统一应用同一规则；参数、因子、调仓和择时不再搜索。仓位只由原发现期账本的回撤和换手校准；校准结果在运行本实验验证账本之前写入frozen_budget.json。',
        f"\n仓位比例=min({budget['maximum_exposure_pct']}%, {budget['drawdown_headroom']}×回撤预算/训练回撤, {budget['turnover_headroom']}×换手预算/训练换手)，按{budget['exposure_step_pct']}个百分点向下取整；最低{budget['minimum_exposure_pct']}%，低于最低值会标记，不视为正常预算候选。线性缩放仅用于校准，不保证未来回撤，以下均为重新运行的真实账本。",
        '\n| 来源 / 因子 | 固定仓位上限 | 连续回放区间 | 年化 | 压力年化 | 最大回撤 | 压力回撤 | 单边年换手 | 实际平均仓位 | 联合研究门槛 |','|---|---:|---|---:|---:|---:|---:|---:|---:|---|']
    for r in allrows:
        if r['phase']!='continuous_forward' or r['stress']:continue
        s=next(x for x in allrows if x['round_id']==r['round_id'] and x['family_id']==r['family_id'] and x['phase']==r['phase'] and x['stress'])
        d=next(x for x in allrows if x['round_id']==r['round_id'] and x['family_id']==r['family_id'] and x['phase']=='discovery')
        below=next(x['calibration']['below_minimum'] for x in frozen if x['round_id']==r['round_id'] and x['family']==r['family_id'])
        passed=bool(d['constraints_pass'] and r['constraints_pass'] and s['constraints_pass'] and not below)
        lines.append(f"|{r['round_id']} / {r['label']}|{r['target_exposure_pct']}%|{r['period']['start']}—{r['period']['end']}|{r['metrics']['annual_return']:.2%}|{s['metrics']['annual_return']:.2%}|{r['metrics']['max_drawdown']:.2%}|{s['metrics']['max_drawdown']:.2%}|{r['annual_one_way_turnover']:.2f}|{r['average_exposure']:.1%}|{'通过' if passed else '未通过'}|")
    lines+=['\n降低仓位会同时降低收益，不能把现金带来的低回撤当作因子预测能力增强。研究门槛包含最大回撤10%、最多5只、普通交易至少5日间隔，以及本轮额外设置的单边年换手3倍、平均仓位10%、正收益与样本长度。',
        f"\n数据与信号口径：股票截至{base['data_end']}，收盘后按冻结因子方向排序，下一交易日开盘执行；先筛至少{base['universe']['min_history_days']}个已观察交易日、过去{base['universe']['liquidity_window']}日平均成交额不低于{base['universe']['min_average_amount']/10000:g}万元。每组最多{cfg['portfolio']['primary_topn']}只，仍在前ceil(N×{cfg['portfolio']['candidate_buffer']})名的持仓优先保留，调仓间隔沿用原发现期选择。",
        '\n原研究的幸存者/ST缺失、估值版本未核验、历史已被观察等限制全部保留。此追加方案需要未来数据继续检验，不能宣称未来回撤必定低于10%。',
        '\n账本未实现100股整手、盘口排队与成交量参与率约束；一字板过滤不等于完整模拟实盘成交。',
        '\n运行 `python scripts/factor_parameter_budget.py` 可复现；参数与账本分别保留在frozen_budget.json和runs/，原参数实验不覆盖。']
    (out/'仓位预算追加研究.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    sources=json.loads((source/'source_register.json').read_text(encoding='utf-8'))
    sources['sources'].append(dict(source_id='S_BUDGET',evidence_label='追加历史回放',title='仅发现期校准固定仓位对照',
        source_identity=source_manifest['identity'],proposed_after_validation_observed=True,calibration_uses_validation=False,path=str(out)))
    write_json(out/'source_register.json',sources)
    import hashlib
    write_json(out/'implementation_hashes.json',{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ['scripts/factor_parameter_budget.py','scripts/factor_parameter_portfolios.py','backtest/research_portfolio.py']})
    write_json(out/'factor_summaries.json',[dict(factor_key=next(f['selection']['row']['factor_key'] for f in frozen if f['round_id']==r['round_id'] and f['family']==r['family_id']),
        backtest_id=r['id'],verdict=r['verdict'],annual_return=r['metrics']['annual_return'],sharpe=r['metrics']['sharpe'],max_drawdown=r['metrics']['max_drawdown'],
        data_range=r['period'],notes='追加方案在已观察历史上提出；仅训练校准仓位；不保证未来回撤') for r in allrows if r['phase']=='continuous_forward' and not r['stress']])


def training_params(source_rows,selected,rd):
    return deepcopy(next(r['params'] for r in source_rows if r['round_id']==rd['id'] and r['id']==selected['discovery_ledger']))


if __name__=='__main__':main()
