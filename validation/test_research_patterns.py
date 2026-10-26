"""Independent event fixtures, time availability and backtest/chart contracts."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from factors import research_patterns as patterns
from factors.research_library import factor_definitions,raw_factor
from backtest import research_runner as runner
from data import research_charts as charts
from data.cache import DailyCache


def fixture(values, volume=100.):
    c=pd.DataFrame({'000001.SZ':np.asarray(values,dtype=float)},
                   index=pd.bdate_range('2019-01-01',periods=len(values)).strftime('%Y-%m-%d'))
    return dict(close=c,open=c.shift().fillna(c),high=c+.3,low=c-.3,volume=c*0+volume,
                amount=c*volume,turn=c*0+1,is_st=c*0)


class PatternTests(unittest.TestCase):
    def test_gaussian_is_one_sided_and_matches_weighted_hand_example(self):
        p=fixture([10,11,13,15,17,18]);cfg=deepcopy(patterns.config()['families']['gaussian'])
        cfg['params'].update(window=3,sigma=1.,atr_period=2,slope_bars=1)
        p['_pattern_config']={'gaussian':cfg}
        result=patterns.bundle('gaussian',p)['outputs']['center']
        expected=(13+11*np.exp(-.5)+10*np.exp(-2))/(1+np.exp(-.5)+np.exp(-2))
        self.assertAlmostEqual(result.iloc[2,0],expected)
        self.assertTrue(result.iloc[:2].isna().all().all())

    def test_all_families_keep_the_same_past_when_future_is_appended(self):
        rng=np.random.default_rng(42);p=fixture(np.exp(rng.normal(0,.018,1150).cumsum())*20)
        p['open']=p['close']*(1+rng.normal(0,.01,(1150,1)))
        p['high']=pd.DataFrame(np.maximum(p['open'],p['close'])+.4,index=p['close'].index,columns=p['close'].columns)
        p['low']=pd.DataFrame(np.minimum(p['open'],p['close'])-.4,index=p['close'].index,columns=p['close'].columns)
        p['volume']=p['volume']*rng.uniform(.5,2,(1150,1))
        short={k:v.iloc[:903].copy() for k,v in p.items()}
        for family in patterns.config()['families']:
            with self.subTest(family=family):
                full=patterns.bundle(family,p);past=patterns.bundle(family,short)
                for key,value in past['outputs'].items():pd.testing.assert_frame_equal(full['outputs'][key].iloc[:903],value)
                self.assertEqual([e for e in full['events'] if e['date']<=short['close'].index[-1]],past['events'])

    def test_pivot_is_not_available_until_right_bars_close(self):
        high=pd.DataFrame({'x':[1.,3.,1.,4.,2.,5.,3.]});low=high-1
        result=patterns.swings(high,low,1,1)
        self.assertTrue(pd.isna(result['resistance'].iloc[1,0]))
        self.assertEqual(result['resistance'].iloc[2,0],3)
        self.assertTrue(pd.isna(result['direction'].iloc[4,0]))
        self.assertEqual(result['direction'].iloc[5,0],1)
        self.assertTrue(all(p['confirmed']==p['origin']+1 for p in result['pivots']))

    def test_weekly_structure_cannot_use_thursdays_partial_week(self):
        # Include both rising and falling swings so a weekly trend must change.
        p=fixture(20+np.sin(np.arange(450)/11)*3+np.sin(np.arange(450)/100)*6)
        out=patterns.bundle('dow',p)['outputs']['week']
        dates=pd.to_datetime(out.index)
        changes=out.ne(out.shift()) & out.notna() & out.shift().notna()
        changed_dates=dates[changes.iloc[:,0].to_numpy()]
        self.assertGreater(len(changed_dates),0)
        self.assertTrue(all(d.weekday()==4 for d in changed_dates))

    def test_month_structure_waits_for_calendar_close_even_when_last_session_is_friday(self):
        dates=pd.bdate_range('2019-01-01','2019-12-31')
        prices=np.array([10,14,11,15,12,16,13,12,10,11,9,10],dtype=float)
        p=fixture(prices[dates.month-1])
        for panel in p.values():panel.index=dates.strftime('%Y-%m-%d')
        cfg=deepcopy(patterns.config()['families']['dow'])
        cfg['params'].update(left=1,right=1)
        p['_pattern_config']={'dow':cfg}
        out=patterns.bundle('dow',p)['outputs']['month']
        # The second rising low (May) is only confirmed once June has ended.
        self.assertTrue(pd.isna(out.loc['2019-06-28'].iloc[0]))
        self.assertEqual(out.loc['2019-07-01'].iloc[0],1.)
        self.assertEqual(out.loc['2019-12-02'].iloc[0],-1.)
        prefix={k:v.loc[:'2019-07-19'].copy() for k,v in p.items() if isinstance(v,pd.DataFrame)}
        prefix['_pattern_config']={'dow':cfg}
        past=patterns.bundle('dow',prefix)['outputs']['month']
        pd.testing.assert_frame_equal(out.loc[:'2019-07-19'],past)

    def test_missing_required_inputs_never_create_a_trading_signal(self):
        for family,cfg in patterns.config()['families'].items():
            for field in cfg['requires']:
                with self.subTest(family=family,field=field):
                    p=fixture(20+3*np.sin(np.arange(250)/7))
                    p[field].iloc[-1,0]=np.nan
                    out=patterns.bundle(family,p)['outputs']
                    self.assertTrue(pd.isna(out['signal'].iloc[-1,0]))
                    self.assertTrue(pd.isna(out['state'].iloc[-1,0]))

    def test_wyckoff_spring_uses_range_before_the_signal_day(self):
        p=fixture(np.full(90,10.));p['high'].loc[:]=11;p['low'].loc[:]=9
        day=p['close'].index[60]
        for key,value in dict(open=9.6,low=8.5,high=10.5,close=10.3,volume=100.).items():p[key].loc[day]=value
        result=patterns.bundle('wyckoff',p)
        spring=[e for e in result['events'] if e['event']=='spring' and e['date']==day]
        self.assertEqual(len(spring),1)
        self.assertEqual(spring[0]['support'],9.)
        self.assertEqual(result['outputs']['signal'].loc[day].iloc[0],1.)
        self.assertEqual(result['outputs']['state'].iloc[61,0],1.)

    def test_naked_k_engulfing_requires_the_prior_down_move(self):
        p=fixture(np.linspace(15,10,30));day=p['close'].index[-1];prior=p['close'].index[-2]
        p['open'].loc[prior]=10.7;p['close'].loc[prior]=10.2
        for key,value in dict(open=10.1,close=10.9,high=11.,low=10.).items():p[key].loc[day]=value
        result=patterns.bundle('naked_k',p)
        self.assertTrue(any(e['event']=='engulf_up' and e['date']==day for e in result['events']))
        self.assertEqual(result['outputs']['signal'].iloc[-1,0],1.)

    def test_volume_price_time_space_breakout_has_real_volume_confirmation(self):
        p=fixture(np.full(70,10.));day=p['close'].index[-1]
        p['close'].loc[day]=11;p['high'].loc[day]=11.2;p['volume'].loc[day]=200
        result=patterns.bundle('vpts',p)
        self.assertTrue(any(e['event']=='breakout' and e['date']==day for e in result['events']))
        self.assertEqual(result['outputs']['volume_ratio'].iloc[-1,0],2.)
        p=fixture(np.full(70,10.));p['volume'].iloc[-1]=0
        self.assertTrue(pd.isna(patterns.bundle('vpts',p)['outputs']['signal'].iloc[-1,0]))

    def test_kdj_reuses_existing_three_lines_and_has_low_cross_signals(self):
        from factors.research_indicators import calculate
        p=fixture(10+2*np.sin(np.arange(220)/4))
        r=patterns.bundle('kdj',p)
        pd.testing.assert_frame_equal(r['outputs']['k'],calculate(dict(operation='kdj_k',params={'n':9,'smooth':3}),p))
        pd.testing.assert_frame_equal(r['outputs']['j'],3*r['outputs']['k']-2*r['outputs']['d'])
        self.assertTrue(any(e['event']=='cross_up' for e in r['events']))
        self.assertTrue(any(e['event']=='cross_down' for e in r['events']))

    def test_rsi_divergence_is_confirmed_later_and_backtest_enters_next_open(self):
        p=fixture([12,10,8,10,11,12,10,9,7,9,10,11,12,12.5,13])
        osc=p['close']*0+35;osc.iloc[2]=20;osc.iloc[8]=30
        with patch('factors.research_indicators.calculate',return_value=osc):r=patterns.bundle('rsi_divergence',p)
        bullish=[e for e in r['events'] if e['event']=='bullish']
        self.assertEqual(len(bullish),1)
        self.assertEqual(bullish[0]['origin_date'],p['close'].index[8])
        self.assertEqual(bullish[0]['date'],p['close'].index[10])
        self.assertEqual(r['outputs']['signal'].iloc[8,0],0.)
        cfg=runner.research_config();params=runner.validate_params(dict(strategy='pattern_rsi_divergence',start=p['close'].index[9],end=p['close'].index[-1],stocks=1,topn=1,rebalance_days=1),cfg)
        scores=runner.factor_score(p,cfg['strategies']['pattern_rsi_divergence']['factor_list'])
        sim=runner.simulate(p,scores,params)
        self.assertEqual(sim['trades'][0]['date'],p['close'].index[11])
        self.assertEqual(sim['trades'][0]['signal_date'],p['close'].index[10])
        self.assertEqual(sim['trades'][0]['side'],'buy')

    def test_holding_lifetime_exits_without_a_one_bar_pulse_strategy(self):
        signal=pd.DataFrame({'x':[0.,1.,0.,0.,0.,1.,-1.,np.nan,0.]})
        state=patterns.holding(signal,signal.notna(),3)
        self.assertEqual(state.x.iloc[:7].tolist(),[0.,1.,1.,1.,0.,1.,0.])
        self.assertTrue(pd.isna(state.x.iloc[7]))
        self.assertEqual(state.x.iloc[8],0.)

    def test_dynamic_catalog_and_all_new_factors_have_valid_shapes(self):
        p=fixture(20+np.sin(np.arange(650)/7)*3)
        definitions=factor_definitions();cfg=runner.research_config()
        for key,item in definitions.items():
            if item['kind']=='pattern':
                with self.subTest(key=key):
                    result=raw_factor(key,p,definitions)
                    self.assertEqual(result.shape,p['close'].shape)
                    self.assertFalse(np.isinf(result.to_numpy()).any())
        for key in patterns.config()['strategies']:
            self.assertIn(key,cfg['strategies'])
            self.assertEqual(cfg['strategies'][key]['defaults']['rebalance_days'],1)

    def test_chart_api_returns_bands_grouped_kdj_and_confirmation_records(self):
        p=fixture(20+3*np.sin(np.arange(700)/7));frame=pd.DataFrame({k:v.iloc[:,0] for k,v in p.items()}).reset_index(names='date')
        with tempfile.TemporaryDirectory() as directory:
            cache=DailyCache(str(Path(directory)/'quotes.db'));cache.put_daily('000001.SZ',frame,adjust='qfq',source='synthetic')
            with patch.object(charts,'DailyCache',return_value=cache):
                for key in ('gaussian_channel','wyckoff_events','dow_structure','naked_k_events','vpts_events','kdj_signals','rsi_divergence'):
                    r=charts.chart_data('000001.SZ','合成例子',dict(start=frame.date.iloc[100],end=frame.date.iloc[-1],indicators=key))
                    self.assertTrue(r['patterns']['studies'])
                    self.assertTrue(all(e['date']>=e['origin_date'] for e in r['patterns']['events']))
                    if key=='gaussian_channel':self.assertEqual(len(r['indicators']),3)
                    if key=='kdj_signals':self.assertEqual({v['panel'] for v in r['indicators'].values()},{'kdj'})
                    if key=='dow_structure':
                        self.assertEqual({v['panel'] for v in r['indicators'].values() if not v['overlay']},
                                         {'dow_day','dow_week','dow_month'})
                        pivots=[e for e in r['patterns']['events'] if e['event'].startswith('pivot_')]
                        self.assertTrue(pivots)
                        self.assertTrue(all(e['placement']==('above' if e['event']=='pivot_high' else 'below') for e in pivots))


if __name__=='__main__':unittest.main()
