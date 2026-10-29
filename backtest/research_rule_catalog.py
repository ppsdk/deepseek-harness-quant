"""Dynamic combined index of completed classical-rule research batches."""
import json
from pathlib import Path
from factors.research_library import read_config

ROOT=Path(__file__).resolve().parents[1]


def catalog():
    cfg=read_config('technical_rule_catalog');entries=[];studies=[];ledgers=0;start=None;end=None;notes=[]
    from factors.research_evaluation import strategy_evaluation
    for key,spec in cfg['studies'].items():
        study=read_config(spec['config']);file=ROOT/study['output_dir']/'platform_catalog.json'
        if not file.exists():
            studies.append(dict(id=key,label=spec['label'],state='pending'));continue
        data=json.loads(file.read_text(encoding='utf-8'));studies.append(dict(id=key,label=spec['label'],state='complete',ledger_count=data['ledger_count']))
        entries.extend(dict(e,study_id=key,study_label=spec['label'],evaluation=strategy_evaluation(dict(e, evaluated_runs=1))) for e in data['entries']);ledgers+=data['ledger_count']
        start=min(start or data['start'],data['start']);end=max(end or data['data_end'],data['data_end'])
        if data['data_note'] not in notes:notes.append(data['data_note'])
    entries.sort(key=lambda e:(not e['candidate_pass'],e['max_drawdown']*-1,-e['annual_return']))
    return dict(ok=True,state='complete' if all(s['state']=='complete' for s in studies) else 'partial',title=cfg['title'],note=cfg['note'],
        data_note=' '.join(notes),start=start,data_end=end,ledger_count=ledgers,entries=entries,studies=studies)
