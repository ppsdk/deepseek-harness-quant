"""Run a bounded, fully disclosed classical-rule study on frozen local history."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backtest import research_runner as runner
from backtest.research_rule_strategies import components,fundamental_mask,signals,market_gate
from data.research_valuation import panels as valuation_panels,coverage as valuation_coverage,settings as valuation_settings
from data.research_fundamentals import coverage as financial_coverage
from data.cache import DailyCache
from factors.research_library import read_config
from scripts.mainboard_repair_research import load_cached_snapshot,canonical_hash
from scripts.mainboard_strategy_research import write_json,log,benchmark_snapshot
from scripts.analyse_matched_ml_risk import risk_statistics

SOURCES=['scripts/technical_rule_research.py','backtest/research_rule_strategies.py',
    'backtest/research_portfolio.py','backtest/research_runner.py','factors/research_indicators.py',
    'data/research_valuation.py','scripts/analyse_matched_ml_risk.py']


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))


def assess(sim,p,cfg):
    m=risk_statistics(sim['returns'],.02)
    # Use the platform's conventional names; supplementary variants stay named.
    m['final_nav']=float(sim['nav_series'].iloc[-1])
    m['win_rate']=float((sim['returns']>0).mean())
    a=pd.DataFrame(sim['daily_account']);trades=pd.DataFrame(sim['trades'])
    days=sorted(trades.date.unique()) if len(trades) else []
    position={d:i for i,d in enumerate(a.date)}
    normal=set(trades.loc[trades.trigger.ne('market_risk_exit'),'date']) if len(trades) else set()
    gaps=[position[b]-position[x] for x,b in zip(days,days[1:]) if b in normal]
    years=(pd.Timestamp(a.date.iloc[-1])-pd.Timestamp(a.date.iloc[0])).days/365.25
    exposure=float(a.exposure.mean());limits=cfg['acceptance']
    activity=exposure>=limits['min_average_exposure'] and len(days)>=limits['min_trade_dates']
    risk_bands={str(int(b*100)):bool(m['max_drawdown']>=-b-1e-12) for b in limits['drawdown_bands']}
    freq=all(v>=p['min_trade_interval_days'] for v in gaps)
    return dict(metrics=m,annual_one_way_turnover=float(trades.amount.sum()/a.equity.mean()/years/2) if len(trades) else 0.,
        average_exposure=exposure,trade_dates=len(days),ordinary_trade_dates=len(normal),emergency_trade_dates=len(set(days)-normal),
        trade_count=len(trades),max_holdings=int(a.holding_count.max()),minimum_ordinary_gap=min(gaps) if gaps else None,
        frequency_pass=freq,activity_pass=bool(activity),return_pass=bool(m['annual_return']>=limits['min_annual_return']),
        risk_bands=risk_bands,candidate_pass=bool(activity and freq and m['annual_return']>=limits['min_annual_return'] and risk_bands[str(int(max(limits['drawdown_bands'])*100))]),
        costs={k:float(trades[k].sum()) if len(trades) else 0. for k in ['commission','stamp_tax','slippage_cost']},
        yearly=[dict(year=int(y),**risk_statistics(group,.02)) for y,group in sim['returns'].groupby(sim['returns'].index.year)],
        stale_valuation_days=sim['stale_valuation_days'])


def describe(cfg,spec,variant):
    i=cfg['indicators'];s=cfg['screens'][variant['screen']];p=cfg['execution'];m=cfg['market']
    out=[cfg['design_note'],cfg['data_note'],
        f"入场基本面硬筛选：{s['label']}；阈值 {json.dumps(s['limits'],ensure_ascii=False)}。仅在新买入时检查，已持有股票不因估值波动自动卖出。",
        f"个股{spec['average'].upper()}({spec['fast']})高于{spec['average'].upper()}({spec['slow']})且收盘高于短均线，连续{i['trend_confirm']}日；收盘高于短均线的幅度不超过{i['max_price_vs_fast']:.0%}。"]
    if spec['kdj']:out.append(f"KDJ({i['kdj_period']},{i['kdj_smooth']},{i['kdj_smooth']})：近{i['kdj_cross_lookback']}日内在K≤{i['kdj_entry_max']}时金叉，当前K>D才允许进入。高位K≥{i['kdj_exit_min']}时死叉产生退出信号。")
    if spec['dmi']:out.append(f"DMI({i['dmi_period']})：+DI>−DI且ADX≥{i['adx_min']}才允许进入；+DI<−DI连续{i['exit_confirm']}日产生退出信号。")
    out.extend([f"均线死叉或收盘低于长均线连续{i['exit_confirm']}日退出。8%成本止损、12%收盘高点回撤止损；没有固定止盈。止损不是成交价保证，普通退出受5交易日间隔约束。",
        f"市场门控：沪深300收盘>MA{m['slow']}且MA{m['fast']}>MA{m['slow']}，连续{m['confirm_days']}日允许持仓，否则下一开盘紧急退出。",
        f"最多{p['topn']}只；新仓目标为账户权益的{variant['exposure']/p['topn']:.1f}%，买入时总股票仓位预算{variant['exposure']}%。存量仓位随价格漂移，不每天再平衡；未用名额留现金。",
        cfg['execution_note'],cfg['cost_note']])
    return out


def archive(row,raw,cfg,codes,signature,identity):
    from backtest.research_report import build_report
    from backtest.research_runtime import snapshot
    name='research_rules_'+canonical_hash(dict(study=identity,run=row['id']))[:32]
    benchmark=benchmark_snapshot(cfg['benchmark']['code'],tuple(raw['dates']))
    nav=np.asarray(raw['nav']);ret=nav/np.r_[1.,nav[:-1]]-1
    params=dict(raw['params'],strategy=row['strategy'],stocks=len(codes),codes=','.join(codes),indicator_params={})
    spec=cfg['strategies'][row['strategy']];variant=cfg['variants'][row['variant']]
    description=describe(cfg,spec,variant)
    realized={};basis={}
    for t in raw['trades']:
        c=t['code']
        if t['side']=='buy':basis[c]=(t['position_before']*basis.get(c,0)+t['amount']+t['commission'])/t['position_after']
        else:realized[c]=realized.get(c,0)+t['amount']-t['commission']-t['stamp_tax']-t['quantity']*basis[c]
    r=dict(ok=True,engine='research_ledger_v1',name=name,run_id=name.rsplit('_',1)[-1],title=row['label'],category=cfg['title'],
        strategy=row['strategy'],factors=[],strategy_spec=dict(kind='classical_rules',rules=spec,screen=cfg['screens'][variant['screen']],description=description),
        generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),metrics=row['metrics'],params=params,
        verdict='历史门槛通过' if row['candidate_pass'] else '探索观察',costs=dict(row['costs'],total=sum(row['costs'].values())),
        dates=raw['dates'],nav=raw['nav'],bench_nav=benchmark['nav'],excess_nav=(nav/np.asarray(benchmark['nav'])).tolist(),
        excess_annual=row['metrics']['annual_return']-benchmark['metrics']['annual_return'],
        benchmark=dict(cfg['benchmark'],series_labels={'bench_nav':cfg['benchmark']['label'],'excess_nav':'策略净值 ÷ 沪深300价格指数净值'},nav_mode_label='净值与沪深300',relative_mode_label='策略相对沪深300'),
        returns=[dict(date=d,ret=float(v)) for d,v in zip(raw['dates'],ret)],data=dict(selected_codes=codes,selection='dated_valuation_sample',price_adjustment='qfq',snapshot_hash=signature),
        trades=raw['trades'],rejections=raw['rejected_orders'],daily_account=raw['daily_account'],final_cash=raw['daily_account'][-1]['cash'],
        final_positions={v['code']:v['quantity'] for v in raw['final_positions']},position_details=raw['final_positions'],realized_pnl=realized,
        stale_valuation_days=row['stale_valuation_days'],research_audit=dict(source_ledger=row['ledger'],formal_acceptance=False,validation_is_fresh_blind=False),
        warnings=description,archived=True,elapsed_s=row['elapsed_seconds'],provenance=snapshot())
    r['report']=build_report(r);runner.save_result(r)
    return dict(run=name,url='/backtests?run='+name,label=row['label'],strategy=row['strategy'],variant=row['variant'],
        annual_return=row['metrics']['annual_return'],max_drawdown=row['metrics']['max_drawdown'],sharpe=row['metrics']['sharpe'],
        turnover=row['annual_one_way_turnover'],exposure=row['average_exposure'],trade_dates=row['trade_dates'],
        return_pass=row['return_pass'],candidate_pass=row['candidate_pass'],risk_bands=row['risk_bands'],source_ledger=row['ledger'])


def main():
    cfg=read_config('technical_rule_research');base=read_config(cfg['base_config']);out=ROOT/cfg['output_dir'];out.mkdir(parents=True,exist_ok=True)
    for d in ['runs','cache','trade_replay']: (out/d).mkdir(exist_ok=True)
    panels,eligible,signature,data_audit=load_cached_snapshot(base)
    val_cfg=read_config(cfg['valuation_config']);universe_path=ROOT/val_cfg['output_dir']/'valuation_universe.json'
    sample=set(read(universe_path)['codes']);codes=[c for c in eligible.columns if c in sample]
    panels={k:v[codes] for k,v in panels.items()};eligible=eligible[codes]
    valuation_path,_=valuation_settings();valuation_hash=digest(valuation_path)
    source_hashes={n:digest(ROOT/n) for n in SOURCES}
    identity=canonical_hash(dict(config=cfg,snapshot_hash=signature,valuation_hash=valuation_hash,sources=source_hashes))
    manifest=dict(identity=identity,config=cfg,snapshot_hash=signature,valuation_sha256=valuation_hash,
        sources=source_hashes,stock_codes=codes,universe_sha256=digest(universe_path),evaluation_mode='exploratory_full_period',
        fixed_grid=True,model_fitting=False,factor_scoring=False,blind_validation=False)
    mf=out/'experiment_manifest.json'
    if mf.exists():assert read(mf)==manifest,'Experiment sources changed; preserve previous evidence and use a new output directory'
    else:write_json(mf,manifest)
    wanted=sorted({f for s in cfg['screens'].values() for f in s['limits']})
    values=valuation_panels(eligible.index,codes,wanted)
    write_json(out/'data_audit.json',dict(data_audit,valuation=valuation_coverage(),financial=financial_coverage(),
        sample_stocks=len(codes),price_start=str(panels['close'].dropna(how='all').index[0]),
        exact_date_joins=True,current_financial_data_used_in_history=False,financial_vintage_verified=False))
    for n,v in values.items():np.save(out/'cache'/(n+'.npy'),v.to_numpy())
    common=components(panels,cfg['indicators'])
    for n,v in common.items():np.save(out/'cache'/(n+'.npy'),v.to_numpy())
    masks={n:fundamental_mask(values,eligible,s) for n,s in cfg['screens'].items()}
    index_frame=DailyCache().get_daily(cfg['benchmark']['code'],start=str(eligible.index[0]),end=cfg['period']['end'],adjust='none').set_index('date').reindex(eligible.index)
    gate,index_values=market_gate(index_frame,cfg['market'])
    market=pd.DataFrame(dict(**index_values,gate=gate));market.to_csv(out/'market.csv',index_label='date')
    market_panel=pd.DataFrame({c:gate for c in codes})
    write_json(out/'signal_manifest.json',dict(dates=list(eligible.index),codes=codes,screen_counts={n:{str(y):float(v.loc[v.index.str.startswith(str(y))].sum(axis=1).mean()) for y in range(2020,2027)} for n,v in masks.items()}))
    rows=[];raws={};entries=[]
    def run(key,vid,stress=False,queue='liquidity',timed=True,diagnostic=False):
        spec=cfg['strategies'][key];variant=cfg['variants'][vid]
        rid=key+'__'+vid+('__stress' if stress else '')+('__'+queue if queue!='liquidity' else '')+('__no_market_gate' if not timed else '')
        path=out/'runs'/(rid+'.json')
        if path.exists():
            raw=read(path);assert raw['experiment_identity']==identity
            row=raw['summary'];raws[rid]=raw;rows.append(row);return row,raw
        start=time.monotonic()
        priority,technical,averages=signals(panels,common,eligible,masks[variant['screen']],spec,cfg['indicators'],queue)
        p=dict(cfg['execution'],**cfg['period']);p['target_exposure_pct']=variant['exposure']
        if stress:p.update(cfg['stress'])
        view=dict(panels,**technical)
        if timed:view['_timing']=market_panel;priority=priority.where(gate.eq(1),axis=0)
        else:p['timing_rule']='none'
        sim=runner.simulate(view,priority,p)
        row=dict(id=rid,strategy=key,variant=vid,label=spec['label']+' · '+variant['label'],stress=stress,diagnostic=diagnostic,
            queue=queue,market_gate=timed,params=p,ledger=str(path.relative_to(ROOT)),elapsed_seconds=time.monotonic()-start,**assess(sim,p,cfg))
        raw=dict(experiment_identity=identity,summary=row,params=p,dates=[str(d.date()) for d in sim['nav_series'].index],nav=list(sim['nav_series']),
            trades=sim['trades'],daily_account=sim['daily_account'],rejected_orders=sim['rejections'],final_positions=sim['position_details'])
        write_json(path,raw);raws[rid]=raw;rows.append(row)
        log(f"RULE {rid}: CAGR={row['metrics']['annual_return']:.2%} DD={row['metrics']['max_drawdown']:.2%} Sharpe={row['metrics']['sharpe']:.2f} turnover={row['annual_one_way_turnover']:.2f} pass={row['candidate_pass']}")
        return row,raw
    pairs=[]
    for key in cfg['strategies']:
        for vid in cfg['variants']:
            b,raw=run(key,vid);s,_=run(key,vid,True)
            pairs.append(dict(base=b,stress=s))
            entry=archive(b,raw,cfg,codes,signature,identity)
            entry.update(stress_annual_return=s['metrics']['annual_return'],stress_max_drawdown=s['metrics']['max_drawdown'])
            entries.append(entry)
    # A bounded diagnostic subset is explicitly post-selection; it does not
    # become another hidden search and all diagnostic returns are reported.
    ordered=sorted(pairs,key=lambda p:(not p['base']['candidate_pass'],not p['stress']['candidate_pass'],abs(p['base']['metrics']['max_drawdown']),-p['base']['metrics']['annual_return']))
    representatives=[];seen=set()
    for pair in ordered:
        b=pair['base']
        if b['strategy'] in seen:continue
        representatives.append(pair);seen.add(b['strategy'])
        if len(representatives)>=cfg['diagnostics']['representative_count']:break
    diagnostics=[]
    for pair in representatives:
        b=pair['base']
        a,_=run(b['strategy'],b['variant'],queue=cfg['diagnostics']['alternative_queue'],diagnostic=True);diagnostics.append(a)
        if cfg['diagnostics']['gate_ablation']:
            a,_=run(b['strategy'],b['variant'],timed=False,diagnostic=True);diagnostics.append(a)
    assert digest(valuation_path)==valuation_hash,'Historical valuation DB changed during study'
    assert all(digest(ROOT/n)==h for n,h in source_hashes.items())
    write_json(out/'results.json',dict(manifest=manifest,records=rows,pairs=pairs,diagnostics=diagnostics))
    preferred={p['base']['id'] for p in representatives}
    for e in entries:e['representative']=any(b['base']['id'] in preferred and b['base']['strategy']==e['strategy'] and b['base']['variant']==e['variant'] for b in representatives)
    catalog=dict(ok=True,state='complete',title=cfg['title'],note=cfg['design_note'],data_note=cfg['data_note'],data_end=cfg['period']['end'],
        start=raws[rows[0]['id']]['dates'][0],ledger_count=len(rows),entries=entries,acceptance=cfg['acceptance'])
    write_json(out/'platform_catalog.json',catalog)
    write_json(out/'platform_archives.json',entries)
    from validation.audit_technical_rule_ledgers import audit
    audited=audit(cfg)
    lines=['# '+cfg['title'],'',f"研究日期：{cfg['as_of']}；回放{catalog['start']}至{cfg['period']['end']}，股票池{len(codes)}只。",'',cfg['design_note'],cfg['data_note'],cfg['execution_note'],cfg['cost_note'],'',
        '## 全部规则组合与成本压力','', '|策略|筛选与仓位|年化|回撤|夏普 rf=0|单边年换手|平均仓位|压力年化|压力回撤|5%与20%回撤门槛|',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for pair in ordered:
        b,s=pair['base'],pair['stress'];m=b['metrics'];sm=s['metrics']
        lines.append(f"|{cfg['strategies'][b['strategy']]['label']}|{cfg['variants'][b['variant']]['label']}|{m['annual_return']:.2%}|{m['max_drawdown']:.2%}|{m['sharpe']:.2f}|{b['annual_one_way_turnover']:.2f}|{b['average_exposure']:.2%}|{sm['annual_return']:.2%}|{sm['max_drawdown']:.2%}|{'通过' if b['candidate_pass'] else '未通过'}|")
    lines+=['','门槛仅为历史筛查：年化至少5%、回撤不超过20%、平均仓位至少10%、至少12个成交日，且普通交易间隔满足要求；同时保留10%和15%回撤档位。排序优先满足基础及压力门槛，再比较基础回撤。','',
        '## 代表性规则的逐年表现与说明','']
    for pair in representatives:
        b=pair['base'];lines+=['### '+b['label'],'']
        lines.extend(v+'\n' for v in describe(cfg,cfg['strategies'][b['strategy']],cfg['variants'][b['variant']]))
        lines+=['|年份|实际收益|年内最大回撤|','|---|---:|---:|']
        for y in b['yearly']:lines.append(f"|{y['year']}|{y['total_return']:.2%}|{y['max_drawdown']:.2%}|")
        lines+=['','2026为截至10月23日的不足一年收益。年度分段保持跨年持仓，不在年末清仓。','']
    lines+=['## 队列敏感性与择时消融（事后诊断）','', '|对应规则|诊断|年化|回撤|','|---|---|---:|---:|']
    for d in diagnostics:lines.append(f"|{d['label']}|{'代码顺序替代流动性队列' if d['queue']!='liquidity' else '去除市场门控'}|{d['metrics']['annual_return']:.2%}|{d['metrics']['max_drawdown']:.2%}|")
    lines+=['','同一策略的成交额排队与代码排队结果可能差异很大：选股队列影响实际持仓，不能把效果全部归因于KDJ/DMI。基本面筛选对照必须在相同仓位、相同技术规则内比较。',
        '现金占比高可降低回撤，也会降低市场参与度。PIT财报尚缺，当前筛选只有估值和现金流倍数约束，不代表已验证财务质量。',
        '所有参数和规则由事后研究提出；完整区间被用于比较，不构成独立留出或未来盈利保证。','',
        f"独立账本审计：{audited['ledgers']}份、{audited['trades']}笔成交。复现：`.venv/Scripts/python.exe -X utf8 scripts/technical_rule_research.py`。",'']
    (out/'技术规则策略研究.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(ok=True,base_strategies=len(entries),ledgers=len(rows),audit=audited,report=str(out/'技术规则策略研究.md')),ensure_ascii=False))


if __name__=='__main__':main()
