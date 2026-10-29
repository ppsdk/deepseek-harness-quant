"""Post-freeze matched graph ablations and chronological interpretation; never select on them."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from backtest.research_multiroute import blended,predict_frozen
from scripts.multiroute_strategy_research import ledger
from scripts.mainboard_repair_research import load_cached_snapshot
from scripts.mainboard_strategy_research import write_json


def archive_continuous(cfg,base,out,results,masks):
    """Expose the actual saved ledgers through the existing platform report viewer."""
    from backtest import research_runner as runner
    from backtest.research_report import build_report
    from backtest.research_runtime import snapshot as runtime_snapshot
    from scripts.mainboard_strategy_research import benchmark_snapshot
    import time
    entries=[]
    for row in results:
        if row['phase']!='continuous_forward' or row['stress']:continue
        original=out/row['round_id']/'runs'/(row['id']+'.json')
        raw=json.loads(original.read_text(encoding='utf-8'))
        run_id=hashlib.sha256((row['identity']+'multiroute_platform').encode()).hexdigest()[:32]
        name='research_multiroute_'+run_id
        benchmark=benchmark_snapshot(row['params']['timing_index'],tuple(raw['dates']))
        nav=np.asarray(raw['nav']);returns=nav/np.r_[1.,nav[:-1]]-1
        costs={**row['costs'],'total':sum(row['costs'].values())}
        p=dict(row['params'],strategy='multiroute_'+row['route'],stocks=len(masks[row['scope']].columns),
            codes=','.join(masks[row['scope']].columns),indicator_params={})
        realized={};average={}
        for t in raw['trades']:
            c=t['code']
            if t['side']=='buy':average[c]=(t['position_before']*average.get(c,0)+t['amount']+t['commission'])/t['position_after']
            else:realized[c]=realized.get(c,0)+t['amount']-t['commission']-t['stamp_tax']-t['quantity']*average[c]
        recipe=json.loads((out/row['round_id']/(row['route']+'_frozen.json')).read_text(encoding='utf-8'))
        position=raw['final_positions']
        result=dict(ok=True,engine='research_ledger_v1',run_id=run_id,name=name,
            title='多路线 '+row['round_id']+' · '+row['label']+' · '+row['candidate'],category='多路线策略研究',
            strategy=p['strategy'],factors=[],strategy_spec=recipe,generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
            metrics=row['metrics'],params=p,verdict=row['verdict'],costs=costs,
            dates=raw['dates'],nav=raw['nav'],bench_nav=benchmark['nav'],
            excess_nav=(nav/np.asarray(benchmark['nav'])).tolist(),excess_annual=row['metrics']['annual_excess_return'],
            benchmark=dict(base['benchmark'],series_labels={'bench_nav':'沪深300价格指数','excess_nav':'策略净值 ÷ 沪深300价格指数净值'},
                nav_mode_label='净值与沪深300',relative_mode_label='策略相对沪深300'),
            returns=[dict(date=d,ret=float(v)) for d,v in zip(raw['dates'],returns)],
            data=dict(selected_codes=list(masks[row['scope']].columns),selection=row['scope'],price_adjustment='qfq',
                snapshot_hash=json.loads((out/'experiment_manifest.json').read_text(encoding='utf-8'))['snapshot_hash']),
            trades=raw['trades'],rejections=raw['rejected_orders'],daily_account=raw['daily_account'],
            final_cash=raw['daily_account'][-1]['cash'],final_positions={p['code']:p['quantity'] for p in position},
            position_details=position,realized_pnl=realized,stale_valuation_days=row['stale_valuation_days'],
            research_audit=dict(source_ledger=str(original.relative_to(ROOT)),selection_uses_validation=False,
                validation_is_fresh_blind=False,formal_acceptance=False,reproduction='python scripts/multiroute_strategy_research.py'),
            warnings=[cfg['design_note'],
                '数据截至2026-10-23，当前幸存者主板池；历史退市和ST不完整，估值财报版本未核验。',
                'T+1开盘和小数份额账本，含佣金、固定历史卖出税和滑点；一字板过滤使用当日完整日线，非真实开盘撮合。',
                '指数为未计分红的沪深300价格指数。研究参考门槛不代表实盘验收。',
                '本报告来自原始研究账本。专用股票池、归一化、模型冻结和相关图需运行研究脚本精确复现。'],
            archived=True,elapsed_s=row['elapsed_seconds'])
        result['provenance']=runtime_snapshot()
        result['report']=build_report(result);runner.save_result(result)
        assert result['report']['max_drawdown']['value']==row['metrics']['max_drawdown']
        entries.append(dict(round_id=row['round_id'],route=row['route'],name=name,
            url='/backtests?run='+name,archive_url=result['archive_url'],source_ledger=str(original.relative_to(ROOT))))
    write_json(out/'platform_archives.json',entries)
    return entries


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',default='multiroute_strategy_research');args=parser.parse_args()
    cfg=read_config(args.config);base=read_config(cfg['base_config']);out=ROOT/cfg['output_dir']
    all_results=json.loads((out/'results.json').read_text(encoding='utf-8'))
    manifest=json.loads((out/'experiment_manifest.json').read_text(encoding='utf-8'))
    panels,eligible,signature,_=load_cached_snapshot(base);assert signature==manifest['snapshot_hash']
    sample=json.loads((ROOT/read_config(cfg['valuation_config'])['output_dir']/'valuation_universe.json').read_text(encoding='utf-8'))['codes']
    codes=[c for c in eligible.columns if c in set(sample)];mask=eligible[codes].copy();ranks={}
    for name in cfg['features']:
        columns=codes if name in cfg['valuation_features'] else list(eligible.columns)
        ranks[name]=pd.DataFrame(np.load(out/'cache'/(name+'.npy'),mmap_mode='r'),index=eligible.index,columns=columns)[codes]
    for name in cfg['valuation_features']:mask &= ranks[name].notna()
    for name in cfg['graph']['features']:
        ranks['graph_'+name]=pd.DataFrame(np.load(out/'cache'/('graph_'+name+'.npy'),mmap_mode='r'),index=eligible.index,columns=codes)
    states=pd.read_csv(out/'market_states.csv',index_col=0);states.index=states.index.astype(str)
    ablations=[];graph_audits=[]
    for rd in cfg['rounds']:
        folder=out/rd['id'];freeze=folder/'graph_ml_frozen.json';contract=json.loads(freeze.read_text(encoding='utf-8'))
        before=hashlib.sha256(freeze.read_bytes()).hexdigest();choice=contract['choice'];model=choice['candidate']
        no_graph=pd.DataFrame(np.load(folder/'cache'/('matched_ml__'+model+'__final.npy'),mmap_mode='r'),index=mask.index,columns=codes)
        saved=joblib.load(folder/'cache'/('graph_ml__'+model+'__final.joblib'))
        fit=json.loads((folder/('graph_ml__'+model+'__final_fit.json')).read_text(encoding='utf-8'))
        graph_names=[n for n in fit['selected_features'] if n.startswith('graph_')]
        perm=np.random.default_rng(cfg['preprocessing']['seed']).permutation(len(codes))
        disrupted=dict(ranks)
        for name in [n for n in saved['names'] if n.startswith('graph_')]:
            disrupted[name]=pd.DataFrame(ranks[name].to_numpy()[:,perm],index=mask.index,columns=codes)
        first=mask.index[max(0,mask.index.searchsorted(rd['validation']['start'])-1)]
        with threadpool_limits(limits=2):
            shuffled=predict_frozen(saved['model'],saved['transformer'],disrupted,saved['names'],mask,first)
        defense_meta=json.loads((folder/'graph_ml_defensive_features.json').read_text(encoding='utf-8'))
        defensive,_=blended(ranks,defense_meta['weights'],defense_meta['audit']['selected_features'],mask)
        for key,score in [('matched_no_graph',no_graph),('shuffled_graph_inference',shuffled)]:
            for phase,period in [('graph_ablation_validation',rd['validation']),
                                 ('graph_ablation_continuous',dict(start=rd['validation']['start'],end=cfg['observation']['end']))]:
                for stress in (False,True):
                    ledger(cfg,rd,'graph_ml',key+'_'+model,score,defensive,choice['timing'],choice['cadence'],period,phase,
                        stress,panels,mask,states,manifest['identity'],folder,ablations)
        graph_audits.append(dict(round_id=rd['id'],choice=choice,retained_graph_features=graph_names,
            comparison_uses_exact_graph_model_timing_and_cadence=True,selection_uses_ablation_results=False,
            shuffled_control='Fixed trained graph model, only inference graph feature node identities permuted; not independently retrained'))
        assert before==hashlib.sha256(freeze.read_bytes()).hexdigest()
    write_json(out/'graph_ablations.json',dict(audits=graph_audits,records=ablations,selection_uses_results=False))
    records=all_results['records'];years=[];consistency=[]
    for r in records:
        if r['stress'] or r['phase']!='continuous_forward':continue
        file=out/r['round_id']/'runs'/(r['id']+'.json');data=json.loads(file.read_text(encoding='utf-8'))
        nav=pd.Series(data['nav'],index=pd.to_datetime(data['dates']));previous=1.
        for year,part in nav.groupby(nav.index.year):
            curve=np.r_[previous,part.to_numpy()];years.append(dict(round_id=r['round_id'],route=r['route'],year=int(year),
                return_pct=float(part.iloc[-1]/previous-1),max_drawdown=float((curve/np.maximum.accumulate(curve)-1).min()),
                complete_year=int(year)<2026,reset_positions_at_year_boundary=False))
            previous=part.iloc[-1]
    for route in cfg['routes']:
        rows=[r for r in records if r['route']==route and not r['stress'] and r['phase']=='validation']
        consistency.append(dict(route=route,label=cfg['routes'][route]['label'],positive_windows=sum(r['metrics']['annual_return']>0 for r in rows),
            windows=len(rows),worst_validation_annual_return=min(r['metrics']['annual_return'] for r in rows),
            worst_validation_drawdown=min(r['metrics']['max_drawdown'] for r in rows)))
    write_json(out/'interpretation.json',dict(annual_continuous_returns=years,route_consistency=consistency,
        no_route_promoted_to_live=True,post_selection_analysis=True))
    lines=['','## 跨窗口稳定性（仅作事后解读，不更换冻结赢家）','',
        '|路线|正收益验证窗口|较差窗口年化|较差窗口回撤|','|---|---:|---:|---:|']
    for r in consistency:lines.append(f"|{r['label']}|{r['positive_windows']}/{r['windows']}|{r['worst_validation_annual_return']:.2%}|{r['worst_validation_drawdown']:.2%}|")
    lines+=['','## 图特征的同参数消融','',
        '无图对照使用相同模型家族、相同训练标签、相同股票池、相同择时和调仓间隔；标准化和PCA仍分别在训练期拟合。',
        '乱序对照固定已训练模型，仅打乱预测阶段图特征的股票归属。乱序变差只能说明模型依赖此输入，不能单独证明超额因果机制。','',
        '|轮次|保留图特征|原图验证年化/回撤|无图同参数年化/回撤|乱序图年化/回撤|','|---|---|---:|---:|---:|']
    for a in graph_audits:
        original=next(r for r in records if r['round_id']==a['round_id'] and r['route']=='graph_ml' and r['phase']=='validation' and not r['stress'])
        controls=[next(r for r in ablations if r['round_id']==a['round_id'] and r['phase']=='graph_ablation_validation' and r['candidate'].startswith(key) and not r['stress']) for key in ['matched_no_graph','shuffled_graph']]
        fmt=lambda r:f"{r['metrics']['annual_return']:.2%}/{r['metrics']['max_drawdown']:.2%}"
        lines.append(f"|{a['round_id']}|{', '.join(a['retained_graph_features']) or '无'}|{fmt(original)}|{fmt(controls[0])}|{fmt(controls[1])}|")
    lines+=['','## 连续路径逐年收益（2026为不足一年观察）','',
        '|轮次/路线|年份|当年净收益|当年段内回撤|','|---|---:|---:|---:|']
    for r in years:lines.append(f"|{r['round_id']}/{cfg['routes'][r['route']]['label']}|{r['year']}|{r['return_pct']:.2%}|{r['max_drawdown']:.2%}|")
    entries=archive_continuous(cfg,base,out,records,dict(mainboard=eligible,valuation_sample=mask))
    lines+=['','## 平台查看','',f'已将{len(entries)}条连续持仓路径归档到现有回测报告。通过“回测档案”搜索“多路线”可查看净值、回撤、逐年收益、成交和持仓。',
        '归档ID和入口保存在 platform_archives.json；通用回测页仅填同名因子不能复现冻结模型。','']
    report=out/'多路线策略研究.md';current=report.read_text(encoding='utf-8').split('\n## 跨窗口稳定性')[0]
    ml_rows=[next(r for r in records if r['route']=='matched_ml' and r['round_id']==rd['id'] and r['phase']=='validation' and not r['stress']) for rd in cfg['rounds']]
    ml_years=[r for r in years if r['route']=='matched_ml' and r['round_id']==cfg['rounds'][-1]['id'] and r['year']==int(cfg['observation']['start'][:4])]
    value_rows=[r for r in records if r['route']=='value_technical' and r['phase']=='validation' and not r['stress']]
    graph_losses=sum(next(r for r in records if r['route']=='graph_ml' and r['round_id']==rd['id'] and r['phase']=='validation' and not r['stress'])['metrics']['annual_return'] <
        next(r for r in ablations if r['round_id']==rd['id'] and r['phase']=='graph_ablation_validation' and r['candidate'].startswith('matched_no_graph') and not r['stress'])['metrics']['annual_return'] for rd in cfg['rounds'])
    lead=['## 本轮判断','',
        '同池无图机器学习路线的时间隔离验证年化依次为'+ '、'.join(f"{r['metrics']['annual_return']:.2%}" for r in ml_rows)+
        '，回撤依次为'+ '、'.join(f"{abs(r['metrics']['max_drawdown']):.2%}" for r in ml_rows)+
        '。选定模型依次为'+ '、'.join(r['candidate'] for r in ml_rows)+'，并非同一固定模型横跨各段的结果。',
        f"最新一轮同池ML连续持仓在{ml_years[-1]['year']}观察段的当年净收益为{ml_years[-1]['return_pct']:.2%}，截至{cfg['observation']['end']}。尚不支持稳定达到年化参考值的结论。",
        f"估值与技术在{sum(r['metrics']['annual_return']>0 for r in value_rows)}/{len(value_rows)}段验证期为正收益，较低年化为{min(r['metrics']['annual_return'] for r in value_rows):.2%}。图路线在{graph_losses}/{len(cfg['rounds'])}段同参数比较中低于无图模型。",
        '当前结果提示股票池与择时规则比增加模型复杂度更值得研究；这是本轮结果的推断，尚未完成股票池选择的独立验证。','']
    heading='# 最多10只股票的多路线策略研究\n'
    current=heading+'\n'+'\n'.join(lead)+'\n'+current[current.index('研究日期：'):]
    report.write_text(current+'\n'+'\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(ok=True,ablation_ledgers=len(ablations),annual_segments=len(years),graph_audits=graph_audits),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
