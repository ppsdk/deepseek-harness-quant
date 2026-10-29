"""Prespecified follow-up to the unsuccessful first classical-rule batch."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from scripts.technical_rule_research import SOURCES,read,digest,assess,archive
from scripts.mainboard_repair_research import load_cached_snapshot,canonical_hash
from scripts.mainboard_strategy_research import write_json,log
from backtest import research_runner as runner
from backtest.research_rule_strategies import components,fundamental_mask,signals,market_gate
from data.research_valuation import panels as valuation_panels,settings as valuation_settings
from data.cache import DailyCache


def main():
    cfg=read_config('technical_rule_extension');base=read_config(cfg['base_config']);out=ROOT/cfg['output_dir'];out.mkdir(parents=True,exist_ok=True)
    for name in ['runs','cache','trade_replay']:(out/name).mkdir(exist_ok=True)
    panels,eligible,signature,data_audit=load_cached_snapshot(base)
    sample=set(read(ROOT/read_config(cfg['valuation_config'])['output_dir']/'valuation_universe.json')['codes'])
    codes=[c for c in eligible.columns if c in sample];eligible=eligible[codes];panels={k:v[codes] for k,v in panels.items()}
    val_path,_=valuation_settings();val_hash=digest(val_path)
    hashes={n:digest(ROOT/n) for n in [*SOURCES,'scripts/technical_rule_extension.py']}
    identity=canonical_hash(dict(config=cfg,snapshot_hash=signature,valuation_hash=val_hash,sources=hashes))
    manifest=dict(identity=identity,config=cfg,snapshot_hash=signature,valuation_sha256=val_hash,sources=hashes,stock_codes=codes,
        evaluation_mode='post_initial_batch_exploration',fixed_grid=True,model_fitting=False,factor_scoring=False,blind_validation=False)
    file=out/'experiment_manifest.json'
    if file.exists():assert read(file)==manifest
    else:write_json(file,manifest)
    needed=sorted({n for s in cfg['screens'].values() for n in s['limits']});values=valuation_panels(eligible.index,codes,needed)
    common=components(panels,cfg['indicators'])
    for n,v in {**values,**common}.items():np.save(out/'cache'/(n+'.npy'),v.to_numpy())
    masks={n:fundamental_mask(values,eligible,s) for n,s in cfg['screens'].items()}
    frame=DailyCache().get_daily(cfg['benchmark']['code'],start=str(eligible.index[0]),end=cfg['period']['end'],adjust='none').set_index('date').reindex(eligible.index)
    gates={}
    for name,spec in cfg['markets'].items():
        gate,market=market_gate(frame,spec);gates[name]=gate
        pd.DataFrame(dict(**market,gate=gate)).to_csv(out/(name+'_market.csv'),index_label='date')
    write_json(out/'data_audit.json',dict(data_audit,sample_stocks=len(codes),current_financial_data_used_in_history=False,financial_vintage_verified=False))
    write_json(out/'signal_manifest.json',dict(dates=list(eligible.index),codes=codes))
    rows=[];pairs=[];entries=[]
    def run(key,vid,stress=False,queue='liquidity',timed=True,diagnostic=False):
        variant=cfg['variants'][vid];spec=cfg['strategies'][key]
        rid=key+'__'+vid+('__stress' if stress else '')+('__'+queue if queue!='liquidity' else '')+('__no_market_gate' if not timed else '')
        path=out/'runs'/(rid+'.json')
        if path.exists():
            raw=read(path);assert raw['experiment_identity']==identity;rows.append(raw['summary']);return raw['summary'],raw
        start=time.monotonic();priority,technical,_=signals(panels,common,eligible,masks[variant['screen']],spec,cfg['indicators'],queue)
        p=dict(cfg['execution'],**cfg['period'],target_exposure_pct=variant['exposure'])
        if stress:p.update(cfg['stress'])
        view=dict(panels,**technical)
        if timed:
            gate=gates[variant['market']];view['_timing']=pd.DataFrame({c:gate for c in codes});priority=priority.where(gate.eq(1),axis=0)
        else:p['timing_rule']='none'
        sim=runner.simulate(view,priority,p)
        row=dict(id=rid,strategy=key,variant=vid,label=spec['label']+' · '+variant['label'],stress=stress,diagnostic=diagnostic,
            queue=queue,market_gate=timed,params=p,ledger=str(path.relative_to(ROOT)),elapsed_seconds=time.monotonic()-start,**assess(sim,p,cfg))
        raw=dict(experiment_identity=identity,summary=row,params=p,dates=[str(d.date()) for d in sim['nav_series'].index],nav=list(sim['nav_series']),
            trades=sim['trades'],daily_account=sim['daily_account'],rejected_orders=sim['rejections'],final_positions=sim['position_details'])
        write_json(path,raw);rows.append(row)
        log(f"EXTENSION {rid}: CAGR={row['metrics']['annual_return']:.2%} DD={row['metrics']['max_drawdown']:.2%} pass={row['candidate_pass']}")
        return row,raw
    for key in cfg['strategies']:
        for vid,variant in cfg['variants'].items():
            b,raw=run(key,vid);s,_=run(key,vid,True);pairs.append(dict(base=b,stress=s))
            local=deepcopy(cfg);local['market']=cfg['markets'][variant['market']]
            e=archive(b,raw,local,codes,signature,identity);e.update(stress_annual_return=s['metrics']['annual_return'],stress_max_drawdown=s['metrics']['max_drawdown'])
            entries.append(e)
    ordered=sorted(pairs,key=lambda p:(not p['base']['candidate_pass'],not p['stress']['candidate_pass'],abs(p['base']['metrics']['max_drawdown']),-p['base']['metrics']['annual_return']))
    selected=[];used=set();diagnostics=[]
    for pair in ordered:
        b=pair['base']
        if b['strategy'] in used:continue
        selected.append(b);used.add(b['strategy'])
        if len(selected)>=cfg['diagnostics']['representative_count']:break
    for b in selected:
        for queue,timed in [(cfg['diagnostics']['alternative_queue'],True),('liquidity',False)]:
            d,_=run(b['strategy'],b['variant'],queue=queue,timed=timed,diagnostic=True);diagnostics.append(d)
    assert digest(val_path)==val_hash and all(digest(ROOT/n)==h for n,h in hashes.items())
    write_json(out/'results.json',dict(manifest=manifest,records=rows,pairs=pairs,diagnostics=diagnostics))
    for e in entries:e['representative']=any(b['strategy']==e['strategy'] and b['variant']==e['variant'] for b in selected)
    write_json(out/'platform_archives.json',entries)
    from validation.audit_technical_rule_ledgers import audit
    audited=audit(cfg)
    write_json(out/'platform_catalog.json',dict(ok=True,state='complete',title=cfg['title'],note=cfg['design_note'],data_note=cfg['data_note'],
        data_end=cfg['period']['end'],start=rows[0]['metrics']['start'],ledger_count=len(rows),entries=entries,acceptance=cfg['acceptance']))
    print(json.dumps(dict(ok=True,audit=audited,candidate_count=sum(p['base']['candidate_pass'] for p in pairs)),ensure_ascii=False))


if __name__=='__main__':main()
