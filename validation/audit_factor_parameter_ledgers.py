"""Independent audit against frozen source opens, trade arithmetic and boundaries."""
import json
import argparse
from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from scripts.mainboard_strategy_research import write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--budget',action='store_true');parser.add_argument('--assembly');args=parser.parse_args()
    if args.budget and args.assembly:parser.error('Choose budget or assembly audit')
    cfg=read_config('factor_parameter_research');base=read_config(cfg['base_config']);out=ROOT/cfg['output_dir']
    training_out=out
    if args.budget:out=ROOT/read_config('factor_parameter_budget')['output_dir']
    if args.assembly:out=ROOT/read_config(args.assembly)['output_dir']
    snapshot=json.loads((ROOT/base['snapshot_dir']/'cache/snapshot.json').read_text(encoding='utf-8'))
    arrays={k:np.load(ROOT/base['snapshot_dir']/'cache'/(k+'.npy'),mmap_mode='r') for k in ['open','high','low','volume']}
    dates={d:i for i,d in enumerate(snapshot['dates'])};codes={c:i for i,c in enumerate(snapshot['codes'])}
    checked=0;trade_count=0
    for file in sorted(out.glob('runs/*.json' if args.budget else 'round_*/runs/*.json')):
        r=json.loads(file.read_text(encoding='utf-8'));p=r['params'];trades=r['trades'];account=r['daily_account']
        seen={};ordinary={};all_trade_dates=set()
        for t in trades:
            day=t['date'];code=t['code'];i,j=dates[day],codes[code]
            assert t['signal_date']<day,(file,t)
            assert np.isfinite(arrays['volume'][i,j]) and arrays['volume'][i,j]>0
            assert not np.isclose(arrays['high'][i,j],arrays['low'][i,j],rtol=1e-8,atol=1e-8)
            np.testing.assert_allclose(t['open'],arrays['open'][i,j],rtol=1e-12)
            side=1 if t['side']=='buy' else -1
            np.testing.assert_allclose(t['fill_price'],t['open']*(1+side*p['slippage_bps']/10000),rtol=1e-12)
            np.testing.assert_allclose(t['amount'],t['quantity']*t['fill_price'],rtol=1e-12)
            np.testing.assert_allclose(t['commission'],max(p['min_commission'],t['amount']*p['commission_bps']/10000),rtol=1e-12)
            np.testing.assert_allclose(t['stamp_tax'],0 if side==1 else t['amount']*p['stamp_tax_bps']/10000,rtol=1e-12)
            np.testing.assert_allclose(t['position_after'],max(0,t['position_before']+side*t['quantity']),atol=1e-7)
            seen.setdefault(day,set()).add(code);all_trade_dates.add(day)
            if t['trigger']!='market_risk_exit':ordinary[day]=True
        assert all(len(v)<=p['max_traded_codes_per_day'] for v in seen.values())
        ordered=sorted(all_trade_dates)
        assert all(dates[b]-dates[a]>=p['min_trade_interval_days'] for a,b in zip(ordered,ordered[1:]) if b in ordinary)
        for a,nav in zip(account,r['nav']):
            np.testing.assert_allclose(a['equity'],a['cash']+a['market_value'],rtol=1e-12)
            np.testing.assert_allclose(nav,a['equity']/p['initial_capital'],rtol=1e-12)
            assert a['holding_count']<=p['topn']
        trade_count+=len(trades);checked+=1
    points=0
    for rd in cfg['rounds']:
        scan=json.loads((training_out/rd['id']/'training_scan.json').read_text(encoding='utf-8'))
        for record in scan['records']:
            for last in record['last_label_exit'].values():assert last is None or last<=rd['discovery']['end']
            points+=1
        preprocess=json.loads((training_out/rd['id']/'training_preprocessing.json').read_text(encoding='utf-8'))
        assert preprocess['pca']['cumulative_explained_variance']>=cfg['preprocessing']['pca_explained_variance']
        corr=pd.read_csv(training_out/rd['id']/'training_factor_correlations.csv',index_col=0)
        selected=preprocess['selected_families'];matrix=corr.loc[selected,selected].to_numpy()
        assert np.max(np.abs(matrix-np.eye(len(selected))))<=cfg['preprocessing']['max_abs_correlation']+1e-12
        if args.assembly:
            acfg=read_config(args.assembly);features=json.loads((out/rd['id']/'frozen_features.json').read_text(encoding='utf-8'))
            for a in features['audits'].values():
                assert a['max_abs_factor_correlation']<=acfg['max_abs_factor_correlation']+1e-12
                assert a['pca']['cumulative_explained_variance']>=acfg['pca_explained_variance']
    report=dict(ok=True,ledgers=checked,trades=trade_count,training_parameter_records=points,checks=['source open prices','prior-day signals',
        'one-price and volume filters','fill arithmetic and all costs','ordinary spacing and daily symbol caps','holdings and account balances',
        'training label exits stay inside discovery','training correlation cap and PCA variance target'])
    write_json(out/'independent_ledger_audit.json',report);print(report)


if __name__=='__main__':main()
