"""Opt-in integration check against the local cache, with temporary archives/pools."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from backtest import research_runner as runner,research_ml as ml,research_signals as signals,research_pools as pools

def main():
    with TemporaryDirectory() as folder,patch.object(runner,'ARCHIVES',Path(folder)/'archives'),patch.object(pools,'DB',Path(folder)/'pools.db'),patch.object(signals,'DB',Path(folder)/'plans.db'):
        values=dict(start='2025-06-03',end='2025-08-29',codes='000001.SZ,600000.SH,600036.SH',stocks=3,topn=2,rebalance_days=20)
        result=ml.run_ml({**values,'recipe':dict(model='ridge',features=['ema_20','rsi_14'],horizon=7,train_days=60,min_train_days=30,retrain_days=20,
            preprocessing=dict(normalization='zscore',scaling='standard',reduction='pca',component_mode='count',n_components=1)),'indicator_params':{'ema_20':{'n':17}}})
        assert result['recipe']['horizon']==7 and result['training'] and result['trades']
        assert result['params']['indicator_params']['ema_20']['n']==17
        assert all(f['last_training_outcome_date']<f['prediction_from'] for f in result['training'])
        assert all(f['preprocessing']['output_dimension']==1 and f['preprocessing']['loadings'] for f in result['training'])
        candidates=signals.create_candidates(dict(pool_name='Local integration',strategy='turn_low',end='2025-08-29',codes=values['codes'],stocks=3,topn=2))
        plan=signals.save(dict(name='Parameterized local check',pool_id=candidates['pool']['id'],entry='CROSS(MA(close,7),MA(close,31))',exit='CROSS(MA(close,31),MA(close,7))'))
        checked=signals.check(dict(id=plan['id'],end='2025-09-30'))
        assert len(checked['rows'])==candidates['pool']['count']
        report=dict(ok=True,source='local cached real OHLC',ml=dict(model='ridge',horizon=7,ema_period=17,preprocessing=result['training'][-1]['preprocessing'],training_folds=len(result['training']),trades=len(result['trades']),run_id=result['run_id']),signals=dict(candidates=candidates['pool']['count'],signal_date=candidates['pool']['signal_date'],asof=checked['asof'],states=[r['state'] for r in checked['rows']]),temporary_storage=True)
    out=Path('output/workspace_repairs');out.mkdir(parents=True,exist_ok=True)
    (out/'local_integration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
