"""Longer fixed-origin and annual walk-forward diagnostic, with no parameter search."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from scripts.mainboard_repair_research import load_cached_snapshot,canonical_hash
from scripts.mainboard_strategy_research import write_json
from scripts.multiroute_strategy_research import model_scores,ledger,SOURCES
from scripts.multiroute_strategy_delivery import archive_continuous
from scripts.analyse_matched_ml_risk import risk_statistics
from validation.audit_multiroute_ledgers import audit as audit_ledgers


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def main():
    cfg=read_config('extratrees_long_cycle');base=read_config(cfg['base_config'])
    out=ROOT/cfg['output_dir'];out.mkdir(parents=True,exist_ok=True)
    source=ROOT/cfg['source_research_dir'];source_manifest=read(source/'experiment_manifest.json')
    for name,h in source_manifest['source_hashes'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==h,name
    panels,eligible,signature,data_audit=load_cached_snapshot(base)
    assert signature==source_manifest['snapshot_hash']
    mapping=read(source/'platform_archives.json')
    anchor=next(e for e in mapping if e['route']=='matched_ml')
    archive=read(ROOT/'output/backtest_archive'/(anchor['name']+'.json'))
    codes=archive['data']['selected_codes'];mask=eligible[codes].copy()
    for n in cfg['valuation_features']:
        mask &= np.isfinite(np.load(source/'cache'/(n+'.npy'),mmap_mode='r'))
    features={n:pd.DataFrame(np.load(source/'cache'/(n+'.npy'),mmap_mode='r'),index=eligible.index,columns=eligible.columns)[codes] for n in cfg['technical_features']}
    states=pd.read_csv(source/'market_states.csv',index_col='date')
    hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in [*SOURCES,'scripts/extratrees_long_cycle.py']}
    identity=canonical_hash(dict(config=cfg,snapshot_hash=signature,source_hashes=hashes))
    path=out/'experiment_manifest.json'
    manifest=dict(identity=identity,config=cfg,snapshot_hash=signature,source_hashes=hashes,
        parameter_search=False,validation_is_fresh_blind=False,policy_chosen_in_later_research=True)
    if path.exists():assert read(path)==manifest,'Use a new output directory for a changed experiment'
    else:write_json(path,manifest)
    data_audit.update(stock_price_start='2019-01-02',index_only_years=[2017,2018],valuation_stocks=len(codes))
    write_json(out/'data_audit.json',data_audit)
    # All feature values come from the exact earlier frozen experiment.
    cache=out/'cache';cache.mkdir(exist_ok=True)
    for n in cfg['technical_features']+cfg['valuation_features']:
        destination=cache/(n+'.npy')
        if not destination.exists():
            import shutil
            shutil.copyfile(source/'cache'/(n+'.npy'),destination)
    if not (out/'market_states.csv').exists():
        import shutil
        shutil.copyfile(source/'market_states.csv',out/'market_states.csv')
    fits=[];score_frames={}
    for rd in cfg['rounds']:
        folder=out/rd['id'];(folder/'cache').mkdir(parents=True,exist_ok=True)
        score,fit=model_scores(cfg,rd,'matched_ml','extra_trees','final',features,cfg['technical_features'],mask,panels,folder)
        assert fit['last_outcome_date']<=fit['fit_end']<rd['validation']['start']
        record=deepcopy(fit)
        start=rd['validation']['start'];end=rd['validation']['end']
        positions=np.flatnonzero(mask.index<start)
        record.update(prediction_year=int(start[:4]),prediction_start=start,prediction_end=end,
            signal_start=str(mask.index[positions[-1]]),signal_end=str(mask.index[mask.index<=end][-1]),
            source_fit=str((folder/'matched_ml__extra_trees__final_fit.json').relative_to(ROOT)))
        fits.append(record);score_frames[int(start[:4])]=score
    begin=f"{cfg['first_prediction_year']}-01-01";end=cfg['observation']['end']
    continuous=dict(start=begin,end=end)
    expanding=pd.DataFrame(np.nan,index=mask.index,columns=codes,dtype=np.float32)
    for fit in fits:
        # The last close of the preceding year predicts the first open of this year.
        chosen=(mask.index>=fit['signal_start'])&(mask.index<=fit['signal_end'])
        expanding.loc[chosen]=score_frames[fit['prediction_year']].loc[chosen]
    rows=[];contracts=[]
    protocols=[('round_fixed_2020','fixed_origin',score_frames[cfg['first_prediction_year']],fits[:1]),
               ('round_annual_expanding','annual_expanding',expanding,fits)]
    for rid,protocol,score,schedule in protocols:
        folder=out/rid;(folder/'cache').mkdir(parents=True,exist_ok=True);(folder/'runs').mkdir(exist_ok=True)
        np.save(folder/'cache/matched_ml__extra_trees__final.npy',score.to_numpy(dtype=np.float32))
        summary=deepcopy(schedule[-1]);summary['fit_schedule']=schedule;summary['protocol']=protocol
        write_json(folder/'matched_ml__extra_trees__final_fit.json',summary)
        discovery=dict(start='2019-01-01',end=schedule[-1]['fit_end'])
        rd=dict(id=rid,discovery=discovery,inner_training_end=discovery['end'],
            selection=dict(start=discovery['start'],end=discovery['end']),validation=continuous)
        choice=dict(candidate='extra_trees',timing='ma_gate',cadence=cfg['cadences'][0])
        recipe=dict(identity=identity,round=rd,route='matched_ml',choice=choice,protocol=protocol,
            selection_free=True,selection_uses_validation=False,parameter_search=False,
            validation_is_fresh_blind=False,fit_schedule=schedule,
            note=cfg['design_note'])
        frozen=folder/'matched_ml_frozen.json'
        if frozen.exists():assert read(frozen)==recipe
        else:write_json(frozen,recipe)
        contracts.append(dict(route='matched_ml',round_id=rid,choice=choice,frozen_sha256=hashlib.sha256(frozen.read_bytes()).hexdigest()))
        for stress in (False,True):
            ledger(cfg,rd,'matched_ml','extra_trees',score,None,'ma_gate',choice['cadence'],continuous,
                'continuous_forward',stress,panels,mask,states,identity,folder,rows)
    write_json(out/'results.json',dict(config=cfg,records=rows,contracts=contracts,fit_schedule=fits))
    archive_continuous(cfg,base,out,rows,dict(valuation_sample=mask))
    independent=audit_ledgers(cfg)
    lines=['# ExtraTrees长周期检验','',cfg['design_note'],'',
        '股票行情从2019年起，资格热身后训练特征从2020年起；本次预测回放为2021-01-01至2026-10-23。2017—2018没有股票价格，不计为验证年。',
        '固定模型只在2020年末前拟合一次，之后不重训。年度滚动每年用截至上一年末的扩展历史重训；预测年度不进入该年度的训练、标准化或PCA。过去年度在后续轮次转为训练，不是整个2021—2026一次性独立留出。',
        '规则和参数来自后期研究，本次属于事后历史诊断；不能称新增盲测或用更早预测年份证明事前选参有效。','',
        '|协议|成本|总收益|年化|夏普 rf=0|波动|最大回撤|Calmar|单边年换手|平均仓位|',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    yearly=[]
    for row in rows:
        m=row['metrics'];label='2020末固定模型' if row['round_id']=='round_fixed_2020' else '每年扩展训练'
        lines.append(f"|{label}|{'压力' if row['stress'] else '基础'}|{m['total_return']:.2%}|{m['annual_return']:.2%}|{m['sharpe']:.2f}|{m['volatility']:.2%}|{m['max_drawdown']:.2%}|{m['calmar']:.2f}|{row['annual_one_way_turnover']:.2f}倍|{row['average_exposure']:.2%}|")
        if row['stress']:continue
        raw=read(out/row['round_id']/'runs'/(row['id']+'.json'))
        nav=np.asarray(raw['nav']);r=pd.Series(nav/np.r_[1.,nav[:-1]]-1,index=pd.DatetimeIndex(raw['dates']))
        for year in sorted(set(r.index.year)):
            stats=risk_statistics(r.loc[r.index.year==year],.02)
            yearly.append(dict(protocol=row['round_id'],year=int(year),**stats))
    lines+=['','|协议|年份|连续账户实际收益|夏普|年内最大回撤|','|---|---|---:|---:|---:|']
    for v in yearly:
        lines.append(f"|{'固定2020' if v['protocol']=='round_fixed_2020' else '年度扩展'}|{v['year']}|{v['total_return']:.2%}|{v['sharpe']:.2f}|{v['max_drawdown']:.2%}|")
    lines+=['','年份收益包含跨年持仓，账户从2021年连续运行，年度滚动也不在年末清空。2026不足一年，表内实际收益不做年化冒充全年收益。',
        '基础成本为佣金3bp、最低5元、滑点10bp和固定卖出税10bp；压力佣金5bp、滑点30bp。现金不计息。沪深300为未计分红的价格指数。',
        '最多10只股票；普通成交至少相隔5个交易日，指数风险退出允许例外。保留旧持仓前15名与0.03分数缓冲，MA20/60连续2日确认。未增加个股止盈止损。',
        '当前幸存者池、历史ST/退市和估值版本偏差仍在。单一随机种子，不给统计显著性结论；不按新增结果更换赢家或倒调参数。','',
        '## 每轮标签边界','', '|预测年度|训练截止|最后特征日|最后标签终点|样本数|去相关后维数→PCA|累计解释方差|',
        '|---|---|---|---|---:|---|---:|']
    for f in fits:
        lines.append(f"|{f['prediction_year']}|{f['fit_end']}|{f['last_feature_date']}|{f['last_outcome_date']}|{f['samples']}|{f['selected_dimension']}→{f['output_dimension']}|{f['cumulative_explained_variance']:.2%}|")
    lines+=['','独立审计通过：'+str(independent['ledgers'])+'份账本、'+str(independent['trades'])+'笔成交，逐日现金与源价格持仓重建通过。复现：`.venv/Scripts/python.exe scripts/extratrees_long_cycle.py`。','']
    write_json(out/'long_cycle_analysis.json',dict(yearly=yearly,fit_schedule=fits,independent_audit=independent))
    (out/'ExtraTrees长周期检验.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(ok=True,report=str(out/'ExtraTrees长周期检验.md'),audit=independent),ensure_ascii=False))


if __name__=='__main__':main()
