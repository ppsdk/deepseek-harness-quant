"""Real portfolio ledgers selected in discovery and frozen before validation."""
from copy import deepcopy
import gc
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from backtest import research_runner as runner
from backtest.bt_report import compute_metrics
from backtest.research_timing import evaluate
from backtest.research_factor_search import raw_parameter,measure,restrict_targets
from scripts.mainboard_repair_research import load_cached_snapshot,canonical_hash
from scripts.mainboard_strategy_research import write_json,log,benchmark_snapshot
from scripts.four_axis_factor_mining import valuation_panels,labels,hac
from scripts.four_axis_factor_audit import pca_summary,representatives
from data.cache import DailyCache

ROOT=Path(__file__).resolve().parents[1]


def assess(sim,p,cfg):
    m=compute_metrics(sim['returns']);nav=sim['nav_series'].to_numpy()
    m['max_drawdown']=float((nav/np.maximum.accumulate(np.r_[1.,nav])[1:]-1).min())
    accounts=pd.DataFrame(sim['daily_account']);trades=pd.DataFrame(sim['trades'])
    years=(pd.Timestamp(accounts.date.iloc[-1])-pd.Timestamp(accounts.date.iloc[0])).days/365.25
    days=list(accounts.date);positions={d:i for i,d in enumerate(days)}
    trade_dates=sorted(trades.date.unique()) if len(trades) else []
    ordinary=set(trades.loc[trades.trigger.ne('market_risk_exit'),'date']) if len(trades) else set()
    gaps=[positions[d]-positions[a] for a,d in zip(trade_dates,trade_dates[1:]) if d in ordinary]
    turnover=float(trades.amount.sum()/accounts.equity.mean()/years/2) if len(trades) else 0.
    top=cfg['portfolio'];exposure=float(accounts.exposure.mean());max_hold=int(accounts.holding_count.max())
    max_codes=int(trades.groupby('date').code.nunique().max()) if len(trades) else 0
    frequency=all(g>=top['min_trade_interval_days'] for g in gaps)
    enough_days=len(days)>=top['min_conclusion_days']
    checks=dict(drawdown=m['max_drawdown']>=top['max_drawdown'],turnover=turnover<=top['max_annual_one_way_turnover'],
        frequency=frequency,holdings=max_hold<=top['primary_topn'],traded_codes=max_codes<=top['max_traded_codes_per_day'],
        exposure=exposure>=top['min_average_exposure'],activity=len(trade_dates)>=top['min_trade_dates'],positive_return=m['annual_return']>0,
        sample_length=enough_days)
    bench=benchmark_snapshot(p['timing_index'],tuple(days));m['benchmark_annual_return']=bench['metrics']['annual_return'];m['annual_excess_return']=m['annual_return']-m['benchmark_annual_return']
    return dict(metrics=m,annual_one_way_turnover=turnover,average_exposure=exposure,max_holdings=max_hold,max_traded_codes=max_codes,
        minimum_regular_after_any_gap=min(gaps) if gaps else None,trade_dates=len(trade_dates),trade_count=len(trades),
        emergency_exit_dates=sorted(trades.loc[trades.trigger.eq('market_risk_exit'),'date'].unique()) if len(trades) else [],
        costs={k:float(trades[k].sum()) if len(trades) else 0. for k in ['commission','stamp_tax','slippage_cost']},
        stale_valuation_days=sim['stale_valuation_days'],checks=checks,constraints_pass=all(checks.values()),
        operational_risk_pass=all(checks[k] for k in ['drawdown','turnover','frequency','holdings','traded_codes']),
        verdict=runner._grade_verdict(m) if enough_days else '样本不足252交易日，不升级正向结论',
        diagnostic_only=p['topn']!=top['primary_topn'])


def discovery_ranking(row):
    c=row['checks'];m=row['metrics']
    viable=c['exposure'] and c['activity'] and c['positive_return']
    return (row['constraints_pass'],viable,c['drawdown'],m['sharpe'],m['annual_return'],-row['annual_one_way_turnover'])


def training_preprocessing(selected,raws,eligible,common_codes,cfg,window,validation,out):
    settings=cfg['preprocessing'];dates=eligible.index
    pos=np.flatnonzero((dates>=window['start'])&(dates<=window['end']))[::settings['sample_every_days']]
    mask=eligible[common_codes].iloc[pos];flat=np.flatnonzero(mask.to_numpy().ravel())
    if len(flat)>settings['max_samples']:flat=flat[np.linspace(0,len(flat)-1,settings['max_samples'],dtype=int)]
    sample={}
    for row in selected:
        rank=raws[row['id']][common_codes].iloc[pos].where(mask).rank(axis=1,pct=True)
        sample[row['family_id']]=rank.to_numpy().ravel()[flat]
    frame=pd.DataFrame(sample).dropna();corr=frame.corr();corr.to_csv(out/'training_factor_correlations.csv',encoding='utf-8-sig')
    ordered=[dict(id=r['family_id'],category=r['category'],effective=True) for r in selected]
    chosen=representatives(ordered,corr,settings['max_abs_correlation'],list(dict.fromkeys(r['category'] for r in selected)))
    audit=dict(fit_window=window,selection_uses_validation=False,all_families=list(frame),selected_families=chosen,complete_rows=len(frame),
        max_abs_corr=settings['max_abs_correlation'],pca=pca_summary(frame[chosen],settings['pca_explained_variance']),
        purpose='training-only descriptive factor audit; no PCA return model or alpha weights trained')
    train=frame[chosen];mean=train.mean().to_numpy();scale=train.std(ddof=0).to_numpy()
    audit['scaler_state']={'mean_':mean.tolist(),'scale_':scale.tolist()}
    vpos=np.flatnonzero((dates>=validation['start'])&(dates<=validation['end']))[::settings['sample_every_days']]
    validation_columns={}
    for row in selected:
        if row['family_id'] not in chosen:continue
        rank=raws[row['id']][common_codes].iloc[vpos].where(eligible[common_codes].iloc[vpos]).rank(axis=1,pct=True)
        validation_columns[row['family_id']]=rank.to_numpy().ravel()
    validation_frame=pd.DataFrame(validation_columns)[chosen].dropna()
    if len(validation_frame)>settings['max_samples']:validation_frame=validation_frame.iloc[np.linspace(0,len(validation_frame)-1,settings['max_samples'],dtype=int)]
    if len(validation_frame)>2:
        vc=validation_frame.corr().to_numpy();components=np.array([list(audit['pca']['loadings'][k].values()) for k in audit['pca']['loadings']])
        pcs=((validation_frame.to_numpy()-mean)/scale)@components.T
        pcorr=np.atleast_2d(np.corrcoef(pcs,rowvar=False))
        audit['validation']=dict(window=validation,samples=len(validation_frame),refit=False,
            max_abs_input_correlation=float(np.max(np.abs(vc-np.eye(len(chosen))))),
            max_abs_pc_correlation=float(np.max(np.abs(pcorr-np.eye(pcs.shape[1])))))
    write_json(out/'training_preprocessing.json',audit)


def exposure_control_audit(raw,eligible,targets,window,panels,base,cfg,direction):
    """Observed-X-only daily residualization, not industry/market-cap neutrality."""
    h=cfg['search']['primary_horizon'];restricted=restrict_targets(targets,raw.index,window)
    pos,future,executable=restricted[h];codes=raw.columns
    amount=panels['amount'][codes].rolling(base['universe']['liquidity_window']).mean()
    from factors.research_controls import policy as controls
    n=controls()['legacy_indicators']['lowvol_60']['params']['n']
    volatility=panels['close'][codes].pct_change(fill_method=None).rolling(n).std()
    controls_frames=[amount,volatility,panels['close'][codes]];values=[]
    for i,day in enumerate(raw.index[pos]):
        known=eligible.loc[day,codes]&raw.loc[day].notna()
        for frame in controls_frames:known &= frame.loc[day].notna()
        if int(known.sum())<base['ic']['min_stocks']:continue
        observed=raw.loc[day,known].rank(pct=True)
        x=np.column_stack([np.ones(len(observed)),*[frame.loc[day,known].rank(pct=True).to_numpy() for frame in controls_frames]])
        residual=observed.to_numpy()-x@np.linalg.lstsq(x,observed.to_numpy(),rcond=None)[0]
        y=future.loc[day,known].where(executable[i,known.to_numpy()])
        good=y.notna()
        if good.sum()<base['ic']['min_stocks'] or np.std(residual[good])<1e-10:continue
        v=pd.Series(residual,index=observed.index)[good].rank().corr(y[good].rank())
        if np.isfinite(v):values.append(v*direction)
    stats=hac(values,math.ceil((h+1)/base['ic']['sample_every_days'])-1)
    return dict(controls=['rolling_average_amount','realized_volatility','share_price'],amount_window=base['universe']['liquidity_window'],volatility_window=n,
        signed_rank_ic_mean=float(np.mean(values)) if values else None,observations=len(values),hac_t=stats['t'],
        industry_neutralized=False,market_cap_neutralized=False,selection_uses_this_result=False)


def run(cfg,base,out):
    panels,eligible,signature,_=load_cached_snapshot(base);values,codes=valuation_panels(panels,base)
    policy=read_execution(cfg);timing={};daily=DailyCache()
    for rule in cfg['portfolio']['timing_rules']:
        if rule=='none':continue
        frame=daily.get_daily(policy['execution']['timing_index'],adjust='none').set_index('date').sort_index()
        timing[rule]=evaluate(frame,rule,dict(benchmark=policy['benchmark'],rules=policy['timing_rules'])).reindex(eligible.index)
    results=[];frozen_rounds=[]
    for round_number,rd in enumerate(cfg['rounds']):
        folder=out/rd['id'];runs=folder/'runs';runs.mkdir(exist_ok=True)
        frozen=json.loads((folder/'frozen_parameters.json').read_text(encoding='utf-8'))
        manifest=json.loads((out/'experiment_manifest.json').read_text(encoding='utf-8'))
        if frozen['identity']!=manifest['identity'] or manifest['snapshot_hash']!=signature:raise ValueError('Frozen study identity mismatch')
        scores={};raws={};training=[]
        def get_raw(row):
            if row['id'] not in raws:
                view=values if row['category']=='valuation' else panels
                raws[row['id']]=raw_parameter(row,view)
            return raws[row['id']]
        def ledger(row,period,phase,topn,cadence,rule,stress=False):
            ident=f"{row['id']}__N{topn}_R{cadence}_{rule}_{phase}"+('_stress' if stress else '')
            path=runs/(ident+'.json')
            stamp=canonical_hash(dict(identity=frozen['identity'],row=row['id'],params=row['definition'],period=period,topn=topn,cadence=cadence,rule=rule,stress=stress))
            if path.exists():
                old=json.loads(path.read_text(encoding='utf-8'))
                if old.get('ledger_identity')==stamp:
                    if 'operational_risk_pass' not in old:
                        old['operational_risk_pass']=all(old['checks'][k] for k in ['drawdown','turnover','frequency','holdings','traded_codes'])
                        write_json(path,old)
                    result={k:v for k,v in old.items() if k not in ('nav','dates','trades','daily_account','rejected_orders')};results.append(result);return result
            raw=get_raw(row);view={k:v.reindex(columns=raw.columns) for k,v in panels.items()};mask=eligible[raw.columns]
            score=(raw*row['direction']).where(mask)
            if rule!='none':
                gate=timing[rule];view['_timing']=pd.DataFrame(np.broadcast_to(gate.to_numpy()[:,None],raw.shape),index=raw.index,columns=raw.columns)
                score=score.where(gate.eq(1),axis=0)
            view['_selection_policy']={'retain_rank':int(math.ceil(topn*cfg['portfolio']['candidate_buffer']))}
            p={**policy['execution'],**period,**{k:cfg['portfolio'][k] for k in ['commission_bps','stamp_tax_bps','slippage_bps','target_exposure_pct','min_trade_interval_days']},
               'topn':topn,'rebalance_days':cadence,'timing_rule':rule,'max_position_pct':100/topn,
               'max_traded_codes_per_day':cfg['portfolio']['max_traded_codes_per_day'] if topn==cfg['portfolio']['primary_topn'] else 2*topn}
            if stress:p.update(cfg['stress'])
            sim=runner.simulate(view,score,p);result=dict(id=ident,ledger_identity=stamp,round_id=rd['id'],family_id=row['family_id'],label=row['label'],category=row['category'],
                parameter_id=row['id'],parameter=row['parameter'],value=row['value'],is_default=row['is_default'],direction=row['direction'],phase=phase,period=period,
                topn=topn,cadence=cadence,timing_rule=rule,stress=stress,params=p,archived=True,**assess(sim,p,cfg))
            write_json(path,dict(**result,dates=list(sim['nav_series'].index.strftime('%Y-%m-%d')),nav=list(sim['nav_series'].values),
                trades=sim['trades'],daily_account=sim['daily_account'],rejected_orders=sim['rejections']))
            results.append(result);m=result['metrics']
            log(f"LEDGER {rd['id']} {ident}: CAGR={m['annual_return']:.2%} DD={m['max_drawdown']:.2%} TURN={result['annual_one_way_turnover']:.2f}")
            return result
        # All discovery variants run before any validation ledger is constructed.
        for family in frozen['finalists']:
            choices={r['id']:r for r in [*family['parameters'],family['default']]}
            for row in choices.values():
                for cadence in cfg['portfolio']['rebalance_days']:
                    for topn in cfg['portfolio']['top_counts']:
                        rules=cfg['portfolio']['timing_rules'] if topn==cfg['portfolio']['primary_topn'] else ['none']
                        for rule in rules:training.append(ledger(row,rd['discovery'],'discovery',topn,cadence,rule))
        chosen=[]
        for family in frozen['finalists']:
            pool=[r for r in training if r['family_id']==family['family_id'] and not r['diagnostic_only']]
            winner=max(pool,key=discovery_ranking)
            row=next(r for r in [*family['parameters'],family['default']] if r['id']==winner['parameter_id'])
            chosen.append(dict(family=family['family_id'],row=row,topn=winner['topn'],cadence=winner['cadence'],timing_rule=winner['timing_rule'],discovery_ledger=winner['id']))
        contract=dict(round=rd,identity=frozen['identity'],selection_uses_validation=False,validation_is_fresh_blind=False,chosen=chosen)
        write_json(folder/'frozen_portfolios.json',contract)
        # Cached ledgers do not materialize their source matrices on resume.
        # Rebuild only the frozen winners needed by subsequent diagnostics.
        for selection in chosen:get_raw(selection['row'])
        training_preprocessing([r['row'] for r in chosen],raws,eligible,codes,cfg,rd['discovery'],rd['validation'],folder)
        validation_factors=[]
        for selection in chosen:
            row=selection['row'];family=next(f for f in frozen['finalists'] if f['family_id']==row['family_id'])
            view=values if row['category']=='valuation' else panels;mask=eligible[view['close'].columns]
            factor_targets=labels(view,base)
            metrics=measure(get_raw(row),mask,factor_targets,rd['validation'],base,cfg,direction=row['direction'])
            metrics['exposure_control']=exposure_control_audit(get_raw(row),mask,factor_targets,rd['validation'],panels,base,cfg,row['direction'])
            signed=metrics['horizons'][str(cfg['search']['primary_horizon'])]['rank_ic_mean']
            validation_factors.append(dict(parameter_id=row['id'],training_direction=row['direction'],validation_signed_ic=None if signed is None else signed*row['direction'],**metrics))
            for topn in cfg['portfolio']['top_counts']:
                for stress in [False,True]:ledger(row,rd['validation'],'validation',topn,selection['cadence'],selection['timing_rule'],stress)
            for stress in [False,True]:ledger(family['default'],rd['validation'],'default_control',cfg['portfolio']['primary_topn'],selection['cadence'],selection['timing_rule'],stress)
            # Post-selection audit of every earlier frozen portfolio, not just
            # its best validation performer. It never feeds discovery ranking.
            for later in cfg['rounds'][round_number+1:]:
                for stress in [False,True]:ledger(row,later['validation'],'follow_through_'+later['id'],cfg['portfolio']['primary_topn'],selection['cadence'],selection['timing_rule'],stress)
            # Keep positions and cash continuous across calendar boundaries;
            # separate annual resets cannot establish a multi-year drawdown.
            continuous=dict(start=rd['validation']['start'],end=cfg['observation']['end'])
            for stress in [False,True]:ledger(row,continuous,'continuous_forward',cfg['portfolio']['primary_topn'],selection['cadence'],selection['timing_rule'],stress)
            if round_number==len(cfg['rounds'])-1:
                for stress in [False,True]:ledger(row,cfg['observation'],'observation',cfg['portfolio']['primary_topn'],selection['cadence'],selection['timing_rule'],stress)
        write_json(folder/'validation_factors.json',dict(selection_uses_validation=False,records=validation_factors))
        frozen_rounds.append(contract);del scores,raws;gc.collect()
    write_json(out/'portfolio_results.json',dict(snapshot_hash=signature,selection_uses_validation=False,validation_is_fresh_blind=False,rounds=frozen_rounds,records=results))
    pd.DataFrame([dict(id=r['id'],round_id=r['round_id'],family_id=r['family_id'],parameter=r['parameter'],value=r['value'],phase=r['phase'],topn=r['topn'],cadence=r['cadence'],timing=r['timing_rule'],stress=r['stress'],
        **r['metrics'],turnover=r['annual_one_way_turnover'],exposure=r['average_exposure'],constraints_pass=r['constraints_pass'],diagnostic_only=r['diagnostic_only']) for r in results]).to_csv(out/'portfolio_results.csv',index=False,encoding='utf-8-sig')


def read_execution(cfg):
    from factors.research_library import read_config
    return read_config(cfg['execution_config'])
