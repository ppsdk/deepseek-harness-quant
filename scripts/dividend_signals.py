"""Export the same dividend signal service consumed by the research API."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.research_dividend_signals import evaluate
from factors.research_library import read_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code')
    parser.add_argument('--profile')
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--all', action='store_true', help='Evaluate configured assets')
    args = parser.parse_args()
    if args.all and args.code:
        parser.error('--all 与 --code 不能同时使用。')
    cfg = read_config('dividend_signals')
    codes = cfg['assets'] if args.all else [args.code or cfg['default_code']]
    for code in codes:
        query = {key: value for key, value in vars(args).items() if key in ('start', 'end', 'profile') and value}
        result = evaluate({**query, 'code': code, 'history': True})
        folder = ROOT / cfg['output_dir']
        folder.mkdir(parents=True, exist_ok=True)
        # A fingerprint prevents parameter changes from replacing an earlier run.
        path = folder / f"{code}_{result['run_hash'][:12]}_{result['requested_end']}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        print(json.dumps(dict(code=code, profile=result['profile'], data_end=result['data_end'],
                             state=result['latest']['state'], quality=result['quality'],
                             annual_return=result['metrics']['annual_return'],
                             stress_annual_return=result['stress_metrics']['annual_return'],
                             max_drawdown=result['metrics']['max_drawdown'], output=str(path)), ensure_ascii=False))


if __name__ == '__main__':
    main()
