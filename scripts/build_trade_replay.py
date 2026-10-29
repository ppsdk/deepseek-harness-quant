"""Materialize read-only trade explanations from frozen scores and original fills."""
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from scripts.mainboard_repair_research import load_cached_snapshot
from data.cache import DailyCache


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')),encoding='utf-8')
    temporary.replace(path)


def finite(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def strategy_description(cfg,base,raw,recipe,fit,labels,stock_count):
    rd=recipe['round'];e=raw['params'];timing=cfg['timing']
    data=dict(stocks=stock_count,history=base['universe']['min_history_days'],
        liquidity_window=base['universe']['liquidity_window'],amount=f"{base['universe']['min_average_amount']:,.0f}",
        discovery_start=rd['discovery']['start'],discovery_end=rd['discovery']['end'],inner_end=rd['inner_training_end'],
        selection_start=rd['selection']['start'],selection_end=rd['selection']['end'],
        validation_start=rd['validation']['start'],validation_end=rd['validation']['end'],
        horizon=cfg['ml']['horizon'],input_dimension=fit['input_dimension'],selected_dimension=fit['selected_dimension'],
        output_dimension=fit['output_dimension'],variance=f"{fit['cumulative_explained_variance']:.2%}",
        topn=e['topn'],weight=e['max_position_pct'],retain=cfg['selection_policy']['retain_rank'],
        margin=cfg['selection_policy']['replacement_score_margin'],fast=timing['fast'],slow=timing['slow'],confirm=timing['confirm_days'],
        cadence=raw['cadence'],interval=e['min_trade_interval_days'],threshold=e['rebalance_threshold_pct'],
        capital=f"{e['initial_capital']:,.0f}",commission=e['commission_bps'],minimum=e['min_commission'],tax=e['stamp_tax_bps'],slippage=e['slippage_bps'])
    descriptions=dict(labels['description'])
    if recipe.get('selection_free'):
        descriptions['training']=labels['protocol_descriptions'][recipe['protocol']]
        if recipe['protocol']=='annual_expanding':descriptions['preprocessing']=labels['protocol_descriptions']['annual_preprocessing']
    return [dict(key=k,text=template.format(**data)) for k,template in descriptions.items()]


def build_one(cfg,base,out,entry,panels,signature,labels,folder):
    archive_path=ROOT/'output/backtest_archive'/(entry['name']+'.json')
    source_path=ROOT/entry['source_ledger']
    rd=entry['round_id'];route=entry['route']
    recipe_path=out/rd/(route+'_frozen.json');recipe=read(recipe_path)
    choice=recipe['choice'];model=choice['candidate']
    fit_path=out/rd/f'{route}__{model}__final_fit.json'
    score_path=out/rd/'cache'/f'{route}__{model}__final.npy'
    paths=[archive_path,source_path,recipe_path,fit_path,score_path,out/'experiment_manifest.json',out/'market_states.csv']
    source_hashes={str(p.relative_to(ROOT)):digest(p) for p in paths}
    raw=read(source_path);archive=read(archive_path);fit=read(fit_path)
    assert raw['nav']==archive['nav'] and raw['trades']==archive['trades']
    assert choice['timing']=='ma_gate', 'This explanation currently supports the MA-gated route'
    codes=archive['data']['selected_codes'];columns=panels['close'].columns.get_indexer(codes)
    dates=panels['close'].index;index_position={d:i for i,d in enumerate(dates)}
    assert min(columns)>=0
    scores=np.load(score_path,mmap_mode='r')
    assert scores.shape==(len(dates),len(codes))
    schedule=fit.get('fit_schedule',[fit])
    factor_names=list(dict.fromkeys(n for f in schedule for n in f['selected_features']))
    def fit_for(day):
        if recipe.get('protocol')=='annual_expanding':
            return next((f for f in reversed(schedule) if f['signal_start']<=day<=f['signal_end']),None)
        return schedule[0]
    def fit_evidence(day):
        f=fit_for(day) if day else None
        return {k:f[k] for k in ['fit_end','last_feature_date','last_outcome_date','samples','selected_features','selected_dimension','output_dimension','cumulative_explained_variance']} if f else None
    factors={n:np.load(out/'cache'/(n+'.npy'),mmap_mode='r') for n in factor_names}
    for n in factor_names:
        path=out/'cache'/(n+'.npy');source_hashes[str(path.relative_to(ROOT))]=digest(path)
    index_code=raw['params']['timing_index']
    frame=DailyCache().get_daily(index_code,start=str(dates[0]),end=str(dates[-1]),adjust='none').set_index('date').reindex(dates)
    fast=frame.close.rolling(cfg['timing']['fast']).mean();slow=frame.close.rolling(cfg['timing']['slow']).mean()
    states=pd.read_csv(out/'market_states.csv',index_col='date')
    gate=states.ma_gate
    expected=(frame.close.gt(slow)&fast.gt(slow)).rolling(cfg['timing']['confirm_days']).sum().eq(cfg['timing']['confirm_days'])
    known=gate.dropna().index
    np.testing.assert_array_equal(gate.loc[known].eq(1).to_numpy(),expected.loc[known].to_numpy())
    rank_cache={}
    def ranked(day):
        if day not in rank_cache:
            pos=index_position[day]
            valid=np.isfinite(scores[pos])
            valid &= panels['close'].loc[day,codes].gt(0).to_numpy()
            valid &= panels['volume'].loc[day,codes].gt(0).to_numpy()
            valid &= panels['is_st'].loc[day,codes].ne(1).to_numpy()
            order=np.flatnonzero(valid)
            order=order[np.argsort(-scores[pos,order],kind='stable')]
            rank_cache[day]=(order,{codes[j]:i+1 for i,j in enumerate(order)})
        return rank_cache[day]
    def stock_signal(code,day):
        if day is None or code not in codes:return dict(rank=None,score=None,factors={})
        pos=index_position[day];j=codes.index(code);_,ranks=ranked(day)
        return dict(rank=ranks.get(code),score=finite(scores[pos,j]),
            factors={n:finite(factors[n][pos,columns[j]]) for n in factor_names})
    def market(day):
        if day is None:return None
        return dict(date=day,close=finite(frame.at[day,'close']),fast=finite(fast.loc[day]),slow=finite(slow.loc[day]),
            gate=finite(gate.loc[day]),close_above_slow=bool(frame.at[day,'close']>slow.loc[day]),fast_above_slow=bool(fast.loc[day]>slow.loc[day]))
    trades_by_date=defaultdict(list);rejections_by_date=defaultdict(list)
    for t in raw['trades']:trades_by_date[t['date']].append(t)
    for t in raw['rejected_orders']:rejections_by_date[t['date']].append(t)
    holdings={};basis={};entered={};last_marks={};daily=[];details={};last_trade=None
    for k,account in enumerate(raw['daily_account']):
        day=account['date'];pos=index_position[day];previous=str(dates[pos-1]) if pos else None
        before=dict(holdings);day_trades=[];realized=0.
        for t in trades_by_date[day]:
            code=t['code'];assert np.isclose(holdings.get(code,0),t['position_before'])
            if t['side']=='buy':
                if t['position_before']<=1e-8:entered[code]=pos
                basis[code]=(t['position_before']*basis.get(code,0)+t['amount']+t['commission'])/t['position_after']
            else:realized+=t['amount']-t['commission']-t['stamp_tax']-t['quantity']*basis[code]
            if t['position_after']>1e-8:holdings[code]=t['position_after']
            else:holdings.pop(code,None);basis.pop(code,None);entered.pop(code,None)
            evidence=stock_signal(code,t['signal_date'])
            model_fit=fit_evidence(t['signal_date'])
            assert model_fit and model_fit['last_outcome_date']<=t['signal_date'] and model_fit['fit_end']<t['date']
            day_trades.append(dict(t,signal_rank=evidence['rank'],model_score=evidence['score'],signal_market=market(t['signal_date']),factor_percentiles=evidence['factors'],fit_evidence=model_fit))
        positions=[]
        for code,quantity in holdings.items():
            value=panels['close'].at[day,code]
            if pd.notna(value) and value>0:last_marks[code]=float(value)
            mark=last_marks.get(code)
            if mark is None:raise ValueError('Missing frozen holding valuation '+code+' '+day)
            mv=quantity*mark
            positions.append(dict(code=code,quantity=quantity,quantity_before=before.get(code,0),quantity_change=quantity-before.get(code,0),
                mark_price=mark,market_value=mv,weight=mv/account['equity'],cost_price=basis[code],
                unrealized_pnl=quantity*(mark-basis[code]),holding_trading_days=pos-entered[code]))
        positions.sort(key=lambda x:-x['weight'])
        total_value=sum(t['market_value'] for t in positions)
        assert np.isclose(total_value,account['market_value'],atol=.02),day
        assert np.isclose(account['cash']+total_value,account['equity'],atol=.02),day
        assert len(positions)==account['holding_count']
        regular=any(t['trigger']!='market_risk_exit' for t in day_trades)
        emergency=any(t['trigger']=='market_risk_exit' for t in day_trades)
        status='mixed' if regular and emergency else 'emergency' if emergency else 'regular' if regular else 'holding' if positions else 'cash'
        order,ranks=ranked(previous) if previous else ([],{})
        candidate_codes=[codes[j] for j in order[:labels['candidate_limit']]]
        for code in list(before)+[t['code'] for t in day_trades]:
            if code not in candidate_codes:candidate_codes.append(code)
        candidates=[dict(code=code,**stock_signal(code,previous),held_before=code in before,held_after=code in holdings,
            bought=any(t['side']=='buy' and t['code']==code for t in day_trades),sold=any(t['side']=='sell' and t['code']==code for t in day_trades)) for code in candidate_codes]
        ret=raw['nav'][k]/(raw['nav'][k-1] if k else 1)-1
        row=dict(date=day,signal_date=previous,status=status,trade_count=len(day_trades),
            buy_count=sum(t['side']=='buy' for t in day_trades),sell_count=sum(t['side']=='sell' for t in day_trades),
            cash=account['cash'],equity=account['equity'],market_value=account['market_value'],exposure=account['exposure'],
            holding_count=account['holding_count'],nav=raw['nav'][k],daily_return=ret,market=market(day))
        daily.append(row)
        details[day]=dict(**row,signal_market=market(previous),fit_evidence=fit_evidence(previous),eligible_score_count=len(order),
            regular_calendar_due=k%raw['cadence']==0,gap_since_last_trade=pos-last_trade if last_trade is not None else None,
            positions_before=[dict(code=c,quantity=q) for c,q in before.items()],positions=positions,
            candidates=candidates,trades=day_trades,rejections=rejections_by_date[day],realized_pnl=realized)
        if day_trades:last_trade=pos
    title=f"{labels.get('round_labels',{}).get(rd,rd)} · {labels['model_labels'].get(model,model)}＋指数均线择时"
    summary=dict(ok=True,available=True,version=1,run=entry['name'],title=title,round_id=rd,model=model,
        generated_at=datetime.now().astimezone().isoformat(),data_end=raw['dates'][-1],dates=raw['dates'],daily=daily,
        event_dates=[d['date'] for d in daily if d['trade_count']],
        market_config=dict(code=index_code,fast=cfg['timing']['fast'],slow=cfg['timing']['slow'],confirm_days=cfg['timing']['confirm_days']),
        description=strategy_description(cfg,base,raw,recipe,fit,labels,len(codes)),
        features=[dict(key=n,label=labels['feature_labels'].get(n,n),definition=cfg['features'][n],retained=n in factor_names,
            formula=labels['feature_formulas'][cfg['features'][n]['operation']].format(**cfg['features'][n]['params'])) for n in cfg['technical_features']],
        fit=dict(fit_end=fit['fit_end'],last_feature_date=fit['last_feature_date'],last_outcome_date=fit['last_outcome_date'],samples=fit['samples'],
            cumulative_explained_variance=fit['cumulative_explained_variance'],validation_refit=fit['validation_refit']),
        model_params=cfg['models'][model],execution=raw['params'],selection_policy=cfg['selection_policy'],
        fit_schedule=[dict(fit_evidence(f.get('signal_start') or raw['dates'][0]),prediction_year=f.get('prediction_year')) for f in schedule],
        verification=dict(days=len(daily),trades=sum(d['trade_count'] for d in daily),holdings_reconstructed=True,
            daily_market_value_reconciled=True,source_ledgers_unchanged=True),
        archive_sha256=source_hashes[str(archive_path.relative_to(ROOT))],snapshot_hash=signature,source_hashes=source_hashes)
    for path,h in source_hashes.items():assert digest(ROOT/path)==h,path
    write(folder/(entry['name']+'.days.json'),details)
    write(folder/(entry['name']+'.summary.json'),summary)
    lines=['# '+title,'',f"回放：{raw['dates'][0]} 至 {raw['dates'][-1]}；{len(daily)} 个交易日。",'']
    lines.extend(x['text']+'\n' for x in summary['description'])
    lines+=['## 每次训练边界','', '|预测年度／模型|训练截止|最后特征日|最后标签终点|样本|去相关→PCA|累计解释方差|',
        '|---|---|---|---|---:|---|---:|']
    for f in summary['fit_schedule']:
        label=f['prediction_year'] if len(summary['fit_schedule'])>1 else '全期固定'
        lines.append(f"|{label}|{f['fit_end']}|{f['last_feature_date']}|{f['last_outcome_date']}|{f['samples']}|{f['selected_dimension']}→{f['output_dimension']}|{f['cumulative_explained_variance']:.2%}|")
    lines+=['','## 模型与特征','', '模型参数：`'+json.dumps(summary['model_params'],ensure_ascii=False)+'`。','',
        '|输入特征|公式|方向|','|---|---|---|']
    for f in summary['features']:
        lines.append(f"|{f['label']}|{f['formula']}|{'取负后排名' if f['definition'].get('sign')==-1 else '原值排名'}|")
    lines+=['',labels['factor_note'],'','## 逐笔查看','',
        '平台入口：`'+entry['url']+'`。点击净值图日期，查看市场门控、原信号日排名、实际成交、收盘持仓与现金；点击股票打开K线买卖点。',
        '原成交账本与快照不变，复盘只重建解释。延迟成交按逐笔原信号日核对。','']
    (folder/(entry['name']+'.说明.md')).write_text('\n'.join(lines),encoding='utf-8')
    return dict(run=entry['name'],title=title,url=entry['url'],round_id=rd,
        summary=entry['name']+'.summary.json',days=entry['name']+'.days.json',archive_sha256=summary['archive_sha256'])


def main():
    labels=read_config('research_trade_replay');results=[]
    for study,spec in labels['studies'].items():
        cfg=read_config(spec['config']);base=read_config(cfg['base_config']);out=ROOT/cfg['output_dir']
        if not (out/'platform_archives.json').exists():continue
        manifest=read(out/'experiment_manifest.json')
        assert manifest['config']==cfg
        for name,h in manifest['source_hashes'].items():assert digest(ROOT/name)==h,name
        panels,_,signature,_=load_cached_snapshot(base)
        assert signature==manifest['snapshot_hash']
        folder=out/spec['output_subdir'];folder.mkdir(exist_ok=True)
        entries=[build_one(cfg,base,out,e,panels,signature,labels,folder)
            for e in read(out/'platform_archives.json') if e['route'] in spec['routes']]
        write(folder/'index.json',dict(study=study,entries=entries))
        results.extend(entries)
    print(json.dumps(dict(ok=True,entries=results),ensure_ascii=False))


if __name__=='__main__':main()
