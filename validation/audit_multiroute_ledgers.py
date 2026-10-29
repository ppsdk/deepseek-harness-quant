"""Independent reconstruction of multiroute cash, holdings, fills and fit boundaries."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from scripts.mainboard_strategy_research import write_json


def audit(cfg):
    base=read_config(cfg['base_config']);out=ROOT/cfg['output_dir'];source=ROOT/base['snapshot_dir']/'cache'
    snapshot=json.loads((source/'snapshot.json').read_text(encoding='utf-8'))
    arrays={k:np.load(source/(k+'.npy'),mmap_mode='r') for k in ['open','high','low','volume','close']}
    dates={d:i for i,d in enumerate(snapshot['dates'])};codes={c:i for i,c in enumerate(snapshot['codes'])}
    results=json.loads((out/'results.json').read_text(encoding='utf-8'))
    trade_count=0;ledgers=0;max_holdings=0;fits=0
    for file in sorted(out.glob('round_*/runs/*.json')):
        r=json.loads(file.read_text(encoding='utf-8'));p=r['params'];cash=p['initial_capital'];shares={};marks={};events={};normal=set()
        for t in r['trades']:
            events.setdefault(t['date'],[]).append(t)
            if t['trigger']!='market_risk_exit':normal.add(t['date'])
            i,j=dates[t['date']],codes[t['code']]
            assert t['signal_date']<t['date'],file
            assert np.isfinite(arrays['volume'][i,j]) and arrays['volume'][i,j]>0,file
            assert not np.isclose(arrays['high'][i,j],arrays['low'][i,j],rtol=1e-8,atol=1e-8),file
            np.testing.assert_allclose(t['open'],arrays['open'][i,j],rtol=1e-12)
            side=1 if t['side']=='buy' else -1
            np.testing.assert_allclose(t['fill_price'],t['open']*(1+side*p['slippage_bps']/10000),rtol=1e-12)
            np.testing.assert_allclose(t['amount'],t['quantity']*t['fill_price'],rtol=1e-12)
            np.testing.assert_allclose(t['commission'],max(p['min_commission'],t['amount']*p['commission_bps']/10000),rtol=1e-12)
            np.testing.assert_allclose(t['stamp_tax'],0 if side==1 else t['amount']*p['stamp_tax_bps']/10000,rtol=1e-12)
        days=sorted(events)
        assert all(dates[b]-dates[a]>=p['min_trade_interval_days'] for a,b in zip(days,days[1:]) if b in normal),file
        for a,nav in zip(r['daily_account'],r['nav']):
            d=a['date'];i=dates[d];trades=events.get(d,[])
            assert len({t['code'] for t in trades})<=p['max_traded_codes_per_day'],file
            for t in trades:
                code=t['code'];before=shares.get(code,0.)
                np.testing.assert_allclose(before,t['position_before'],atol=1e-6)
                if t['side']=='buy':cash-=t['amount']+t['commission'];after=before+t['quantity']
                else:cash+=t['amount']-t['commission']-t['stamp_tax'];after=before-t['quantity']
                np.testing.assert_allclose(cash,t['cash_after'],atol=1e-5)
                np.testing.assert_allclose(max(0,after),t['position_after'],atol=1e-6)
                if after<1e-8:shares.pop(code,None)
                else:shares[code]=after
                marks[code]=t['open']
                assert len(shares)<=10,file
            for code in shares:
                value=arrays['close'][i,codes[code]]
                if np.isfinite(value) and value>0:marks[code]=value
            market=sum(q*marks[c] for c,q in shares.items())
            np.testing.assert_allclose(cash,a['cash'],atol=1e-5)
            np.testing.assert_allclose(market,a['market_value'],rtol=1e-10,atol=1e-5)
            np.testing.assert_allclose(cash+market,a['equity'],rtol=1e-10,atol=1e-5)
            np.testing.assert_allclose(nav,(cash+market)/p['initial_capital'],rtol=1e-10)
            assert len(shares)==a['holding_count'];max_holdings=max(max_holdings,len(shares))
        trade_count+=len(r['trades']);ledgers+=1
    for contract in results['contracts']:
        freeze=out/contract['round_id']/(contract['route']+'_frozen.json')
        assert hashlib.sha256(freeze.read_bytes()).hexdigest()==contract['frozen_sha256']
    for rd in cfg['rounds']:
        folder=out/rd['id']
        for file in folder.glob('*_fit.json'):
            f=json.loads(file.read_text(encoding='utf-8'))
            assert f['last_outcome_date']<=f['fit_end']<rd['validation']['start'],file
            if f['stage']=='inner':assert f['fit_end']<rd['selection']['start'],file
            assert f['cumulative_explained_variance']+1e-12>=cfg['preprocessing']['variance_ratio'],file
            assert f['correlation_filter']['max_abs_retained_correlation']<=cfg['preprocessing']['max_abs_correlation']+1e-12,file
            assert not f['validation_refit'];fits+=1
        for file in folder.glob('*_features.json'):
            f=json.loads(file.read_text(encoding='utf-8'))
            audits=f.get('audits',{'defense':f['audit']} if 'audit' in f else {})
            for a in audits.values():
                assert a['last_outcome_date']<=rd['inner_training_end']
                assert a['correlation_filter']['max_abs_retained_correlation']<=cfg['preprocessing']['max_abs_correlation']+1e-12
                assert a['cumulative_explained_variance']+1e-12>=cfg['preprocessing']['variance_ratio']
    report=dict(ok=True,ledgers=ledgers,trades=trade_count,model_fits=fits,max_holdings=max_holdings,
        frozen_contracts=len(results['contracts']),checks=['source opens and tradability','T+1 prior signals',
        'all costs and fill arithmetic','independent daily cash and source-price portfolio reconstruction',
        'max 10 holdings after every fill','ordinary spacing and emergency exception','frozen contract integrity',
        'training label cutoff','correlation cap and PCA cumulative variance'])
    write_json(out/'independent_ledger_audit.json',report);return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',default='multiroute_strategy_research');a=p.parse_args()
    print(json.dumps(audit(read_config(a.config)),ensure_ascii=False,indent=2))
