"""Prespecified stock/ETF timing comparison through the existing cash ledger."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.cache import DailyCache
from factors.research_library import read_config
from backtest.research_dividend_timing import signals
from backtest.research_portfolio import simulate
from backtest.bt_report import compute_metrics


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def _date_value(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    value = str(value).replace('T00:00:00.000', '')[:10]
    return value if len(value) == 10 and value[4] == '-' else None


def _number(value, default=0.0):
    try:
        value = float(value)
        return value if np.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def load_cash_actions(cfg, code, group):
    """Load locally acquired, point-in-time company/fund actions.

    Raw prices are deliberately separated from vendor qfq prices.  The source
    files live under data/private and are never committed; this loader only
    turns acquired source rows into the stable CashActions contract.
    """
    root = ROOT / cfg.get('cash_actions_dir', 'data/private/dividend_timing')
    pure = code.split('.')[0]
    events = []
    if group == 'stock':
        source = root / f'{pure}_dividends_cninfo.json'
        if not source.exists():
            source = root / f'{pure}_dividends_sina.json'
        if not source.exists():
            raise FileNotFoundError(f'缺少公司行为文件：{source}')
        rows = json.loads(source.read_text(encoding='utf-8'))
        for row in rows:
            status = str(row.get('进度', '实施'))
            record = _date_value(row.get('股权登记日'))
            ex = _date_value(row.get('除权日') or row.get('除权除息日'))
            pay = _date_value(row.get('派息日')) or ex
            available = _date_value(row.get('实施方案公告日期') or row.get('公告日期')) or record
            if status not in ('实施', 'nan', 'None') or not (record and ex and pay):
                continue
            cash = _number(row.get('派息比例') if '派息比例' in row else row.get('派息')) / 10.0
            split = 1.0 + (_number(row.get('送股比例')) + _number(row.get('转增比例'))) / 10.0
            events.append(dict(id=f'{code}-div-{record}', code=code,
                               record_date=record, ex_date=ex, pay_date=pay,
                               available_date=available, cash_per_share=cash,
                               split_ratio=split, tax_kind='personal_stock'))
    else:
        if pure == '515450':
            source = root / '515450_events_1.json'
            for row in json.loads(source.read_text(encoding='utf-8')) if source.exists() else []:
                record = _date_value(row.get('权益登记日')); ex = _date_value(row.get('除息日'))
                pay = _date_value(row.get('分红发放日'))
                if not (record and ex and pay):
                    continue
                text_value = str(row.get('每10份分红', ''))
                import re
                matches = re.findall(r'([0-9]+(?:\.[0-9]+)?)', text_value)
                cash = _number(matches[-1] if matches else 0.0) / 10.0
                events.append(dict(id=f'{code}-div-{record}', code=code,
                                   record_date=record, ex_date=ex, pay_date=pay,
                                   available_date=record, cash_per_share=cash,
                                   split_ratio=1.0, tax_kind='fund_exempt'))
        if pure == '512890':
            source = root / '512890_events_2.json'
            rows = json.loads(source.read_text(encoding='utf-8')) if source.exists() else []
            for row in rows:
                ex = _date_value(row.get('拆分折算日'))
                if not ex:
                    continue
                # The fund notice gives the effective split day only.  The
                # prior trading day is a conservative point-in-time record day.
                prior = (pd.Timestamp(ex) - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
                events.append(dict(id=f'{code}-split-{ex}', code=code,
                                   record_date=prior, ex_date=ex, pay_date=ex,
                                   available_date=prior, cash_per_share=0.0,
                                   split_ratio=2.0, tax_kind='fund_exempt'))
    return sorted(events, key=lambda event: (event['record_date'], event['id']))


def load_data(cfg, download=False, price_basis='qfq'):
    from data.fetcher_akshare import fetch_daily
    stock_cache=DailyCache(); etf_cache=DailyCache(ROOT/cfg['etf_database'])
    frames, audit = {}, {}
    for group, spec in cfg['groups'].items():
        cache=etf_cache if group=='etf' else stock_cache
        for code in spec['assets']:
            frame=cache.get_daily(code,start=cfg['data_start'],end=cfg['data_end'],adjust=price_basis)
            if group=='etf' and (frame is None or frame.empty) and download:
                net=cfg['network']
                fetched=fetch_daily(code,cfg['data_start'],cfg['data_end'],price_basis,**net)
                if fetched.empty:
                    raise ValueError('ETF没有行情：'+code)
                cache.put_daily(code,fetched,adjust=price_basis,source='akshare')
                frame=cache.get_daily(code,start=cfg['data_start'],end=cfg['data_end'],adjust=price_basis)
            if (frame is None or frame.empty) and download:
                net=cfg['network']
                fetched=fetch_daily(code,cfg['data_start'],cfg['data_end'],price_basis,**net)
                if fetched.empty:
                    raise ValueError('行情没有数据：'+code)
                cache.put_daily(code,fetched,adjust=price_basis,source='akshare')
                frame=cache.get_daily(code,start=cfg['data_start'],end=cfg['data_end'],adjust=price_basis)
            if frame is None or frame.empty:
                raise ValueError('没有已缓存行情：'+code+'；ETF首次运行可加 --download。')
            frame=frame.sort_values('date').drop_duplicates('date').set_index('date')
            if not frame.index.is_monotonic_increasing:
                raise ValueError('日期不按序。')
            # Preserve missing bars and reject invalid OHLC, never invent fills.
            numeric=frame[['open','high','low','close']]
            invalid=((numeric.le(0).any(axis=1)) | frame['high'].lt(numeric[['open','low','close']].max(axis=1)-1e-6)
                     | frame['low'].gt(numeric[['open','high','close']].min(axis=1)+1e-6))
            if invalid.any():
                raise ValueError('行情OHLC不一致：'+code)
            frames[code]=frame
            audit[code]=dict(start=str(frame.index[0]),end=str(frame.index[-1]),rows=len(frame),
                             missing_prices=int(numeric.isna().any(axis=1).sum()),
                             hash=hashlib.sha256(pd.util.hash_pandas_object(frame,index=True).values.tobytes()).hexdigest(),
                             sources=sorted(frame['source'].dropna().unique()) if 'source' in frame else [],
                             price_basis=('raw_with_explicit_cash_actions' if price_basis == 'none'
                                          else 'vendor_qfq_proxy_not_explicit_dividend_cashflow'))
    return frames,audit


def panels_for(frame, code):
    return {field:pd.DataFrame({code:frame[field] if field in frame else np.nan},index=frame.index)
            for field in ('open','high','low','close','volume','amount','is_st')}


def summarize(sim, initial):
    ret=sim['returns']; nav=sim['nav_series']
    metrics=compute_metrics(ret)
    if not metrics:
        raise ValueError('回测没有有效交易日。')
    metrics['max_drawdown']=float((nav/np.maximum.accumulate(np.r_[1.,nav.to_numpy()])[1:]-1).min())
    metrics['calmar']=metrics['annual_return']/abs(metrics['max_drawdown']) if metrics['max_drawdown'] < 0 else 0.
    metrics['average_exposure']=float(np.mean([r['exposure'] or 0. for r in sim['daily_account']]))
    metrics['trades']=len(sim['trades'])
    metrics['annual_one_way_turnover']=sum(t['amount'] for t in sim['trades'])/initial/2/(len(ret)/242.)
    metrics['yearly_returns']={str(y):float((1+v).prod()-1) for y,v in ret.groupby(ret.index.year)}
    return metrics


def run(frame, code, group, variant, period, cfg, stress=False, ledger=False,
        price_basis='qfq', cash_actions=None):
    p={**deepcopy(cfg['execution']),**period, 'stamp_tax_bps':cfg['groups'][group]['stamp_tax_bps']}
    if stress:
        p.update(cfg['stress'])
    panels=panels_for(frame,code)
    if price_basis == 'none':
        panels['_execution_price_basis'] = 'raw'
        panels['_cash_actions'] = list(cash_actions or [])
    # Only fully warmed dates may enter the timing study; the buy/hold control
    # for each asset uses the same maximum-grid warmup boundary.
    warmed=panels['close'].rolling(max(cfg['candidate_grid']['slow'])).count().eq(max(cfg['candidate_grid']['slow']))
    valid_dates=warmed.index[warmed.iloc[:,0] & (warmed.index>=period['start']) & (warmed.index<=period['end'])]
    if not len(valid_dates):
        raise ValueError('没有足够预热行情：'+code)
    p['start']=str(valid_dates[0])
    score=panels['close']*0+1.
    if variant['id']=='buy_hold':
        # Entry once; no discretionary exit or forced liquidation at year end.
        enter=panels['close'].notna().astype(float)
        parts=[(1.,dict(_entry=enter,_exit=enter*0))]
    else:
        adapters,_=signals(panels,variant['slow'],variant['band'],cfg['candidate_grid']['confirm_days'])
        core=cfg['groups'][group]['core_fraction']
        parts=[]
        if core:
            enter=panels['close'].notna().astype(float)
            parts.append((core,dict(_entry=enter,_exit=enter*0)))
        parts.append((1-core,adapters))
    sims=[]
    for fraction,adapter in parts:
        view={**panels,**adapter}; params={**p,'initial_capital':p['initial_capital']*fraction}
        sims.append(simulate(view,score,params))
    # Independent sleeves use actual capital/fees and never rebalance capital
    # between sleeves. Aggregate equity, not a synthetic mean of daily returns.
    accounts=[]
    for i,day in enumerate(sims[0]['daily_account']):
        rows=[s['daily_account'][i] for s in sims]
        equity=sum(r['equity'] for r in rows); mv=sum(r['market_value'] for r in rows)
        accounts.append(dict(date=day['date'],equity=equity,cash=sum(r['cash'] for r in rows),market_value=mv,
                             dividend_receivable=sum(r.get('dividend_receivable', 0.) for r in rows),
                             exposure=mv/equity if equity else 0.))
    nav=pd.Series([r['equity']/p['initial_capital'] for r in accounts],index=sims[0]['nav_series'].index)
    ret=nav.pct_change(fill_method=None);ret.iloc[0]=nav.iloc[0]-1
    sim=dict(returns=ret,nav_series=nav,daily_account=accounts,
             trades=[dict(t,sleeve=i) for i,s in enumerate(sims) for t in s['trades']],
             rejections=[r for s in sims for r in s['rejections']],
             cash_distributions=dict(
                 gross_dividends=sum(s.get('cash_distributions', {}).get('gross_dividends', 0.) for s in sims),
                 dividend_tax_paid=sum(s.get('cash_distributions', {}).get('dividend_tax_paid', 0.) for s in sims),
                 net_dividends=sum(s.get('cash_distributions', {}).get('net_dividends', 0.) for s in sims),
                 receivable=sum(s.get('cash_distributions', {}).get('receivable', 0.) for s in sims),
                 conditional_tax_if_sold=sum(s.get('cash_distributions', {}).get('conditional_tax_if_sold', 0.) for s in sims),
                 price_basis='raw' if price_basis == 'none' else 'unchanged',
                 events=[dict(event, sleeve=i) for i,s in enumerate(sims)
                         for event in s.get('cash_distributions', {}).get('events', [])]))
    metrics=summarize(sim,p['initial_capital'])
    result=dict(code=code,group=group,variant=variant,stress=stress,period=period,
                actual_start=p['start'],metrics=metrics,params=p,
                cash_distributions=sim['cash_distributions'])
    if ledger:
        result.update(daily_account=accounts,returns=[dict(date=str(d)[:10],ret=float(v)) for d,v in ret.items()],
                      trades=sim['trades'],rejections=sim['rejections'],
                      cash_distributions=sim['cash_distributions'])
    return result


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--download',action='store_true')
    parser.add_argument('--cashflow',action='store_true', help='使用未复权价格和现金分红账本')
    args=parser.parse_args();cfg=read_config('dividend_timing_research');out=ROOT/cfg['output_dir']
    price_basis='none' if args.cashflow else 'qfq'
    if args.cashflow:
        out=ROOT/cfg.get('cashflow_output_dir','output/strategy_research/dividend_timing_cashflow_v1')
    variants=[dict(id=f'ma{n}_b{int(b*100)}',slow=n,band=b) for n in cfg['candidate_grid']['slow'] for b in cfg['candidate_grid']['band']]
    frames,audit=load_data(cfg,args.download,price_basis)
    actions=({code:load_cash_actions(cfg,code,group) for group,spec in cfg['groups'].items() for code in spec['assets']}
             if args.cashflow else {code:[] for spec in cfg['groups'].values() for code in spec['assets']})
    write(out/'manifest.json',dict(config=cfg,candidates=variants,data=audit,
          source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                         ['scripts/dividend_timing_research.py','backtest/research_dividend_timing.py','backtest/research_portfolio.py']},
          evaluation='historically_observed_chronological_replay_not_blind',price_basis=price_basis,
          cash_action_counts={code:len(value) for code,value in actions.items()}))
    rows=[];selected={};ranking={}
    for group,spec in cfg['groups'].items():
        candidates=[]
        for v in variants:
            values=[]
            for code in spec['assets']:
                for stress in (False,True):
                    row=run(frames[code],code,group,v,cfg['periods']['discovery'],cfg,stress,
                            price_basis=price_basis,cash_actions=actions[code])
                    rows.append(row)
                    if row['metrics']['n_days']>=cfg['selection']['min_days']:
                        values.append(row['metrics'])
            if not values:
                continue
            median=lambda key:float(np.median([r[key] for r in values]))
            record=dict(variant=v,median_calmar=median('calmar'),median_annual=median('annual_return'),
                        worst_drawdown=min(r['max_drawdown'] for r in values),samples=len(values))
            record['drawdown_pass']=record['worst_drawdown']>=cfg['selection']['max_drawdown']
            candidates.append(record)
        candidates.sort(key=lambda r:(r['median_annual']>0,r['drawdown_pass'],r['median_calmar'],r['median_annual']),reverse=True)
        selected[group]=candidates[0]['variant'];ranking[group]=candidates
        print('FROZEN',group,selected[group],flush=True)
        write(out/'frozen_selection.json',dict(selected=selected,ranking=ranking,selection_uses_validation=False))
        for code in spec['assets']:
            for phase,period in cfg['periods'].items():
                for v in [dict(id='buy_hold'),selected[group]]:
                    for stress in (False,True):
                        row=run(frames[code],code,group,v,period,cfg,stress,ledger=True,
                                price_basis=price_basis,cash_actions=actions[code])
                        row['phase']=phase
                        path=out/'runs'/f"{code}_{v['id']}_{phase}{'_stress' if stress else ''}.json"
                        write(path,row)
                        rows.append({k:v for k,v in row.items() if k not in ('daily_account','returns','trades','rejections')})
        print('DONE',group,flush=True)
    write(out/'results.json',dict(selected=selected,records=rows,data=audit))
    print(json.dumps(dict(ok=True,selected=selected,records=len(rows)),ensure_ascii=False))


if __name__=='__main__':main()
