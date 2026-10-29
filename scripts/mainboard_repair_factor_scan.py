"""Scan causal price-volume factor RankIC inside each discovery window.

The target is the next-open-to-horizon-end-open return. Validation years are
never inspected by this script, and the output keeps per-year IC so direction
stability can be judged before a strategy is frozen.
"""
from pathlib import Path
import argparse
import gc
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from factors.research_library import factor_definitions, read_config
from scripts.mainboard_repair_research import (
    ensure_factor_array,
    load_cached_snapshot,
    load_factor,
    log,
    write_json,
)


def _mean(values):
    values = values.dropna()
    return float(values.mean()) if len(values) else None


def scan_factor(name, raw, eligible, panels, start, end, ic):
    """Return one causal RankIC record for a factor and discovery window."""
    dates = panels['close'].index
    available = dates[(dates >= start) & (dates <= end)]
    opening = panels['open']
    coverage_rows = eligible.loc[available].sum(axis=1)
    coverage = raw.loc[available].where(
        eligible.loc[available]
    ).notna().sum(axis=1).div(coverage_rows.replace(0, np.nan))
    result = dict(
        factor=name,
        window_start=str(available[0])[:10] if len(available) else None,
        window_end=str(available[-1])[:10] if len(available) else None,
        coverage=_mean(coverage),
        horizons={},
    )
    for horizon in ic['horizons']:
        horizon = int(horizon)
        selected = available[:-(horizon + 1):int(ic['sample_every_days'])]
        if not len(selected):
            continue
        future = (
            opening.shift(-(horizon + 1)).loc[selected]
            / opening.shift(-1).loc[selected]
            - 1
        )
        pair = raw.loc[selected]
        mask = eligible.loc[selected] & pair.notna() & future.notna()
        mask &= panels['volume'].shift(-1).loc[selected].gt(0)
        mask &= panels['volume'].shift(-(horizon + 1)).loc[selected].gt(0)
        mask &= panels['high'].shift(-1).loc[selected].gt(
            panels['low'].shift(-1).loc[selected]
        )
        mask &= panels['high'].shift(-(horizon + 1)).loc[selected].gt(
            panels['low'].shift(-(horizon + 1)).loc[selected]
        )
        rank_ic = pair.where(mask).rank(axis=1).corrwith(
            future.where(mask).rank(axis=1), axis=1
        )
        rank_ic = rank_ic.where(mask.sum(axis=1).ge(int(ic['min_stocks']))).dropna()
        by_year = rank_ic.groupby(pd.to_datetime(rank_ic.index).year).mean()
        direction = 1 if len(rank_ic) and rank_ic.mean() >= 0 else -1
        directional = by_year * direction
        result['horizons'][str(horizon)] = dict(
            rank_ic_mean=_mean(rank_ic),
            rank_ic_std=float(rank_ic.std()) if len(rank_ic) > 1 else None,
            rank_ic_ir=(
                float(rank_ic.mean() / rank_ic.std())
                if len(rank_ic) > 1 and rank_ic.std() > 0
                else None
            ),
            positive_fraction=float(rank_ic.gt(0).mean()) if len(rank_ic) else None,
            observations=int(len(rank_ic)),
            average_stocks=float(mask.sum(axis=1).loc[selected].mean()),
            direction=direction,
            direction_year_fraction=(
                float(directional.gt(0).mean()) if len(directional) else None
            ),
            yearly_rank_ic={str(year): float(value) for year, value in by_year.items()},
        )
        del future, pair, mask, rank_ic, by_year
    return result


def build_shortlist(rows, selection):
    primary = str(int(selection['primary_horizon']))
    output = []
    for row in rows:
        item = row['horizons'].get(primary)
        if not item:
            continue
        absolute_ic = abs(item['rank_ic_mean']) if item['rank_ic_mean'] is not None else 0
        if (row['coverage'] is not None and
                row['coverage'] >= float(selection['minimum_coverage']) and
                absolute_ic >= float(selection['minimum_abs_rank_ic']) and
                (item['direction_year_fraction'] or 0) >= float(selection['minimum_direction_years'])):
            output.append(dict(
                factor=row['factor'],
                window=row['window'],
                direction=item['direction'],
                horizon=int(primary),
                rank_ic=item['rank_ic_mean'],
                rank_ic_ir=item['rank_ic_ir'],
                direction_year_fraction=item['direction_year_fraction'],
                coverage=row['coverage'],
            ))
    return sorted(output, key=lambda item: (
        abs(item['rank_ic']) * (item['direction_year_fraction'] or 0),
        item['coverage'],
    ), reverse=True)


def build_stability_table(rows, selection):
    """Collapse both discovery windows and every horizon into one gate."""
    output = []
    for factor in sorted({row['factor'] for row in rows}):
        records = [row for row in rows if row['factor'] == factor]
        items = [item for row in records for item in row['horizons'].values()
                 if item.get('rank_ic_mean') is not None]
        if not items:
            continue
        signs = {1 if item['rank_ic_mean'] > 0 else -1 if item['rank_ic_mean'] < 0 else 0
                 for item in items}
        direction = signs.pop() if len(signs) == 1 and 0 not in signs else 0
        directional = [item['rank_ic_mean'] * direction for item in items] if direction else []
        min_abs_ic = min(abs(item['rank_ic_mean']) for item in items)
        min_years = min(float(item.get('direction_year_fraction') or 0) for item in items)
        coverage = min(row['coverage'] or 0 for row in records)
        by_horizon = {}
        horizon_gate = True
        for horizon in sorted({h for row in records for h in row['horizons']}):
            members = [row['horizons'][horizon] for row in records
                       if horizon in row['horizons'] and row['horizons'][horizon].get('rank_ic_mean') is not None]
            signed_ics = [item['rank_ic_mean'] * direction for item in members] if direction else []
            by_horizon['ic_' + horizon] = float(np.mean(signed_ics)) if signed_ics else None
            minimum = float(selection.get('stability_minimum_abs_rank_ic', selection['minimum_abs_rank_ic']))
            horizon_gate &= bool(signed_ics and min(abs(item['rank_ic_mean']) for item in members) >= minimum)
        stable = bool(
            direction and horizon_gate and
            coverage >= float(selection['minimum_coverage']) and
            min_abs_ic >= float(selection.get('stability_minimum_abs_rank_ic', selection['minimum_abs_rank_ic'])) and
            min_years >= float(selection.get('stability_minimum_direction_years', selection['minimum_direction_years']))
        )
        output.append({
            'factor': factor,
            'direction': direction,
            'stable_pass': stable,
            'min_abs_rank_ic': min_abs_ic,
            'mean_directional_rank_ic': float(np.mean(directional)) if directional else None,
            'mean_rank_ic_ir': float(np.mean([item['rank_ic_ir'] for item in items if item.get('rank_ic_ir') is not None])),
            'minimum_direction_year_fraction': min_years,
            'coverage': coverage,
            **by_horizon,
        })
    return sorted(output, key=lambda item: (
        bool(item['stable_pass']),
        item['mean_directional_rank_ic'] if item['mean_directional_rank_ic'] is not None else -1,
    ), reverse=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Scan discovery-window factor stability.')
    parser.add_argument('--config', default='mainboard_repair_factor_scan')
    args = parser.parse_args(argv)
    policy = read_config(args.config)
    out = ROOT / policy['output_dir']
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / 'policy.json', policy)
    panels, eligible, signature, audit = load_cached_snapshot(policy)
    definitions = factor_definitions()
    rows = []
    factor_rows = []
    for name in policy['factors']:
        path, reused = ensure_factor_array(name, panels, policy, definitions)
        raw, _, _ = load_factor(name, panels, eligible, policy, definitions)
        factor_rows.append(dict(
            factor=name,
            source=str(path.relative_to(ROOT)),
            reused=bool(reused),
            finite_latest=int(np.isfinite(raw.iloc[-1].to_numpy(dtype=float)).sum()),
        ))
        log(f'FACTOR {name} ({"reused" if reused else "computed"})')
        for label, window in policy['research_windows'].items():
            row = scan_factor(
                name, raw, eligible, panels,
                window['start'], window['end'], policy['ic'],
            )
            row['window'] = label
            rows.append(row)
            primary = row['horizons'].get(str(policy['selection']['primary_horizon']), {})
            log(
                f"  {label}: IC={primary.get('rank_ic_mean')} "
                f"years={primary.get('direction_year_fraction')} coverage={row['coverage']}"
            )
        del raw
        panels.pop('_factor_cache', None)
        gc.collect()
    shortlist = build_shortlist(rows, policy['selection'])
    stability = build_stability_table(rows, policy['selection'])
    stable_shortlist = [item for item in stability if item['stable_pass']]
    write_json(out / 'factor_rank_ic.json', dict(
        snapshot_hash=signature,
        audit=audit,
        ic_policy=policy['ic'],
        windows=policy['research_windows'],
        factors=factor_rows,
        results=rows,
        shortlist=shortlist,
        stability=stability,
        stability_shortlist=stable_shortlist,
        validation_used=False,
    ))
    flat = []
    for row in rows:
        for horizon, metrics in row['horizons'].items():
            flat.append({**{
                'factor': row['factor'],
                'window': row['window'],
                'window_start': row['window_start'],
                'window_end': row['window_end'],
                'coverage': row['coverage'],
                'horizon': int(horizon),
            }, **metrics})
    pd.DataFrame(flat).to_csv(
        out / 'factor_rank_ic.csv', index=False, encoding='utf-8-sig'
    )
    pd.DataFrame(shortlist).to_csv(
        out / 'factor_shortlist.csv', index=False, encoding='utf-8-sig'
    )
    pd.DataFrame(stability).to_csv(
        out / 'factor_stability.csv', index=False, encoding='utf-8-sig'
    )
    log(f'FACTOR SCAN COMPLETE rows={len(rows)} shortlist={len(shortlist)} '
        f'stable={len(stable_shortlist)}')


if __name__ == '__main__':
    main()
