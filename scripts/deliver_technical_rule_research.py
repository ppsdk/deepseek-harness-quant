"""Present all classical-rule outcomes and their actual strategy descriptions."""
from pathlib import Path
import json
import sys
import pandas as pd

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from backtest.research_rule_catalog import catalog
from scripts.technical_rule_research import describe


def read(p):return json.loads(p.read_text(encoding='utf-8'))


def main():
    config=read_config('technical_rule_catalog');out=ROOT/config['output_dir'];out.mkdir(parents=True,exist_ok=True)
    index=catalog();pairs=[];diagnostics=[];studies=[]
    for key,spec in config['studies'].items():
        cfg=read_config(spec['config']);folder=ROOT/cfg['output_dir'];r=read(folder/'results.json');a=read(folder/'independent_ledger_audit.json')
        assert a['ok']
        entries={(e['strategy'],e['variant']):e for e in read(folder/'platform_catalog.json')['entries']}
        for p in r['pairs']:pairs.append(dict(p,study=key,study_label=spec['label'],config=cfg,entry=entries[(p['base']['strategy'],p['base']['variant'])]))
        diagnostics.extend(dict(d,study=key) for d in r['diagnostics'])
        studies.append(dict(id=key,config=spec['config'],label=spec['label'],combinations=len(r['pairs']),audit=a))
    accepted=sorted([p for p in pairs if p['base']['candidate_pass']],key=lambda p:abs(p['base']['metrics']['max_drawdown']))
    stable=[p for p in accepted if p['stress']['candidate_pass']]
    bands={str(b):sum(p['base']['return_pass'] and p['base']['activity_pass'] and p['base']['risk_bands'][str(b)] for p in pairs) for b in [10,15,20]}
    # Representatives are explicitly an explanation subset, not a new protocol.
    selected=list(accepted)
    lowrisk=[p for p in pairs if p['base']['risk_bands']['10'] and p['base']['activity_pass']]
    if lowrisk:
        low=max(lowrisk,key=lambda p:p['base']['metrics']['annual_return'])
        if low not in selected:selected.append(low)
    rows=[]
    for p in pairs:
        b,s=p['base'],p['stress'];rows.append(dict(study=p['study'],id=b['id'],label=b['label'],**{k:b['metrics'][k] for k in ['annual_return','max_drawdown','sharpe','volatility','calmar']},
            turnover=b['annual_one_way_turnover'],exposure=b['average_exposure'],trade_dates=b['trade_dates'],
            max_holdings=b['max_holdings'],stress_annual=s['metrics']['annual_return'],stress_drawdown=s['metrics']['max_drawdown'],
            candidate_pass=b['candidate_pass'],stress_candidate_pass=s['candidate_pass'],url=p['entry']['url']))
    pd.DataFrame(rows).to_csv(out/'全部技术规则结果.csv',index=False,encoding='utf-8-sig')
    total_ledgers=sum(s['audit']['ledgers'] for s in studies);total_trades=sum(s['audit']['trades'] for s in studies)
    lines=['# 基本面硬筛选与纯技术交易规则研究','',f"研究日期：2026-10-28（Asia/Shanghai）；历史回放{index['start']}—{index['data_end']}。",'',
        f"共比较{len(pairs)}组规则、{total_ledgers}份账本，独立重建{total_trades}笔成交。没有因子加权、IC选因子、PCA或机器学习。",'',
        f"基础成本下，年化≥5%且最大回撤≤10%有{bands['10']}组，≤15%有{bands['15']}组，≤20%有{bands['20']}组。基础与压力成本同时满足5%收益和20%回撤门槛的组合有{len(stable)}组。",'',
        '## 本轮判断','',
        '目前只得到需要继续观察的历史候选，尚未得到经压力费用及选股队列变化后仍稳定满足目标的策略。不能仅凭5%以上年化将其称为成熟策略。',
        '降低最大持股数改变了单只预算与实际选股，不是免费提高收益：集中度和回撤同时上升。保留低回撤但收益不足的对照。','',
        '|代表组合|最大持股数|单只新仓目标|年化|最大回撤|夏普 rf=0|单边年换手|平均仓位|压力年化／回撤|',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for p in selected:
        b,s=p['base'],p['stress'];m=b['metrics'];par=b['params']
        lines.append(f"|{b['label']}|{par['topn']}|{par['target_exposure_pct']/par['topn']:.1f}%|{m['annual_return']:.2%}|{m['max_drawdown']:.2%}|{m['sharpe']:.2f}|{b['annual_one_way_turnover']:.2f}倍|{b['average_exposure']:.2%}|{s['metrics']['annual_return']:.2%}／{s['metrics']['max_drawdown']:.2%}|")
    lines+=['','## 三批探索的边界','', '|批次|组合数|账本数|成交笔数|','|---|---:|---:|---:|']
    for s in studies:lines.append(f"|{s['label']}|{s['combinations']}|{s['audit']['ledgers']}|{s['audit']['trades']}|")
    lines+=['','首批比较MA/EMA、KDJ、DMI与估值过滤；未达目标后，第二批缩短均线并改变市场门控；第三批固定较低回撤规则，诊断持股上限与现金利用率。各批参数在各自运行前固定，但后两批方向由前面结果启发，整项研究仍是样本内探索。','',
        '## 候选的完整买卖规则与逐年收益','']
    for p in selected:
        b=p['base'];cfg=dict(p['config']);cfg['execution']=b['params']
        if 'markets' in cfg:cfg['market']=cfg['markets'][cfg['variants'][b['variant']]['market']]
        lines+=['### '+b['label'],'']
        lines.extend(v+'\n' for v in describe(cfg,cfg['strategies'][b['strategy']],cfg['variants'][b['variant']]))
        lines+=['|年份|连续账户实际收益|年内最大回撤|','|---|---:|---:|']
        for y in b['yearly']:lines.append(f"|{y['year']}|{y['total_return']:.2%}|{y['max_drawdown']:.2%}|")
        related=[d for d in diagnostics if d['study']==p['study'] and d['strategy']==b['strategy'] and d['variant']==b['variant']]
        if related:
            lines+=['','|后续诊断|年化|最大回撤|','|---|---:|---:|']
            for d in related:lines.append(f"|{'代码顺序替代成交额队列' if d['queue']!='liquidity' else '去掉指数门控'}{'／压力成本' if d['stress'] else ''}|{d['metrics']['annual_return']:.2%}|{d['metrics']['max_drawdown']:.2%}|")
        lines+=['','平台账本：`'+p['entry']['url']+'`。','']
    lines+=['## 基本面筛选的同条件对照','', '|技术规则|同池无基本面筛选年化／回撤|合理估值筛选年化／回撤|','|---|---:|---:|']
    initial=[p for p in pairs if p['study']=='initial']
    for key in dict.fromkeys(p['base']['strategy'] for p in initial):
        control=next(p['base'] for p in initial if p['base']['strategy']==key and p['base']['variant']=='technical_control')
        value=next(p['base'] for p in initial if p['base']['strategy']==key and p['base']['variant']=='value_full')
        lines.append(f"|{initial[0]['config']['strategies'][key]['label']}|{control['metrics']['annual_return']:.2%}／{control['metrics']['max_drawdown']:.2%}|{value['metrics']['annual_return']:.2%}／{value['metrics']['max_drawdown']:.2%}|")
    lines+=['','上述对照只改变基本面硬筛选，保持仓位、指数门控和技术规则；它不能替代历史财务版本审计。','',
        '## 全部规则结果（含未达标组合）','', '|批次／规则|年化|最大回撤|夏普|单边年换手|压力年化|压力回撤|','|---|---:|---:|---:|---:|---:|---:|']
    for p in sorted(pairs,key=lambda p:(not p['base']['candidate_pass'],-p['base']['metrics']['annual_return'])):
        b,s=p['base'],p['stress'];m=b['metrics'];lines.append(f"|{p['study_label']}／{b['label']}|{m['annual_return']:.2%}|{m['max_drawdown']:.2%}|{m['sharpe']:.2f}|{b['annual_one_way_turnover']:.2f}|{s['metrics']['annual_return']:.2%}|{s['metrics']['max_drawdown']:.2%}|")
    lines+=['','## 数据、执行与复现','',index['data_note'],
        '每日估值用信号日精确日期连接，不作前向或后向填充；当前可用日期为2026年10月的ROE、增长率不参与历史筛选。缺少历史公告版本的财务质量选股尚未实现。',
        '普通交易至少相隔5个交易日，个股止损与技术退出也会等待，只有指数风险退出是紧急例外；次日开盘有跳空风险，8%止损阈值不代表损失最多8%。',
        '现金不计息；股票使用前复权与小数份额，未处理100股整手、容量和真实盘口成交；一字板以完整日线过滤。卖出税固定10bp，未按历史分段。',
        '2026为不足一年实际收益，不冒充全年。最高收益、最低回撤和代表性组合都在同一已观察历史中比较，不能当成新盲测或统计显著性证明。',
        '独立审计覆盖源开盘价、成交与费用算术、现金及收盘权益、持股数量和交易间隔；未重构退市全集或核实历史财报版本。',
        '复现依次运行 `scripts/technical_rule_research.py`、`scripts/technical_rule_extension.py`、`scripts/technical_rule_allocation.py`、`scripts/deliver_technical_rule_research.py`。原始行情与估值仅本地使用，不再分发。','']
    report=out/'纯技术与基本面规则策略研究.md';report.write_text('\n'.join(lines),encoding='utf-8')
    result=dict(ok=True,combinations=len(pairs),ledgers=total_ledgers,trades=total_trades,bands=bands,stress_robust_candidates=len(stable),
        candidates=[dict(study=p['study'],**p['entry']) for p in accepted],representatives=[dict(study=p['study'],**p['entry']) for p in selected])
    (out/'研究结论.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(result,report=str(report)),ensure_ascii=False))


if __name__=='__main__':main()
