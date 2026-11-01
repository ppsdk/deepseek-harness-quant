"""Compare configured dividend rules on a common per-asset ledger window."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backtest.research_dividend_signals import evaluate
from factors.research_library import read_config


def compare(code, profiles, end=None):
    runs = [evaluate(dict(code=code, profile=profile, history=True, **({'end': end} if end else {})))
            for profile in profiles]
    common_start = max(run['actual_start'] for run in runs)
    common_end = min(run['data_end'] for run in runs)
    # Re-run the shared ledger from equal cash when warmup dates differ;
    # truncating already-invested equity would give an unmatched comparison.
    for i, run in enumerate(runs):
        if run['actual_start'] != common_start or run['data_end'] != common_end:
            runs[i] = evaluate(dict(code=code, profile=run['profile'], start=common_start,
                                    end=common_end, history=True))
    dates = [row['date'] for row in runs[0]['history']]
    for run in runs:
        if [row['date'] for row in run['history']] != dates:
            raise ValueError('候选规则的回测日期不一致。')
        if run['buy_hold_metrics'] != runs[0]['buy_hold_metrics']:
            raise ValueError('候选规则的买入持有对照不一致。')
    return runs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--end')
    args = parser.parse_args()
    cfg = read_config('dividend_signals')
    output = ROOT / cfg['output_dir']
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for code in cfg['assets']:
        for run in compare(code, cfg['comparison_profiles'], args.end):
            source = output / f"{code}_{run['profile']}_{run['run_hash'][:12]}.json"
            source.write_text(json.dumps(run, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            row = dict(code=code, profile=run['profile'], start=run['actual_start'], end=run['data_end'],
                       metrics=run['metrics'], stress=run['stress_metrics'], hold=run['buy_hold_metrics'],
                       trades=len(run['trades']), source=str(source))
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    (output/'trend_exit_comparison.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    main()
