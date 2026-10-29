"""Independent price/cash reconstruction of every classical-rule study ledger."""
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from factors.research_library import read_config


def read(p):return json.loads(p.read_text(encoding='utf-8'))


def audit(cfg):
    base=read_config(cfg['base_config']);out=ROOT/cfg['output_dir'];source=ROOT/base['snapshot_dir']/'cache'
    manifest=read(out/'experiment_manifest.json');snapshot=read(source/'snapshot.json')
    assert snapshot['signature']==manifest['snapshot_hash']
    assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in manifest['sources'].items())
    arrays={n:np.load(source/(n+'.npy'),mmap_mode='r') for n in ['open','close','high','low','volume']}
    dates={d:i for i,d in enumerate(snapshot['dates'])};codes={c:i for i,c in enumerate(snapshot['codes'])}
    count=0;orders=0;max_holdings=0
    for file in sorted((out/'runs').glob('*.json')):
        r=read(file);assert r['experiment_identity']==manifest['identity'];p=r['params']
        shares={};marks={};cash=p['initial_capital'];events={};normal=set()
        for t in r['trades']:
            events.setdefault(t['date'],[]).append(t)
            if t['trigger']!='market_risk_exit':normal.add(t['date'])
            assert t['signal_date']<t['date']
            i,j=dates[t['date']],codes[t['code']]
            assert arrays['volume'][i,j]>0 and not np.isclose(arrays['high'][i,j],arrays['low'][i,j],rtol=1e-8,atol=1e-8)
            np.testing.assert_allclose(t['open'],arrays['open'][i,j],rtol=1e-12)
            sign=1 if t['side']=='buy' else -1
            np.testing.assert_allclose(t['fill_price'],t['open']*(1+sign*p['slippage_bps']/10000),rtol=1e-12)
            np.testing.assert_allclose(t['amount'],t['quantity']*t['fill_price'],rtol=1e-12)
            np.testing.assert_allclose(t['commission'],max(p['min_commission'],t['amount']*p['commission_bps']/10000),rtol=1e-12)
            np.testing.assert_allclose(t['stamp_tax'],t['amount']*p['stamp_tax_bps']/10000 if sign<0 else 0,rtol=1e-12)
        days=sorted(events)
        assert all(dates[b]-dates[a]>=p['min_trade_interval_days'] for a,b in zip(days,days[1:]) if b in normal)
        for account,nav in zip(r['daily_account'],r['nav']):
            day=account['date'];i=dates[day]
            for t in events.get(day,[]):
                code=t['code'];before=shares.get(code,0.)
                np.testing.assert_allclose(before,t['position_before'],atol=1e-6)
                after=before+(t['quantity'] if t['side']=='buy' else -t['quantity'])
                if t['side']=='buy':cash-=t['amount']+t['commission']
                else:cash+=t['amount']-t['commission']-t['stamp_tax']
                assert cash>=-1e-5
                np.testing.assert_allclose(cash,t['cash_after'],atol=1e-5)
                np.testing.assert_allclose(max(0.,after),t['position_after'],atol=1e-6)
                if after<1e-8:shares.pop(code,None)
                else:shares[code]=after
                marks[code]=t['open'];max_holdings=max(max_holdings,len(shares));assert len(shares)<=p['topn']<=10
            for code in shares:
                value=arrays['close'][i,codes[code]]
                if np.isfinite(value) and value>0:marks[code]=value
            market=sum(q*marks[c] for c,q in shares.items())
            np.testing.assert_allclose(cash,account['cash'],atol=1e-5)
            np.testing.assert_allclose(market,account['market_value'],rtol=1e-10,atol=1e-5)
            np.testing.assert_allclose(cash+market,account['equity'],rtol=1e-10,atol=1e-5)
            np.testing.assert_allclose(nav,(cash+market)/p['initial_capital'],rtol=1e-10)
            assert len(shares)==account['holding_count']
        count+=1;orders+=len(r['trades'])
    report=dict(ok=True,ledgers=count,trades=orders,max_holdings=max_holdings,source_prices=True,cash_reconstructed=True,
        costs_recomputed=True,t_plus_1=True,ordinary_spacing=True,emergency_exception=True)
    (out/'independent_ledger_audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':print(json.dumps(audit(read_config('technical_rule_research'))))
