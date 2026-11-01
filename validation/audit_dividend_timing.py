"""Independently replay archived fills, cash, positions and marked equity."""
import json
from pathlib import Path
import sys

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from scripts.dividend_timing_research import load_data,write


def main():
    base=read_config('dividend_timing_research');vol=read_config('dividend_volatility_research')
    frame_cache={};ledgers=0;trade_count=0;max_error=0.
    for folder in [base['output_dir'],vol['output_dir']]:
        manifest_path=ROOT/folder/'manifest.json'
        manifest=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
        raw=manifest.get('price_basis') == 'none' or 'cashflow' in folder
        basis='none' if raw else 'qfq'
        if basis not in frame_cache:
            frame_cache[basis],_=load_data(base,price_basis=basis)
        frames=frame_cache[basis]
        for path in (ROOT/folder/'runs').glob('*.json'):
            row=json.loads(path.read_text(encoding='utf-8'));code=row['code'];p=row['params'];cash=p['initial_capital']
            by_day={}; holdings={};marks={}
            event_by_day={}
            for event in row.get('cash_distributions', {}).get('events', []):
                event_by_day.setdefault(event['date'], []).append(event)
            for trade in row['trades']:
                assert trade['signal_date']<trade['date'],path
                by_day.setdefault(trade['date'],[]).append(trade)
                assert np.isclose(trade['amount'],trade['quantity']*trade['fill_price'])
                assert np.isclose(trade['commission'],max(p['min_commission'],trade['amount']*p['commission_bps']/10000))
                expected_tax=trade['amount']*p['stamp_tax_bps']/10000 if trade['side']=='sell' else 0.
                assert np.isclose(trade['stamp_tax'],expected_tax)
            previous=p['initial_capital']
            for account,ret in zip(row['daily_account'],row['returns']):
                day=account['date']
                for trade in by_day.get(day,[]):
                    key=(trade.get('sleeve',0),trade['code'])
                    sign=1 if trade['side']=='buy' else -1
                    cash-=sign*trade['amount']+trade['commission']+trade['stamp_tax']
                    cash-=float(trade.get('dividend_tax', 0.))
                    holdings[key]=holdings.get(key,0.)+sign*trade['quantity']
                    assert holdings[key]>-1e-7
                for event in event_by_day.get(day,[]):
                    if event['type']=='cash_dividend':
                        cash+=event['amount']
                    elif event['type']=='split':
                        for key in list(holdings):
                            if key[1] == event['code'] and (event.get('sleeve') is None or key[0] == event.get('sleeve')):
                                holdings[key] *= event['ratio']
                mv=0.
                for key,qty in holdings.items():
                    if abs(qty)<1e-7:continue
                    price=frames[key[1]].at[day,'close']
                    if np.isfinite(price):marks[key]=price
                    mv+=qty*marks[key]
                receivable=float(account.get('dividend_receivable', 0.))
                equity=cash+mv+receivable
                error=abs(equity-account['equity']);max_error=max(max_error,error)
                assert error<1e-6,(path,day,error)
                assert abs(cash-account['cash'])<1e-6,(path,day,cash,account['cash'])
                assert abs(equity/previous-1-ret['ret'])<1e-9
                previous=equity
            ledgers+=1;trade_count+=len(row['trades'])
    report=dict(ok=True,ledgers=ledgers,trades=trade_count,max_equity_error=max_error,
                checks=['prior_close_next_open','cash_conservation','fee_math','stock_etf_tax_separation','marked_equity','return_reconstruction'])
    write(ROOT/vol['output_dir']/'independent_ledger_audit.json',report)
    print(json.dumps(report))


if __name__=='__main__':main()
