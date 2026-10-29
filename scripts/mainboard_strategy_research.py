"""Bounded, config-driven main-board research using the platform's real ledger.

Run: python scripts/mainboard_strategy_research.py
Local cache files contain provider-derived data and must not be redistributed.
"""
import gc
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from factors.research_library import read_config, factor_definitions, raw_factor
from backtest import research_runner as runner
from backtest.research_timing import evaluate
from backtest.bt_report import compute_metrics
from data.cache import DailyCache, normalize_units
from functools import lru_cache


@lru_cache(maxsize=64)
def benchmark_snapshot(code, dates):
    frame=DailyCache().get_daily(code,start=dates[0],end=dates[-1],adjust='none').set_index('date').reindex(dates)
    if frame[['open','close']].isna().any().any() or (frame[['open','close']]<=0).any().any():
        raise ValueError('Benchmark prices are missing; do not substitute or fill them.')
    nav=frame.close/frame.open.iloc[0]
    returns=nav.pct_change(fill_method=None);returns.iloc[0]=nav.iloc[0]-1
    returns.index=pd.to_datetime(returns.index)
    return dict(nav=list(nav),metrics=compute_metrics(returns))


def evaluate_return_target(item, dates, policy):
    bench=benchmark_snapshot(policy['benchmark']['code'],tuple(str(d)[:10] for d in dates))
    annual=item['metrics']['annual_return'];reference=bench['metrics']['annual_return']
    item['metrics'].update(benchmark_annual_return=reference,annual_excess_return=annual-reference,
        annual_excess_pp=(annual-reference)*100,relative_annual_return=(1+annual)/(1+reference)-1)
    targets=policy['targets'];accepted=targets['accepted_return_measures']
    max_replacements=targets.get('max_replacements',targets.get('max_traded_codes_per_day',targets['max_holdings']))
    meets=lambda value,threshold:value>=threshold or math.isclose(value,threshold,rel_tol=0,abs_tol=1e-12)
    return_pass=('absolute' in accepted and meets(annual,targets['annual_return'])) or ('excess_pp' in accepted and meets(annual-reference,targets['excess_annual_return']))
    item.update(return_target_pass=bool(return_pass),benchmark_code=policy['benchmark']['code'],return_evaluation_version='absolute_or_index_excess_pp',
        target_pass=bool(return_pass and meets(item['metrics']['max_drawdown'],targets['max_drawdown']) and
                         item['max_holdings']<=targets['max_holdings'] and item['frequency_pass'] and
                         item['max_bought_codes']<=max_replacements and item['max_sold_codes']<=max_replacements))
    return item


def write_json(path, value):
    def convert(obj):
        if isinstance(obj, np.generic): return obj.item()
        if isinstance(obj, Path): return str(obj)
        raise TypeError(type(obj).__name__)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=convert, allow_nan=False), encoding='utf-8')


def log(message):
    print(time.strftime('%H:%M:%S'), message, flush=True)


def metadata(cache):
    rows = {}
    for db in [cache.db_path, *cache._inc_paths()]:
        if not Path(db).exists(): continue
        with sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True) as con:
            for code, start, end, count in con.execute("SELECT code,start_date,end_date,rows FROM bar_meta WHERE adjust='qfq'"):
                rows[code] = dict(code=code, start=start, end=end, rows=count)
    return rows


def load_snapshot(policy, out):
    """Chunked reads and float64 disk arrays keep peak memory bounded."""
    cache = DailyCache()
    codes = sorted(code for code in metadata(cache) if re.fullmatch(policy['universe']['code_pattern'], code))
    index = cache.get_daily(policy['execution']['timing_index'], start=policy['data_start'], end=policy['data_end'], adjust='none')
    dates = pd.Index(sorted(index.date.unique()), name='date')
    fields = ['open', 'high', 'low', 'close', 'volume', 'amount', 'turn', 'is_st']
    folder = out / 'cache'
    folder.mkdir(exist_ok=True)
    identity = dict(codes=codes, dates=list(dates), db=[(str(p), Path(p).stat().st_size, Path(p).stat().st_mtime_ns)
                   for p in [cache.db_path, *cache._inc_paths()] if Path(p).exists()])
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    stamp = folder / 'snapshot.json'
    reuse = stamp.exists() and json.loads(stamp.read_text(encoding='utf-8')).get('signature') == signature
    arrays = {}
    for field in fields:
        file = folder / (field + '.npy')
        arrays[field] = np.load(file, mmap_mode='r') if reuse else np.lib.format.open_memmap(file, mode='w+', dtype='float64', shape=(len(dates),len(codes)))
        if not reuse: arrays[field][:] = np.nan
    if not reuse:
        chunk_size = policy['resources']['read_chunk_stocks']
        for start in range(0, len(codes), chunk_size):
            chunk = codes[start:start+chunk_size]
            frames = cache.get_daily_batch(chunk, start=policy['data_start'], end=policy['data_end'], adjust='qfq')
            for offset, code in enumerate(chunk):
                frame = frames.get(code)
                if frame is None or frame.empty: continue
                frame = normalize_units(frame).set_index('date').reindex(dates)
                for field in fields:
                    arrays[field][:,start+offset] = pd.to_numeric(frame[field], errors='coerce').to_numpy()
            del frames
            for array in arrays.values(): array.flush()
            if start % (chunk_size * 5) == 0: log(f'data {min(start+chunk_size,len(codes))}/{len(codes)}')
            gc.collect()
        write_json(stamp, dict(signature=signature, **identity))
    panels = {field: pd.DataFrame(array, index=dates, columns=codes, copy=False) for field,array in arrays.items()}
    u = policy['universe']
    eligible = panels['close'].notna().cumsum().ge(u['min_history_days'])
    eligible &= panels['amount'].rolling(u['liquidity_window']).mean().ge(u['min_average_amount'])
    eligible &= panels['volume'].gt(0) & panels['close'].gt(0)
    if u['exclude_st']: eligible &= panels['is_st'].ne(1)
    write_json(out/'data_audit.json', dict(signature=signature, codes=len(codes), start=str(dates[0]), end=str(dates[-1]),
        trade_days=len(dates), eligible_latest=int(eligible.iloc[-1].sum()),
        turn_latest_coverage=float(panels['turn'].iloc[-1].notna().mean()),
        st_latest_coverage=float(panels['is_st'].iloc[-1].notna().mean()),
        latest_valid_price=int(panels['close'].iloc[-1].notna().sum()),
        first_eligible_date=str(eligible.index[eligible.any(axis=1)][0]),
        first_equity_date=str(panels['close'].index[panels['close'].notna().any(axis=1)][0]),
        provider_data_local_only=True, source_units_normalized=True,
        universe_limitation='当前缓存幸存者池，未重建历史退市全集；历史ST状态依赖缓存真实性。'))
    return panels, eligible, signature


def market_state(panels, eligible, policy, out):
    cache = DailyCache(); timing = read_config('research_services')['timing']
    result = dict(research_as_of=policy['as_of'], equity_snapshot=policy['data_end'], indices={},
        breadth=dict(date=str(panels['close'].index[-1]),
            above_ma60=float((panels['close'].iloc[-1]>panels['close'].rolling(60).mean().iloc[-1]).where(eligible.iloc[-1]).mean()),
            eligible=int(eligible.iloc[-1].sum())))
    for code in policy['market_indices']:
        frame=cache.get_daily(code, adjust='none').sort_values('date').set_index('date')
        c=frame.close
        result['indices'][code]=dict(date=str(frame.index[-1]),close=float(c.iloc[-1]),
            daily_return=float(c.pct_change(fill_method=None).iloc[-1]),
            ma={str(n):float(c.rolling(n).mean().iloc[-1]) for n in policy['market_windows']},
            volatility20=float(c.pct_change(fill_method=None).rolling(20).std().iloc[-1]*np.sqrt(252)),
            drawdown60=float(c.iloc[-1]/c.tail(60).max()-1),
            gates={key:bool(evaluate(frame,key,timing).iloc[-1]==1) for key in timing['rules']})
    write_json(out/'market_state.json',result)
    return result


def factor_study(panels, eligible, policy, out):
    definitions = factor_definitions()
    specs = list(policy['ic']['factors']) + [dict(name=key,sign=value['default_sign']) for key,value in policy['new_factors'].items()]
    needed={x['name'] for x in specs}
    cfg=runner.research_config()
    for strategy in policy['baseline_strategies']:
        needed.update(x['name'] for x in cfg['strategies'][strategy]['factor_list'])
    discovery=policy['periods']['discovery']; rows=[]
    dates=panels['close'].index
    for name in sorted(needed):
        raw=raw_factor(name, panels, definitions)
        np.save(ROOT/policy['output_dir']/'cache'/('factor_'+name+'.npy'), raw.to_numpy())
        for spec in [x for x in specs if x['name']==name]:
            for horizon in policy['ic']['horizons']:
                train_dates=dates[(dates>=discovery['start'])&(dates<=discovery['end'])]
                selected=train_dates[:-(horizon+1):policy['ic']['sample_every_days']]
                opening=panels['open']
                future=opening.shift(-(horizon+1)).loc[selected]/opening.shift(-1).loc[selected]-1
                pair=raw.loc[selected].where(eligible.loc[selected]) * spec['sign']
                mask=pair.notna()&future.notna()
                mask &= panels['volume'].shift(-1).loc[selected].gt(0)&panels['volume'].shift(-(horizon+1)).loc[selected].gt(0)
                mask &= panels['high'].shift(-1).loc[selected].gt(panels['low'].shift(-1).loc[selected])
                mask &= panels['high'].shift(-(horizon+1)).loc[selected].gt(panels['low'].shift(-(horizon+1)).loc[selected])
                values=pair.where(mask).rank(axis=1).corrwith(future.where(mask).rank(axis=1),axis=1).where(mask.sum(axis=1)>=policy['ic']['min_stocks']).dropna()
                sd=float(values.std())
                rows.append(dict(factor=name,sign=spec['sign'],horizon=horizon,ic_mean=float(values.mean()),
                    ic_std=sd,ic_ir=float(values.mean()/sd) if sd else None,positive_fraction=float(values.gt(0).mean()),
                    observations=len(values),first_signal=str(values.index[0]),last_signal=str(values.index[-1]),
                    label_end_bound=discovery['end']))
                del future,pair,mask,values
        panels.pop('_factor_cache',None)
        del raw
        gc.collect();log('IC '+name)
    pd.DataFrame(rows).to_csv(out/'factor_ic.csv', index=False, encoding='utf-8-sig')
    write_json(out/'factor_ic.json', rows)
    ranking=pd.DataFrame(rows).groupby('factor').ic_mean.mean().sort_values(ascending=False)
    selected=ranking.head(policy['ic']['select_top']).index.tolist()
    strategies={key:cfg['strategies'][key] for key in policy['baseline_strategies']}
    signed={s['name']:s['sign'] for s in specs}
    for name in selected:
        key='mined_'+name
        names=list(dict.fromkeys([name,*policy['mined_defensive_factors']]))
        strategies[key]=dict(name='主板探索：'+cfg['factor_catalog'][name]['label'],category='研究候选',
            desc='仅发现期IC选择因子，结合预先固定的防御项；未确认满足20%年化/10%回撤目标。',
            factor_list=[dict(name=n,sign=signed[n]) for n in names],factors=names,defaults={})
    write_json(out/'frozen_candidates.json',dict(selected_from_discovery_ic=selected,ranking=ranking.to_dict(),strategies=strategies))
    return strategies


def strategy_scores(panels, eligible, strategies, out, policy):
    paths={}
    cache=ROOT/policy['output_dir']/'cache'
    for key,meta in strategies.items():
        score=None;valid=eligible.copy();total=0.
        for factor in meta['factor_list']:
            raw=pd.DataFrame(np.load(cache/('factor_'+factor['name']+'.npy'),mmap_mode='r'),index=eligible.index,columns=eligible.columns)
            rank=(raw*factor.get('sign',1)).rank(axis=1,pct=True)
            weight=factor.get('weight',1)
            score=rank*weight if score is None else score+rank*weight
            total+=weight;valid &= raw.notna()
            if 'min' in factor:valid &= raw.ge(factor['min'])
            if 'max' in factor:valid &= raw.le(factor['max'])
            del raw,rank
        path=cache/('score_'+key+'.npy')
        np.save(path,(score/total).where(valid).to_numpy());paths[key]=path
        del score,valid;gc.collect()
    return paths


def summarize(sim, p, policy):
    metrics=compute_metrics(sim['returns'])
    nav=sim['nav_series'].to_numpy()
    metrics['max_drawdown']=float((nav/np.maximum.accumulate(np.r_[1.,nav])[1:]-1).min())
    metrics['calmar']=metrics['annual_return']/abs(metrics['max_drawdown']) if metrics['max_drawdown']<0 else 0.
    account=pd.DataFrame(sim['daily_account']);trades=pd.DataFrame(sim['trades'])
    trade_days=sorted(trades.date.unique()) if len(trades) else []
    years=(pd.Timestamp(account.date.iloc[-1])-pd.Timestamp(account.date.iloc[0])).days/365.25
    turnover=float(trades.amount.sum()/account.equity.mean()/years/2) if len(trades) else 0.
    dates=list(account.date);positions={d:i for i,d in enumerate(dates)}
    gaps=[positions[b]-positions[a] for a,b in zip(trade_days,trade_days[1:])]
    normal=trades[~trades.trigger.eq('market_risk_exit')] if len(trades) else trades
    normal_days=sorted(normal.date.unique()) if len(normal) else []
    normal_gaps=[positions[b]-positions[a] for a,b in zip(normal_days,normal_days[1:])]
    normal_after_any=[positions[day]-positions[previous] for previous,day in zip(trade_days,trade_days[1:]) if day in normal_days]
    max_hold=int(account.holding_count.max())
    buys=trades[trades.side.eq('buy')].groupby('date').code.nunique().max() if len(trades) else 0
    sells=trades[trades.side.eq('sell')].groupby('date').code.nunique().max() if len(trades) else 0
    targets=policy['targets']
    max_replacements=targets.get('max_replacements',targets.get('max_traded_codes_per_day',targets['max_holdings']))
    interval=p['min_trade_interval_days'] if p.get('trade_mode')=='event' else p['rebalance_days']
    valid_frequency=all(g>=interval for g in (normal_after_any if p.get('trade_mode')=='event' else gaps))
    max_traded=int(trades.groupby('date').code.nunique().max()) if len(trades) else 0
    hard=metrics['annual_return']>=targets['annual_return'] and metrics['max_drawdown']>=targets['max_drawdown'] and max_hold<=targets['max_holdings'] and valid_frequency and buys<=max_replacements and sells<=max_replacements
    result=dict(metrics=metrics,verdict=runner._grade_verdict(metrics),target_pass=bool(hard),
        max_holdings=max_hold,annual_one_way_turnover=turnover,trade_dates=len(trade_days),
        minimum_trade_day_gap=int(min(gaps)) if gaps else None,frequency_pass=valid_frequency,
        minimum_regular_trade_gap=int(min(normal_gaps)) if normal_gaps else None,
        minimum_regular_after_any_gap=int(min(normal_after_any)) if normal_after_any else None,
        emergency_exit_dates=len(set(trade_days)-set(normal_days)),max_traded_codes=max_traded,
        max_bought_codes=int(buys) if pd.notna(buys) else 0,max_sold_codes=int(sells) if pd.notna(sells) else 0,
        average_exposure=float(account.exposure.mean()),stale_valuation_days=sim['stale_valuation_days'],
        trade_count=len(trades),rejections=len(sim['rejections']),
        costs={field:float(trades[field].sum()) if len(trades) else 0. for field in ['commission','stamp_tax','slippage_cost']})
    return evaluate_return_target(result,sim['nav_series'].index,policy)


def execute(panels, score, period, variant, policy, *, attach_cadence=True):
    p={**policy['execution'],**period,**{k:v for k,v in variant.items() if k!='id'}}
    view=dict(panels)
    if variant.get('selection_policy'):
        view['_selection_policy']=policy['stock_selection_policies'][variant['selection_policy']]
    if p['timing_rule']!='none':
        timing = ({
            'benchmark': policy['benchmark'],
            'rules': policy['timing_rules'],
        } if policy.get('timing_rules') else read_config('research_services')['timing'])
        frame=DailyCache().get_daily(p['timing_index'],adjust='none').set_index('date').sort_index()
        gate=evaluate(frame,p['timing_rule'],timing).reindex(score.index)
        view['_timing']=pd.DataFrame({c:gate for c in score.columns})
        score=score.where(gate.eq(1),axis=0)
    if attach_cadence and p.get('trade_mode')=='event':
        from backtest.research_cadence import attach
        attach(view,p)
    return runner.simulate(view,score,p),p


def archive_finalists(policy,out,panels,eligible,signature,results):
    """Put selected research runs into the existing platform archive, with gaps explicit."""
    cfg=runner.research_config();archives=[]
    audit=json.loads((ROOT/policy['output_dir']/'data_audit.json').read_text(encoding='utf-8'))
    frozen=json.loads((out/'frozen_candidates.json').read_text(encoding='utf-8'))['strategies']
    for item in results['validation']:
        key=item['strategy'];meta=frozen[key]
        score_name=item.get('score_file','score_'+key+'.npy')
        score=pd.DataFrame(np.load(ROOT/policy['output_dir']/'cache'/score_name,mmap_mode='r'),index=eligible.index,columns=eligible.columns)
        p={**{k:v['default'] for k,v in cfg['parameters'].items()},**item['params'],
           'strategy':key,'stocks':len(score.columns),'codes':','.join(score.columns)}
        view=dict(panels)
        if p.get('selection_policy'):
            view['_selection_policy']=policy['stock_selection_policies'][p['selection_policy']]
        if p['timing_rule']!='none':
            timing=read_config('research_services')['timing']
            frame=DailyCache().get_daily(p['timing_index'],adjust='none').set_index('date').sort_index()
            gate=evaluate(frame,p['timing_rule'],timing).reindex(score.index)
            view['_timing']=pd.DataFrame({code:gate for code in score.columns})
            score=score.where(gate.eq(1),axis=0)
        if p.get('trade_mode')=='event':
            from backtest.research_cadence import attach as attach_cadence
            attach_cadence(view,p)
        data=dict(available_stocks=len(score.columns),selected_codes=list(score.columns),selection='mainboard_research_filter',
                  price_adjustment='qfq',snapshot_hash=signature,universe_policy=policy['universe'])
        result=runner.build_result(view,data,score,p,cfg,meta,time.monotonic(),archive=False)
        if abs(result['metrics']['annual_return']-item['metrics']['annual_return'])>1e-10:
            raise AssertionError('Archive replay differs from research ledger')
        result['title']='主板滚动 '+policy['round_id']+' · '+meta['name']
        result['category']='主板低频研究'
        benchmark=benchmark_snapshot(policy['benchmark']['code'],tuple(result['dates']))
        result['benchmark']=policy['benchmark']
        result['bench_nav']=benchmark['nav']
        result['excess_nav']=list(np.array(result['nav'])/np.array(result['bench_nav']))
        result['metrics'].update({key:item['metrics'][key] for key in ['benchmark_annual_return','annual_excess_return','annual_excess_pp','relative_annual_return']})
        result['excess_annual']=item['metrics']['annual_excess_return']
        result['factors']=[dict(spec,label=cfg['factor_catalog'][spec['name']]['label']) for spec in meta['factor_list']]
        result['research_audit']=dict(policy=policy,data_audit=audit,target_pass=item['target_pass'],round_id=policy['round_id'],
            formal_acceptance=False,reason='缺历史ST/退市全集；本轮收益回撤联合目标'+('已达到数值门槛' if item['target_pass'] else '未达到'),
            reproduction=policy.get('reproduction_command','python scripts/mainboard_strategy_research.py')+'；普通页面直接重跑未必包含研究脚本的历史长度/流动性/选股机制。')
        result['warnings'][:0]=[
            '平台五级等级不等于收益/回撤联合目标通过；本轮目标允许绝对年化20%或相对指数年化超额20个百分点，最大回撤仍按策略自身净值核验。',
            policy['benchmark']['description'],
            '历史ST列缺失，当前剔除已知ST不能保证排除历史风险警示股；不视为正式验收通过。',
            '本研究额外使用主板代码、至少252个已观察交易日、过去20日平均成交额>=2000万元过滤，复现需运行研究脚本。',
            '价格历史从2019-01-02开始，资格热身后首个可选日期为'+audit['first_eligible_date']+'；此前空仓期保留在全窗口指标中。',
        ]
        from backtest.research_report import build_report
        result['report']=build_report(result)
        runner.save_result(result)
        archives.append(dict(strategy=key,name=result['name'],archive_url=result['archive_url'],metrics=result['metrics'],target_pass=item['target_pass']))
        log('archived '+result['name'])
        del result,view,score;gc.collect()
    write_json(out/'platform_archives.json',archives)


def run(policy,out,panels,eligible,signature):
    cfg=runner.research_config()
    frozen=out/'frozen_candidates.json'
    strategies=json.loads(frozen.read_text(encoding='utf-8'))['strategies'] if frozen.exists() else factor_study(panels,eligible,policy,out)
    paths=strategy_scores(panels,eligible,strategies,out,policy)
    experiments=[]
    ledger=out/'experiments.jsonl'
    if ledger.exists():experiments=[json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines() if line]
    for item in experiments:
        dates=eligible.index[(eligible.index>=item['params']['start'])&(eligible.index<=item['params']['end'])]
        evaluate_return_target(item,dates,policy)
    done={(r['strategy'],r['variant'],r['period']) for r in experiments}
    def batch(keys,period_name,variant_map=None,stress=False):
        results=[]
        for key in keys:
            score=pd.DataFrame(np.load(paths[key],mmap_mode='r'),index=eligible.index,columns=eligible.columns)
            for original_variant in policy['variants']:
                if variant_map and original_variant['id']!=variant_map[key]:continue
                variant=dict(original_variant)
                label=period_name+('_stress' if stress else '')
                if stress:variant.update(policy['stress'])
                old=next((r for r in experiments if (r['strategy'],r['variant'],r['period'])==(key,variant['id'],label)),None)
                if old:results.append(old);continue
                started=time.monotonic()
                sim,p=execute(panels,score,policy['periods'][period_name],variant,policy)
                item=dict(strategy=key,title=strategies[key]['name'],variant=variant['id'],period=label,
                    params=p,factor_list=strategies[key]['factor_list'],snapshot_hash=signature,
                    **summarize(sim,p,policy),elapsed_s=round(time.monotonic()-started,2))
                navfile=out/'runs'/(key+'_'+variant['id']+'_'+label+'.json')
                navfile.parent.mkdir(exist_ok=True)
                write_json(navfile,dict(**item,dates=[str(d.date()) for d in sim['nav_series'].index],nav=list(sim['nav_series'].values),
                    trades=sim['trades'],daily_account=sim['daily_account'],rejected_orders=sim['rejections']))
                with ledger.open('a',encoding='utf-8') as handle:handle.write(json.dumps(item,ensure_ascii=False)+'\n')
                experiments.append(item);results.append(item)
                m=item['metrics'];log(f"{label} {key}/{variant['id']} CAGR={m['annual_return']:.2%} DD={m['max_drawdown']:.2%} holdings={item['max_holdings']} {item['elapsed_s']}s")
                del sim;gc.collect()
            del score
        return results
    discovery=batch(list(strategies),'discovery')
    ranked=sorted(discovery,key=lambda row:(row['target_pass'],row['max_holdings']<=policy['targets']['max_holdings'],row['frequency_pass'],row['metrics']['calmar']),reverse=True)
    finalists=[]
    for item in ranked:
        if item['strategy'] not in finalists:finalists.append(item['strategy'])
        if len(finalists)==policy['selection']['finalists']:break
    selection={key:next(r for r in ranked if r['strategy']==key)['variant'] for key in finalists}
    write_json(out/'frozen_selection.json',dict(finalists=finalists,variants=selection,selection_data_end=policy['periods']['discovery']['end'],validation_inspected=False,observation_blind=False))
    validation=batch(finalists,'validation',selection)
    observation=batch(finalists,'observation',selection)
    full=batch(finalists,'full',selection)
    stress=batch(finalists,'full',selection,stress=True)
    write_json(out/'results.json',dict(snapshot_hash=signature,evaluation_policy=policy['targets'],benchmark=policy['benchmark'],selection=selection,finalists=finalists,experiments=experiments,
        validation=validation,observation=observation,full=full,stress=stress,passing_full=sum(r['target_pass'] for r in full),passing_validation=sum(r['target_pass'] for r in validation),observation_blind=False))
    table=[]
    for item in experiments:
        table.append({**{k:item[k] for k in ['strategy','variant','period','target_pass','max_holdings','annual_one_way_turnover','trade_dates','average_exposure']},**item['metrics']})
    pd.DataFrame(table).to_csv(out/'results.csv',index=False,encoding='utf-8-sig')
    log('COMPLETE '+str(out/'results.json'))


def main():
    policy=read_config('mainboard_research');out=ROOT/policy['output_dir'];out.mkdir(parents=True,exist_ok=True)
    write_json(out/'policy.json',policy)
    panels,eligible,signature=load_snapshot(policy,out)
    market_state(panels,eligible,policy,out)
    rounds=[]
    for spec in policy['rounds']:
        current=deepcopy(policy);current['round_id']=spec['id']
        current['periods'].update(discovery=spec['discovery'],validation=spec['validation'])
        folder=out/spec['id'];folder.mkdir(exist_ok=True)
        write_json(folder/'policy.json',current)
        log('BEGIN '+spec['id'])
        run(current,folder,panels,eligible,signature)
        results=json.loads((folder/'results.json').read_text(encoding='utf-8'))
        archive_path=folder/'platform_archives.json'
        old_archives=json.loads(archive_path.read_text(encoding='utf-8')) if archive_path.exists() else []
        if old_archives and not (folder/'platform_archives_absolute_target.json').exists():
            write_json(folder/'platform_archives_absolute_target.json',old_archives)
        if not old_archives or not (folder/'benchmark_archive_complete.json').exists():
            archive_finalists(current,folder,panels,eligible,signature,results)
            write_json(folder/'benchmark_archive_complete.json',dict(benchmark=current['benchmark']['code'],evaluation=current['targets']))
        rounds.append(dict(id=spec['id'],discovery=spec['discovery'],validation_period=spec['validation'],
                           finalists=results['finalists'],selection=results['selection'],validation=results['validation'],
                           observation=results['observation'],full=results['full'],passing_validation=results['passing_validation']))
    write_json(out/'rolling_results.json',dict(rounds=rounds,observation_blind=False,snapshot_hash=signature))


if __name__=='__main__':main()
