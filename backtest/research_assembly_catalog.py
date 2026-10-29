"""Configuration-driven presentation of frozen, archived strategy assemblies."""
import json
from pathlib import Path
from factors.research_library import read_config

ROOT=Path(__file__).resolve().parents[1]


def latest():
    catalog=read_config('strategy_assembly_catalog');studies=[];entries=[]
    from factors.research_evaluation import strategy_evaluation
    for key,spec in catalog['studies'].items():
        cfg=read_config(spec['config']);folder=ROOT/cfg['output_dir'];file=folder/'candidate_strategies.json'
        row=dict(id=key,label=spec['label'],state='pending',entries=0)
        if file.exists():
            result=json.loads(file.read_text(encoding='utf-8'));row.update(state='complete',entries=len(result['entries']),data_end=result['data_end'],ledgers=result['ledger_count'])
            entries.extend([dict(r,study_id=key,study_label=spec['label'],scope_label=catalog['scope_labels'].get(r['scope'],r['scope']), evaluation=strategy_evaluation(dict(r, evaluated_runs=1))) for r in result['entries']])
        studies.append(row)
    return dict(ok=True,state='complete' if all(s['state']=='complete' for s in studies) else 'partial',studies=studies,entries=entries,
        note='发现期组建后冻结回放；既有历史已被观察。数值门槛通过不代表未来收益或回撤保证。估值与全主板分别比较；账本可在下方打开。')


def write_index():
    catalog=read_config('strategy_assembly_catalog');data=latest();folder=ROOT/catalog['output_dir'];folder.mkdir(parents=True,exist_ok=True)
    lines=['# 策略组建研究索引','\n'+data['note'],
        '\n| 批次 / 冻结组合 | 股票池 | 仓位上限 | 连续年化 | 同条件核心年化 | 最大回撤 | 单边年换手 | 全段年化 | 全段回撤 |','|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for e in data['entries']:lines.append(f"|{e['study_label']} / {e['round_id']} / {e['label']}|{e['scope_label']}|{e['target_exposure_pct']}%|{e['annual_return']:.2%}|{e['matched_core_annual_return']:.2%}|{e['max_drawdown']:.2%}|{e['turnover']:.2f}|{e['overall_annual_return']:.2%}|{e['overall_max_drawdown']:.2%}|")
    lines+=['\n连续回放窗口依各轮发现期之后的首日开始，不能跨不同窗口直接按年化排序。技术全段包含发现期，仅作整体历史回放。']
    for key,spec in catalog['studies'].items():
        cfg=read_config(spec['config']);report=ROOT/cfg['output_dir']/'主板策略组建研究.md'
        lines.append(f"\n- [{spec['label']}详细报告]({report.as_posix()})")
    lines+=['\n在平台“策略管理”的“本轮组建策略”中可打开每个冻结配置的图形账本；精确复现使用冻结策略包和研究脚本。']
    (folder/'策略组建研究索引.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return folder/'策略组建研究索引.md'
