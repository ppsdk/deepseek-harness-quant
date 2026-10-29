"""Config-driven discovery and frozen replay for low-risk main-board repair ideas.

The script reuses the platform's real T+1/open/limit/cost ledger. It keeps a
separate output tree and reads the existing point-in-time price snapshot in
read-only mode. Validation years are chronological replays, not untouched
blind samples, because earlier research already inspected 2023--2025.
"""
from copy import deepcopy
import argparse
import gc
import hashlib
import json
from pathlib import Path
import re
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from factors.research_library import factor_definitions, raw_factor, read_config
from scripts.mainboard_strategy_research import execute, log, metadata, summarize, write_json
from data.cache import DailyCache

FIELDS = ['open', 'high', 'low', 'volume', 'close', 'amount', 'turn', 'is_st']


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def load_cached_snapshot(policy):
    source = ROOT / policy['snapshot_dir'] / 'cache'
    cache = DailyCache()
    codes = sorted(code for code in metadata(cache) if re.fullmatch(policy['universe']['code_pattern'], code))
    index = cache.get_daily(policy['execution']['timing_index'], start=policy['data_start'], end=policy['data_end'], adjust='none')
    dates = pd.Index(sorted(index.date.unique()), name='date')
    identity = dict(codes=codes, dates=list(dates), db=[(str(p), Path(p).stat().st_size, Path(p).stat().st_mtime_ns)
                    for p in [cache.db_path, *cache._inc_paths()] if Path(p).exists()])
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    stamp = source / 'snapshot.json'
    if not stamp.exists():
        raise ValueError('缺少价格快照：' + str(stamp))
    frozen = json.loads(stamp.read_text(encoding='utf-8'))
    if frozen.get('signature') != signature:
        raise ValueError('价格快照签名不一致。请先更新全市场数据并重跑主板基础研究，禁止混用新旧日期。')
    missing = [field for field in FIELDS if not (source / (field + '.npy')).exists()]
    if missing:
        raise ValueError('价格快照缺字段：' + ', '.join(missing))
    panels = {}
    for field in FIELDS:
        array = np.load(source / (field + '.npy'), mmap_mode='r')
        if array.shape != (len(dates), len(codes)):
            raise ValueError(f'{field}.npy 形状与快照不一致。')
        panels[field] = pd.DataFrame(array, index=dates, columns=codes, copy=False)
    u = policy['universe']
    eligible = panels['close'].notna().cumsum().ge(u['min_history_days'])
    eligible &= panels['amount'].rolling(u['liquidity_window']).mean().ge(u['min_average_amount'])
    eligible &= panels['volume'].gt(0) & panels['close'].gt(0)
    if u['exclude_st']:
        eligible &= panels['is_st'].ne(1)
    audit = json.loads((ROOT / policy['snapshot_dir'] / 'data_audit.json').read_text(encoding='utf-8'))
    audit.update(snapshot_dir=policy['snapshot_dir'], reused_read_only=True,
                 eligible_recomputed_latest=int(eligible.iloc[-1].sum()),
                 universe_limitation=audit.get('universe_limitation', '') + ' 本脚本未补齐历史退市证券；ST覆盖不足时只视为未证明排除。')
    return panels, eligible, signature, audit


def ensure_factor_array(name, panels, policy, definitions):
    source = ROOT / policy['snapshot_dir'] / 'cache' / ('factor_' + name + '.npy')
    target = ROOT / policy['output_dir'] / 'cache' / ('factor_' + name + '.npy')
    paths = [source]
    if policy.get('factor_cache_dir'):
        factor_folder = ROOT / policy['factor_cache_dir']
        manifest = factor_folder.parent / 'factor_rank_ic.json'
        snapshot = ROOT / policy['snapshot_dir'] / 'cache' / 'snapshot.json'
        if not manifest.exists() or not snapshot.exists():
            raise ValueError('复用外部因子缓存需要价格快照与因子扫描来源记录。')
        if json.loads(manifest.read_text(encoding='utf-8'))['snapshot_hash'] != json.loads(snapshot.read_text(encoding='utf-8'))['signature']:
            raise ValueError('外部因子缓存不属于当前价格快照。')
        paths.append(factor_folder / source.name)
    paths.append(target)
    for path in paths:
        if path.exists():
            if np.load(path, mmap_mode='r').shape != panels['close'].shape:
                raise ValueError('因子缓存与价格快照形状不一致：' + str(path))
            return path, True
    log('计算新因子 ' + name)
    raw = raw_factor(name, panels, definitions)
    target.parent.mkdir(parents=True, exist_ok=True)
    np.save(target, raw.to_numpy())
    panels.pop('_factor_cache', None)
    del raw
    gc.collect()
    return target, False


def load_factor(name, panels, eligible, policy, definitions):
    path, reused = ensure_factor_array(name, panels, policy, definitions)
    frame = pd.DataFrame(np.load(path, mmap_mode='r'), index=eligible.index, columns=eligible.columns, copy=False)
    return frame, path, reused

def build_pool_masks(panels, eligible, policy):
    definitions = factor_definitions()
    folder = ROOT / policy['output_dir'] / 'cache'
    folder.mkdir(parents=True, exist_ok=True)
    masks, audit = {}, {}
    for key, spec in policy['risk_pools'].items():
        mask = eligible.copy()
        factor_audit = []
        for limit in spec['limits']:
            raw, path, reused = load_factor(limit['factor'], panels, eligible, policy, definitions)
            rank = raw.where(eligible).rank(axis=1, pct=True)
            mask &= rank.le(float(limit['max_percentile']))
            factor_audit.append(dict(factor=limit['factor'], max_percentile=limit['max_percentile'],
                                     source=str(path.relative_to(ROOT)), reused=reused,
                                     latest_coverage=float(rank.iloc[-1].notna().mean())))
            del raw, rank
            gc.collect()
        trend_period = int(spec['trend']['period'])
        trend = panels['close'] / panels['close'].rolling(trend_period).mean() - 1
        mask &= trend.ge(float(spec['trend']['min_return']))
        np.save(folder / ('pool_' + key + '.npy'), mask.to_numpy(dtype=bool))
        audit[key] = dict(description=spec['description'], factors=factor_audit, trend=spec['trend'],
                          latest_candidates=int(mask.iloc[-1].sum()),
                          average_candidates=float(mask.sum(axis=1).mean()))
        log(f"风险池 {key}: 最新 {mask.iloc[-1].sum()} 只，日均 {mask.sum(axis=1).mean():.1f} 只")
        masks[key] = mask
        del trend
        gc.collect()
    write_json(folder / 'risk_pools.json', audit)
    return masks, audit


def build_entry_masks(panels, eligible, policy):
    folder = ROOT / policy["output_dir"] / "cache"
    folder.mkdir(parents=True, exist_ok=True)
    masks, audit = {}, {}
    close = panels["close"]
    volume = panels["volume"]
    for key, spec in policy["entry_rules"].items():
        if key == "none":
            mask = eligible.copy()
            audit[key] = dict(description=spec["description"], latest_candidates=int(mask.iloc[-1].sum()))
        else:
            fast = close.rolling(int(spec["fast"])).mean() if "fast" in spec else None
            slow = close.rolling(int(spec["slow"])).mean() if "slow" in spec else None
            reference = slow if slow is not None else fast
            if reference is None:
                raise ValueError("入场规则 " + key + " 至少需要一个均线周期。")
            ratio = close / reference - 1
            mask = eligible & np.isfinite(ratio)
            if "min_price_vs_slow" in spec:
                mask &= ratio.ge(float(spec["min_price_vs_slow"]))
            if "max_price_vs_slow" in spec:
                mask &= ratio.le(float(spec["max_price_vs_slow"]))
            if spec.get("require_fast_above_slow"):
                if fast is None or slow is None:
                    raise ValueError("入场规则 " + key + " 的均线比较需要同时配置 fast 和 slow。")
                mask &= fast.ge(slow)
            if spec.get("slow_rising_days"):
                days = int(spec["slow_rising_days"])
                slope = reference / reference.shift(days) - 1
                mask &= slope.ge(float(spec.get("min_slow_slope", 0.0)))
            if spec.get("volume_shrink_window"):
                window = int(spec["volume_shrink_window"])
                baseline = volume.rolling(window, min_periods=window).mean().shift(1)
                volume_ratio = volume / baseline.replace(0, np.nan)
                mask &= volume_ratio.le(float(spec.get("max_volume_ratio", 1.0)))
            if spec.get("confirm_days"):
                days = int(spec["confirm_days"])
                mask &= mask.rolling(days, min_periods=days).sum().eq(days)
            mask &= eligible
            audit[key] = dict(
                description=spec["description"],
                latest_candidates=int(mask.iloc[-1].sum()),
                average_candidates=float(mask.sum(axis=1).mean()),
                params={k: v for k, v in spec.items() if k != "description"},
            )
            del ratio
            if fast is not None:
                del fast
            if slow is not None:
                del slow
        np.save(folder / ("entry_" + key + ".npy"), mask.to_numpy(dtype=bool))
        masks[key] = mask
        gc.collect()
    write_json(folder / "entry_rules.json", audit)
    return masks, audit

def build_scores(panels, eligible, policy, pool_masks, entry_masks):
    definitions = factor_definitions()
    folder = ROOT / policy['output_dir'] / 'cache'
    paths, audit = {}, {}
    for cid, candidate in policy['candidates'].items():
        base_mask = pool_masks[candidate['pool']] & entry_masks[candidate['entry_rule']]
        score = None
        valid = base_mask.copy()
        weight_total = 0.0
        factor_audit = []
        for item in candidate['factors']:
            raw, path, reused = load_factor(item['name'], panels, eligible, policy, definitions)
            signed = raw * float(item['sign'])
            rank = signed.where(base_mask).rank(axis=1, pct=True)
            weight = float(item.get('weight', 1))
            score = rank * weight if score is None else score + rank * weight
            valid &= raw.notna()
            weight_total += weight
            factor_audit.append(dict(**item, source=str(path.relative_to(ROOT)), reused=reused,
                                     latest_coverage=float(rank.iloc[-1].notna().mean())))
            del raw, signed, rank
            gc.collect()
        final = (score / weight_total).where(valid)
        target = folder / ('score_' + cid + '.npy')
        np.save(target, final.to_numpy())
        paths[cid] = target
        audit[cid] = dict(label=candidate['label'], pool=candidate['pool'], entry_rule=candidate['entry_rule'],
                          factors=factor_audit, latest_candidates=int(final.iloc[-1].notna().sum()),
                          average_candidates=float(final.notna().sum(axis=1).mean()))
        log(f"候选 {cid}: 最新可排名 {final.iloc[-1].notna().sum()} 只")
        del score, valid, final, base_mask
        gc.collect()
    write_json(folder / 'candidate_factors.json', audit)
    return paths, audit

def conditional_ic(panels, eligible, score_paths, policy, discovery, out):
    rows = []
    dates = panels['close'].index
    train_dates = dates[(dates >= discovery['start']) & (dates <= discovery['end'])]
    opening = panels['open']
    for cid, path in score_paths.items():
        score = pd.DataFrame(np.load(path, mmap_mode='r'), index=eligible.index, columns=eligible.columns, copy=False)
        for horizon in policy['ic']['horizons']:
            selected = train_dates[:-(horizon + 1):policy['ic']['sample_every_days']]
            future = opening.shift(-(horizon + 1)).loc[selected] / opening.shift(-1).loc[selected] - 1
            pair = score.loc[selected]
            mask = pair.notna() & future.notna()
            mask &= panels['volume'].shift(-1).loc[selected].gt(0)
            mask &= panels['volume'].shift(-(horizon + 1)).loc[selected].gt(0)
            mask &= panels['high'].shift(-1).loc[selected].gt(panels['low'].shift(-1).loc[selected])
            mask &= panels['high'].shift(-(horizon + 1)).loc[selected].gt(panels['low'].shift(-(horizon + 1)).loc[selected])
            values = pair.where(mask).rank(axis=1).corrwith(future.where(mask).rank(axis=1), axis=1)
            values = values.where(mask.sum(axis=1).ge(policy['ic']['min_stocks'])).dropna()
            sd = float(values.std()) if len(values) else None
            rows.append(dict(candidate=cid, horizon=horizon, rank_ic_mean=float(values.mean()) if len(values) else None,
                             rank_ic_std=sd, rank_ic_ir=float(values.mean() / sd) if sd else None,
                             positive_fraction=float(values.gt(0).mean()) if len(values) else None,
                             observations=len(values), condition='risk_pool_and_entry_rule',
                             label_end_bound=discovery['end']))
            del future, pair, mask, values
        del score
        gc.collect()
    pd.DataFrame(rows).to_csv(out / 'candidate_conditional_ic.csv', index=False, encoding='utf-8-sig')
    write_json(out / 'candidate_conditional_ic.json', rows)
    return rows


def finish_summary(sim, p, policy):
    item = summarize(sim, p, policy)
    interval = int(p.get('min_trade_interval_days', p['rebalance_days']))
    item['ordinary_interval_days'] = interval
    # Market-risk exits are emergency permission, while ordinary rebalances retain their cadence.
    item['frequency_pass'] = bool((item.get('minimum_regular_trade_gap') is None or item['minimum_regular_trade_gap'] >= interval)
                                  and (item.get('minimum_regular_after_any_gap') is None or item['minimum_regular_after_any_gap'] >= interval))
    dd_ok = item['metrics']['max_drawdown'] >= float(policy['targets']['max_drawdown'])
    trade_count_ok = item['max_traded_codes'] <= int(policy['targets']['max_traded_codes_per_day'])
    holding_ok = item['max_holdings'] <= int(policy['targets']['max_holdings'])
    item['target_pass'] = bool(item['return_target_pass'] and dd_ok and item['frequency_pass'] and trade_count_ok and holding_ok)
    item['drawdown_pass'] = bool(dd_ok)
    item['trade_count_pass'] = bool(trade_count_ok)
    item['holding_count_pass'] = bool(holding_ok)
    return item


def rank_key(item):
    return (bool(item['target_pass']), bool(item['drawdown_pass']), bool(item['frequency_pass']),
            bool(item['return_target_pass']), float(item['metrics']['annual_return']),
            float(item['metrics']['calmar']), -float(item['annual_one_way_turnover']))


def select_discovery(discovery, policy, round_id):
    by_candidate = {}
    for cid in policy['candidates']:
        options = [item for item in discovery if item['strategy'] == cid]
        if options:
            by_candidate[cid] = max(options, key=rank_key)
    ranked = sorted(by_candidate.values(), key=rank_key, reverse=True)
    finalists = [item['strategy'] for item in ranked[:int(policy['selection']['finalists'])]]
    return {item['strategy']: item['variant'] for item in ranked[:int(policy['selection']['finalists'])]}, finalists, ranked


def run_one(panels, eligible, score_paths, policy, round_spec, out, cache_rows, signature, config_hash,
            cid, variant, period, stress=False):
    phase = period + ('_stress' if stress else '')
    run_variant = dict(variant)
    if stress:
        run_variant.update(policy['stress'])
    identity = dict(config_hash=config_hash, snapshot_hash=signature, round=round_spec['id'],
                    candidate=cid, variant=variant['id'], phase=phase)
    old = next((item for item in cache_rows if item.get('run_identity') == identity), None)
    if old:
        log(f"复用 {round_spec['id']} {phase} {cid}/{variant['id']}")
        return old
    score = pd.DataFrame(np.load(score_paths[cid], mmap_mode='r'), index=eligible.index, columns=eligible.columns, copy=False)
    sim, p = execute(panels, score, policy['periods'][period], run_variant, policy)
    item = dict(round_id=round_spec['id'], strategy=cid, title=policy['candidates'][cid]['label'],
                variant=variant['id'], variant_label=variant['label'], period=phase,
                params=p, factor_list=policy['candidates'][cid]['factors'], snapshot_hash=signature,
                run_identity=identity, **finish_summary(sim, p, policy))
    folder = out / round_spec['id']
    (folder / 'runs').mkdir(parents=True, exist_ok=True)
    payload = dict(**item, dates=[str(d.date()) for d in sim['nav_series'].index],
                   nav=list(sim['nav_series'].values), trades=sim['trades'],
                   daily_account=sim['daily_account'], rejected_orders=sim['rejections'])
    write_json(folder / 'runs' / f'{cid}_{variant["id"]}_{phase}.json', payload)
    m = item['metrics']
    log(f"{round_spec['id']} {phase} {cid}/{variant['id']} annual={m['annual_return']:.2%} "
        f"excess={m['annual_excess_pp']:.2f}pp DD={m['max_drawdown']:.2%} "
        f"turnover={item['annual_one_way_turnover']:.2f} traded={item['max_traded_codes']}")
    del score, sim, payload
    gc.collect()
    return item

def run_round(panels, eligible, score_paths, base_policy, round_spec, out, signature):
    round_policy = deepcopy(base_policy)
    round_policy['round_id'] = round_spec['id']
    round_policy['periods'].update(discovery=round_spec['discovery'], validation=round_spec['validation'])
    folder = out / round_spec['id']
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / 'policy.json', round_policy)
    config_hash = canonical_hash(round_policy)
    ledger = folder / 'experiments.jsonl'
    cache_rows = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines() if line] if ledger.exists() else []
    logs = []
    ic_rows = conditional_ic(panels, eligible, score_paths, round_policy, round_spec['discovery'], folder)

    def run(cid, variant, period, stress=False):
        item = run_one(panels, eligible, score_paths, round_policy, round_spec, out, cache_rows,
                       signature, config_hash, cid, variant, period, stress)
        if not any(row.get('run_identity') == item.get('run_identity') for row in cache_rows):
            with ledger.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(item, ensure_ascii=False) + '\n')
            cache_rows.append(item)
        logs.append(item)
        return item

    discovery = [run(cid, variant, 'discovery') for cid in round_policy['candidates'] for variant in round_policy['variants']]
    selected_variants, finalists, ranked = select_discovery(discovery, round_policy, round_spec['id'])
    frozen = dict(round_id=round_spec['id'], discovery=round_spec['discovery'], validation=round_spec['validation'],
                  finalists=finalists, variants=selected_variants,
                  discovery_ranking=[dict(strategy=item['strategy'], variant=item['variant'],
                                          target_pass=item['target_pass'], drawdown_pass=item['drawdown_pass'],
                                          annual_return=item['metrics']['annual_return'],
                                          annual_excess_pp=item['metrics']['annual_excess_pp'],
                                          max_drawdown=item['metrics']['max_drawdown'],
                                          calmar=item['metrics']['calmar'],
                                          annual_one_way_turnover=item['annual_one_way_turnover'])
                                      for item in ranked],
                  selection_data_end=round_spec['discovery']['end'], selection_uses_validation=False,
                  validation_is_fresh_blind=False,
                  design_note=round_policy.get('research_design_note',''),
                  note='发现期筛选后才生成此文件；2023--2025已被旧研究查看，验证属于时间隔离回放。')
    write_json(folder / 'frozen_selection.json', frozen)

    def chosen(cid):
        return next(variant for variant in round_policy['variants'] if variant['id'] == selected_variants[cid])

    validation = [run(cid, chosen(cid), 'validation') for cid in finalists]
    validation_stress = [run(cid, chosen(cid), 'validation', stress=True) for cid in finalists]
    full = [run(cid, chosen(cid), 'full') for cid in finalists]
    result = dict(round_id=round_spec['id'], frozen=frozen, conditional_ic=ic_rows,
                  experiments=logs, validation=validation, validation_stress=validation_stress, full=full,
                  passing_discovery=sum(item['target_pass'] for item in discovery),
                  passing_validation=sum(item['target_pass'] for item in validation),
                  passing_full=sum(item['target_pass'] for item in full),
                  passing_validation_stress=sum(item['target_pass'] for item in validation_stress))
    write_json(folder / 'results.json', result)
    return result


def render_report(policy, rounds, market):
    lines = ['# 主板低风险修复策略研究', '',
             f"价格快照截至 `{policy['data_end']}`。本记录是带时间隔离的规则复现；验证年份曾被旧研究查看，不能称为新的盲测。", '',
             policy.get('research_design_note',''), '',
             '## 已保存快照中的环境（非实时）', '']
    for code, row in market.get('indices', {}).items():
        lines.append(f"- `{code}`：收盘 {row['close']:.2f}，MA20 {row['ma']['20']:.2f}，MA60 {row['ma']['60']:.2f}；60日回撤 {row['drawdown60']:.2%}。")
    breadth = market.get('breadth', {})
    if breadth:
        lines.append(f"- 主板可交易股票中位于 MA60 上方：{breadth['above_ma60']:.2%}，样本 {breadth['eligible']} 只。")
    lines += ['', '## 验证结果', '']
    for result in rounds:
        frozen = result['frozen']
        lines.append(f"### {result['round_id']}")
        lines.append(f"发现期 {frozen['discovery']['start']} ~ {frozen['discovery']['end']}；验证期 {frozen['validation']['start']} ~ {frozen['validation']['end']}。")
        lines += ['', '| 候选 | 冻结变体 | 验证年化 | 相对沪深300 | 最大回撤 | 单边年换手 | 结果 |', '|---|---|---:|---:|---:|---:|---|']
        for item in result['validation']:
            lines.append('| {title} | {variant} | {annual:.2%} | {excess:.2f}pp | {dd:.2%} | {turn:.2f} | {status} |'.format(
                title=item['title'], variant=item['variant'], annual=item['metrics']['annual_return'],
                excess=item['metrics']['annual_excess_pp'], dd=item['metrics']['max_drawdown'],
                turn=item['annual_one_way_turnover'], status='硬约束通过' if item['target_pass'] else '未通过'))
        lines += ['', '| 候选 | 5日 RankIC | 20日 RankIC |', '|---|---:|---:|']
        for cid in frozen['finalists']:
            ic5 = next(row for row in result['conditional_ic'] if row['candidate'] == cid and row['horizon'] == 5)
            ic20 = next(row for row in result['conditional_ic'] if row['candidate'] == cid and row['horizon'] == 20)
            lines.append(f"| {policy['candidates'][cid]['label']} | {ic5['rank_ic_mean']:.4f} | {ic20['rank_ic_mean']:.4f} |")
        lines.append('')
    lines += ['## 口径与限制', '',
              '- 信号使用前一交易日收盘数据，T+1 开盘执行；过滤一字板、停牌和无效价格。',
              '- 计入佣金、卖出税费和滑点；普通交易按冻结配置的交易日间隔，强风险允许次日紧急清仓。',
              '- 最多持有 5 只，并要求每个交易日买卖去重后的股票数不超过 5 只。',
              '- 基准是沪深300价格指数，未计分红；超额不能视为全收益指数的严格净超额。',
              '- 当前缓存是幸存者股票池，未重建完整历史退市全集；ST 字段覆盖不足时不能声称已可靠排除 ST。',
              '- 验证结果未通过上限时保留失败记录，不据此回头调参。', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='mainboard_repair_research')
    args = parser.parse_args(argv)
    policy = read_config(args.config)
    out = ROOT / policy['output_dir']
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / 'policy.json', policy)
    panels, eligible, signature, audit = load_cached_snapshot(policy)
    write_json(out / 'data_audit.json', audit)
    log('快照 ' + signature)
    pool_masks, _ = build_pool_masks(panels, eligible, policy)
    entry_masks, _ = build_entry_masks(panels, eligible, policy)
    score_paths, _ = build_scores(panels, eligible, policy, pool_masks, entry_masks)
    rounds = [run_round(panels, eligible, score_paths, policy, spec, out, signature) for spec in policy['rounds']]
    flat = [item for result in rounds for item in result['experiments']]
    table = []
    for item in flat:
        table.append({**{key: item[key] for key in ['round_id', 'strategy', 'title', 'variant', 'period', 'target_pass',
                                                   'drawdown_pass', 'frequency_pass', 'max_holdings', 'max_traded_codes',
                                                   'annual_one_way_turnover', 'ordinary_interval_days']}, **item['metrics']})
    pd.DataFrame(table).to_csv(out / 'results.csv', index=False, encoding='utf-8-sig')
    existing_state = ROOT / policy['snapshot_dir'] / 'market_state.json'
    market = json.loads(existing_state.read_text(encoding='utf-8')) if existing_state.exists() else {}
    write_json(out / 'results.json', dict(snapshot_hash=signature, audit=audit, rounds=rounds,
                                          validation_is_fresh_blind=False, market_context=market))
    (out / '研究结论.md').write_text(render_report(policy, rounds, market), encoding='utf-8')
    log('REPAIR RESEARCH COMPLETE ' + str(out / 'results.json'))


if __name__ == '__main__':
    main()
