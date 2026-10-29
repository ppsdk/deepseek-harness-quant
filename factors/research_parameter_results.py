"""Read-only presentation of frozen parameter research, without provider bars."""
import json
from pathlib import Path
from factors.research_library import read_config

ROOT=Path(__file__).resolve().parents[1]


def latest():
    cfg=read_config('factor_parameter_research');folder=ROOT/cfg['output_dir']
    manifest_path=folder/'experiment_manifest.json';result_path=folder/'portfolio_results.json'
    payload=dict(ok=True,state='pending',as_of=cfg['as_of'],rounds=cfg['rounds'],records=[],budget_records=[],scan_completed=0,scan_total=0,
                 note=f"发现期选参后冻结回放；既有历史已被查看，不是新盲测。{cfg['portfolio']['diagnostic_topn']}只组合仅作诊断。",
                 budget_note='仓位对照是在观察原验证结果后提出的追加实验；仓位仅用发现期校准，低回撤同时来自现金配置。')
    if manifest_path.exists():
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        payload.update(state='running',scan_total=len(manifest['grid']),scan_completed=len(list((folder/'scan').glob('*.json'))))
    if result_path.exists():
        from factors.research_evaluation import parameter_evaluation
        data=json.loads(result_path.read_text(encoding='utf-8'));rows=data['records'];selected=[]
        for row in rows:
            if row['phase']!='validation' or row['stress'] or row['diagnostic_only']:continue
            control=next(r for r in rows if r['round_id']==row['round_id'] and r['family_id']==row['family_id'] and r['phase']=='default_control' and not r['stress'])
            item=dict(round_id=row['round_id'],label=row['label'],parameter=row['parameter'],value=row['value'],
                annual_return=row['metrics']['annual_return'],max_drawdown=row['metrics']['max_drawdown'],turnover=row['annual_one_way_turnover'],
                default_annual_return=control['metrics']['annual_return'],constraints_pass=row['constraints_pass'],cadence=row['cadence'],timing_rule=row['timing_rule'])
            item['evaluation']=parameter_evaluation(item);selected.append(item)
        payload.update(state='complete',records=selected)
    budget=read_config('factor_parameter_budget');budget_folder=ROOT/budget['output_dir']
    if (budget_folder/'results.json').exists():
        data=json.loads((budget_folder/'results.json').read_text(encoding='utf-8'));rows=data['records']
        frozen_budget=json.loads((budget_folder/'frozen_budget.json').read_text(encoding='utf-8'))['records']
        for row in rows:
            if row['phase']!='continuous_forward' or row['stress']:continue
            pressure=next(r for r in rows if r['round_id']==row['round_id'] and r['family_id']==row['family_id'] and r['phase']==row['phase'] and r['stress'])
            discovery=next(r for r in rows if r['round_id']==row['round_id'] and r['family_id']==row['family_id'] and r['phase']=='discovery')
            below_minimum=next(r['calibration']['below_minimum'] for r in frozen_budget if r['round_id']==row['round_id'] and r['family']==row['family_id'])
            item=dict(round_id=row['round_id'],label=row['label'],period=row['period'],target_exposure_pct=row['target_exposure_pct'],
                annual_return=row['metrics']['annual_return'],max_drawdown=row['metrics']['max_drawdown'],turnover=row['annual_one_way_turnover'],average_exposure=row['average_exposure'],
                pressure_annual_return=pressure['metrics']['annual_return'],pressure_max_drawdown=pressure['metrics']['max_drawdown'],
                constraints_pass=bool(discovery['constraints_pass'] and row['constraints_pass'] and pressure['constraints_pass'] and not below_minimum))
            item['evaluation']=parameter_evaluation(item);payload['budget_records'].append(item)
    return payload
