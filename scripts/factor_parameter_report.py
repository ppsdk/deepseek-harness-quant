"""Chinese report for parameter plateaus and frozen portfolio replay evidence."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.mainboard_strategy_research import write_json

ROOT=Path(__file__).resolve().parents[1]


def percentage(value):return '不可用' if value is None else f'{value:.2%}'


def run(cfg,base,out):
    manifest=json.loads((out/'experiment_manifest.json').read_text(encoding='utf-8'))
    results=json.loads((out/'portfolio_results.json').read_text(encoding='utf-8'));rows=results['records']
    labels=base['category_labels'];periods=cfg['search']['periods'];primary=cfg['portfolio']['primary_topn']
    snapshot=json.loads((ROOT/base['snapshot_dir']/'cache/snapshot.json').read_text(encoding='utf-8'))
    valuation_universe=json.loads((ROOT/base['output_dir']/'valuation_universe.json').read_text(encoding='utf-8'))
    source=json.loads((ROOT/base['output_dir']/'source_register.json').read_text(encoding='utf-8'))
    source['checked_at']=datetime.now(timezone.utc).isoformat()
    source['sources'].append(dict(source_id='S_PARAM',title='本轮参数网格、训练冻结清单与逐笔账本',access_class='not_applicable',
        evidence_label='本地实测历史回放',identity=manifest['identity'],snapshot_hash=results['snapshot_hash'],path=str(out),redistribution=False))
    source['sources'].append(dict(source_id='S_PBO',title='The Probability of Backtest Overfitting',access_class='public_full',
        evidence_label='一手方法论文',canonical_url='https://carmamaths.org/resources/jon/backtest2.pdf',supports='多次试验和参数择优会引入选择偏差；本轮没有声称已计算PBO'))
    write_json(out/'source_register.json',source)
    code_paths=['backtest/research_factor_search.py','scripts/factor_parameter_research.py','scripts/factor_parameter_portfolios.py','scripts/factor_parameter_report.py',
                'factors/research_axis.py','factors/research_indicators.py','backtest/research_portfolio.py']
    write_json(out/'implementation_hashes.json',{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in code_paths})
    lines=['# 因子参数稳健性与集中选股研究',
        f"\n执行日 {cfg['as_of']}，Asia/Shanghai；股票与估值数据截止 {base['data_end']}。本研究复用冻结的{len(snapshot['codes'])}只主板价格代码池，估值限预先确定的{len(valuation_universe['codes'])}只样本。未获取新的行情，也未改写上一轮默认参数研究结果。[S_PRICE][S_VALUE][S_PARAM]",
        '\n本轮结果是已被观察历史上的时间隔离回放，不是新的盲测。不训练机器学习收益模型；发现期选参数、因子、交易频率和择时方式，冻结后才运行验证账本。',
        '\n## 研究设计',
        f"\n共 {len(manifest['grid'])} 个参数组合、{len(set(r['family_id'] for r in manifest['grid']))} 个因子家族。常规周期 {periods}；历史相对估值周期 {cfg['search']['relative_valuation_periods']}；无周期估值比率的因果平滑周期 {cfg['search']['smoothing_periods']}。每次只改一个参数，保留原默认值；不为不同周期注册新因子编号。",
        f"\n每轮用发现期5/20/60日标签独立筛选。所有标签从T+1开盘进入，退出开盘不得越过发现期末。主期限{cfg['search']['primary_horizon']}日，|RankIC|门槛{cfg['search']['min_abs_rank_ic']}；每轮全部参数的HAC检验一起做BH-FDR（q≤{cfg['search']['fdr']}）。覆盖率、年份同向比例、五分组差和前{primary}只毛收益超额也需通过。FDR只覆盖IC网格，不消除历次研究和组合规则选择的全部偏差。[S_PBO]",
        f"\n稳定性要求：相邻参数中至少{cfg['search']['min_same_direction_neighbors']}个同向通过，且通过比例至少{cfg['search']['min_neighbor_fraction']:.0%}；按自身与合格邻居的IC绝对值下{cfg['search']['robust_quantile']:.0%}分位数排序。每家族最多{cfg['search']['parameters_per_family']}个参数入围，每类先选{cfg['search']['finalists_per_category']}个家族做真实账本。只有单个参数突出的尖峰会被淘汰。",
        f"\n持仓账本使用前收盘信号、次日开盘、停牌和一字板限制；佣金{cfg['portfolio']['commission_bps']}bps、卖出印花税{cfg['portfolio']['stamp_tax_bps']}bps、滑点{cfg['portfolio']['slippage_bps']}bps，另有最低佣金。压力成本为{cfg['stress']}。正常交易至少间隔{cfg['portfolio']['min_trade_interval_days']}个交易日，强风险次日退出可例外。",
        f"\n研究比较{cfg['portfolio']['rebalance_days']}日调仓和{cfg['portfolio']['timing_rules']}择时；已有持仓位于前ceil(N×{cfg['portfolio']['candidate_buffer']})名内时优先保留，减少轻微排名变化导致的换手。前{primary}只为可部署数量口径，每日买卖去重最多{cfg['portfolio']['max_traded_codes_per_day']}只；前{cfg['portfolio']['diagnostic_topn']}只仅作诊断，其持仓和换手数量不符合用户的5只限制。",
        f"\n风险评价要求自身最大回撤不超过{abs(cfg['portfolio']['max_drawdown']):.0%}、单边年换手≤{cfg['portfolio']['max_annual_one_way_turnover']}，平均仓位≥{cfg['portfolio']['min_average_exposure']:.0%}并有足够交易，避免靠长期空仓取得漂亮回撤。技术策略无固定年化收益门槛。少于{cfg['portfolio']['min_conclusion_days']}个交易日的窗口不升级正向等级；2025年单年结果仅提供年度回放数值，2026年是不完整年度观察。",
        '\n## 参数筛选与冻结',
        '\n| 轮次 | 发现期 | 验证期 | 点参数通过数 | 稳定家族数 | 进入账本的家族数 |','|---|---|---|---:|---:|---:|']
    for rd in cfg['rounds']:
        folder=out/rd['id'];train=json.loads((folder/'training_scan.json').read_text(encoding='utf-8'))
        frozen=json.loads((folder/'frozen_parameters.json').read_text(encoding='utf-8'))
        lines.append(f"|{rd['id']}|{rd['discovery']['start']}—{rd['discovery']['end']}|{rd['validation']['start']}—{rd['validation']['end']}|{sum(r['point_pass'] for r in train['records'])}|{frozen['robust_families']}|{len(frozen['finalists'])}|")
    summaries=[]
    for rd in cfg['rounds']:
        folder=out/rd['id'];frozen=json.loads((folder/'frozen_parameters.json').read_text(encoding='utf-8'))
        portfolios=json.loads((folder/'frozen_portfolios.json').read_text(encoding='utf-8'))
        factors=json.loads((folder/'validation_factors.json').read_text(encoding='utf-8'))['records']
        lines+=['\n### '+rd['id'],'\n| 类别 / 家族 | 原默认值 | 入围参数 | 最终参数 | 训练IC | 验证IC（沿用训练方向） | 控制暴露后验证IC |','|---|---:|---|---:|---:|---:|---:|']
        for selection in portfolios['chosen']:
            row=selection['row'];family=next(f for f in frozen['finalists'] if f['family_id']==row['family_id'])
            test=next(f for f in factors if f['parameter_id']==row['id']);neutral=test['exposure_control']['signed_rank_ic_mean']
            signed=row['rank_ic']*row['direction']
            lines.append(f"|{labels[row['category']]} / {row['label']}|{family['default']['value']}|{[r['value'] for r in family['parameters']]}|{row['parameter']}={row['value']}|{signed:.4f}|{test['validation_signed_ic']:.4f}|{'不可用' if neutral is None else f'{neutral:.4f}'}|")
        lines+=['\n以上控制项为成交额、实现波动率和股价。控制系数只使用当日已知X做横截面回归，不使用未来收益；未实现严格行业/市值中性化，不把成交额冒充市值。验证控制结果只作事后诊断，不反向改变冻结选择。',
            '\n| 前5只冻结组合 | 调仓 / 择时 | 验证年化 | 压力年化 | 默认参数年化 | 验证回撤 | 压力回撤 | 年换手 | 平均仓位 | 风险约束 |','|---|---|---:|---:|---:|---:|---:|---:|---:|---|']
        for selection in portfolios['chosen']:
            family=selection['family'];match=lambda phase,stress,top=primary:next(r for r in rows if r['round_id']==rd['id'] and r['family_id']==family and r['phase']==phase and r['stress']==stress and r['topn']==top)
            r=match('validation',False);s=match('validation',True);default=match('default_control',False)
            lines.append(f"|{r['label']} {r['parameter']}={r['value']}|{r['cadence']}日 / {r['timing_rule']}|{percentage(r['metrics']['annual_return'])}|{percentage(s['metrics']['annual_return'])}|{percentage(default['metrics']['annual_return'])}|{percentage(r['metrics']['max_drawdown'])}|{percentage(s['metrics']['max_drawdown'])}|{r['annual_one_way_turnover']:.2f}|{r['average_exposure']:.1%}|{'通过' if r['operational_risk_pass'] and s['operational_risk_pass'] else '未通过'}|")
            summaries.append(dict(factor_key=selection['row']['factor_key'],backtest_id=r['id'],verdict=r['verdict'],annual_return=r['metrics']['annual_return'],
                sharpe=r['metrics']['sharpe'],max_drawdown=r['metrics']['max_drawdown'],data_range=r['period'],round_id=rd['id'],
                improved_over_default=r['metrics']['annual_return']>default['metrics']['annual_return'],constraints_pass=r['constraints_pass'],
                notes='历史回放；退市/ST不完整；估值原始财报版本未核验；2025单年不足252交易日不升级正向结论'))
        lines+=['\n风险约束列联合检查基础/压力两套成本下的回撤、换手、交易间隔、最多持仓和每日交易股票数；完整的研究验收还检查仓位、交易活跃度、正收益和样本长度。',
            '\n| 相同冻结参数与规则 | 前5只年化 / 回撤 | 前10只年化 / 回撤（诊断） | 前5只年换手 | 前10只年换手 |','|---|---|---|---:|---:|']
        for selection in portfolios['chosen']:
            family=selection['family'];a=next(r for r in rows if r['round_id']==rd['id'] and r['family_id']==family and r['phase']=='validation' and not r['stress'] and r['topn']==primary)
            b=next(r for r in rows if r['round_id']==rd['id'] and r['family_id']==family and r['phase']=='validation' and not r['stress'] and r['topn']==cfg['portfolio']['diagnostic_topn'])
            lines.append(f"|{a['label']}|{percentage(a['metrics']['annual_return'])} / {percentage(a['metrics']['max_drawdown'])}|{percentage(b['metrics']['annual_return'])} / {percentage(b['metrics']['max_drawdown'])}|{a['annual_one_way_turnover']:.2f}|{b['annual_one_way_turnover']:.2f}|")
        follow=[r for r in rows if r['round_id']==rd['id'] and r['phase'].startswith('follow_through_') and not r['stress']]
        if follow:
            lines+=['\n事后延续检查：将本轮所有冻结组合一并延续到后续验证年，不只挑表现最好的组合。此诊断不改变原冻结选择，也不参与重新调参。',
                '\n| 原冻结因子 | 后续窗口 | 年化 | 压力年化 | 回撤 | 压力回撤 | 单边年换手 |','|---|---|---:|---:|---:|---:|---:|']
            for r in follow:
                s=next(x for x in rows if x['round_id']==r['round_id'] and x['family_id']==r['family_id'] and x['phase']==r['phase'] and x['stress'])
                lines.append(f"|{r['label']} {r['parameter']}={r['value']}|{r['period']['start']}—{r['period']['end']}|{percentage(r['metrics']['annual_return'])}|{percentage(s['metrics']['annual_return'])}|{percentage(r['metrics']['max_drawdown'])}|{percentage(s['metrics']['max_drawdown'])}|{r['annual_one_way_turnover']:.2f}|")
        preprocessing=json.loads((folder/'training_preprocessing.json').read_text(encoding='utf-8'));p=preprocessing['pca']
        lines.append(f"\n训练期参数冻结后的因子归一化、去相关与PCA：{len(preprocessing['all_families'])}个入围家族，相关性筛选后{len(preprocessing['selected_families'])}个，PCA {p['input_dimensions']}→{p['components']}维，训练累计解释方差{p['cumulative_explained_variance']:.2%}。验证期不重新拟合。该审计没有被用于训练收益模型或决定本轮单因子交易权重。")
    lines+=['\n## 冻结后的连续持仓回放',
        '\n下面从各轮验证期首日连续运行至观察期末，保留跨年的持仓和资金，不拼接年度重置账本。参数保持原冻结值；不依据延续表现重新选择参数。',
        '\n| 原轮次 / 因子 | 连续区间 | 年化 | 压力年化 | 回撤 | 压力回撤 | 单边年换手 | 周间隔及5只数量 |','|---|---|---:|---:|---:|---:|---:|---|']
    for r in rows:
        if r['phase']!='continuous_forward' or r['stress']:continue
        s=next(x for x in rows if x['round_id']==r['round_id'] and x['family_id']==r['family_id'] and x['phase']==r['phase'] and x['stress'])
        frequency=all(r['checks'][k] and s['checks'][k] for k in ['frequency','holdings','traded_codes'])
        lines.append(f"|{r['round_id']} / {r['label']} {r['parameter']}={r['value']}|{r['period']['start']}—{r['period']['end']}|{percentage(r['metrics']['annual_return'])}|{percentage(s['metrics']['annual_return'])}|{percentage(r['metrics']['max_drawdown'])}|{percentage(s['metrics']['max_drawdown'])}|{r['annual_one_way_turnover']:.2f}|{'通过' if frequency else '未通过'}|")
    lines+=['\n单边年换手3倍是本轮预先设置的低换手工作门槛，并非用户明确给出的硬上限；它与“普通交易至少相隔一周”是不同指标。表中分别展示，不把超出3倍等同于违反周频限制。',
        '\n## 本轮检验结果',
        f"\n归档真实账本 {len(rows)} 份；冻结的前{primary}只基础成本验证组合 {len(summaries)} 个，其中年化高于同规则默认参数对照的 {sum(r['improved_over_default'] for r in summaries)} 个。不能因选参提高训练IC就认为提高了后续可交易收益。",
        '\n### 2026年观察（沿用round_2冻结选择）', '\n| 因子 | 年化数值 | 最大回撤 | 单边年换手 | 平均仓位 |','|---|---:|---:|---:|---:|']
    for r in rows:
        if r['phase']=='observation' and not r['stress']:lines.append(f"|{r['label']}|{percentage(r['metrics']['annual_return'])}|{percentage(r['metrics']['max_drawdown'])}|{r['annual_one_way_turnover']:.2f}|{r['average_exposure']:.1%}|")
    lines+=['\n观察期不足一年，以上年化是折算数值，不据此升级正向结论，也不参与本轮选参。',
        '\n## 仍需补证据与使用边界',
        '\n- 当前代码池有幸存者偏差，历史退市全集与ST状态不完整。估值为供应商带日期历史序列，未核验原始财报版本和追溯重述；不是严格财报时点库。',
        '- 尚未做真正行业/市值中性化。20日IC是股票全排序指标，前5只的尾部结果和实际账本可能与IC方向不同。',
        '- 前5/10只标签统计不计成本，标签端点缺失会单独计数；真实投资结论只读每日账本。账本计入成本，持仓期末未强制出售，因此不是含期末清仓费用的兑现结果。',
        '- 当前账本未实现100股整手约束、盘口排队或成交量参与率限制；一字板过滤不等于完整模拟涨跌停可成交性。',
        '- 前10只仅比较集中度，允许的单日交易股票数量也不同，不能作为符合5只硬约束的可部署候选。',
        '- 周/月调仓、择时和持仓缓冲本身也构成发现期搜索；保留了所有训练账本与冻结清单，未计算PBO，不声称FDR覆盖了全部研究路径。',
        '\n## 复现与证据文件',
        '\n运行 `python scripts/factor_parameter_research.py --stage all`；也可依次运行scan、portfolio、report。相同实验身份可复用参数检验和账本；新实验应使用新配置和输出目录。',
        '\n- `experiment_manifest.json`：所有尝试参数、配置、来源身份与已知历史观察边界。',
        '- `round_*/training_parameters.csv`、`training_scan.json`：完整训练参数IC、校正、邻域和失败门槛。',
        '- `round_*/frozen_parameters.json`、`frozen_portfolios.json`：验证前冻结的参数/交易选择。',
        '- `round_*/runs/*.json`：逐笔交易、成本、每日账户、净值及拒单。',
        '- `round_*/validation_factors.json`、`training_preprocessing.json`：验证IC、暴露控制诊断和仅训练拟合的预处理状态。',
        '- `portfolio_results.csv/json`、`factor_summaries.json`：全部账本概要与结构化因子摘要。',
        '- `source_register.json`、`implementation_hashes.json`：来源和实现哈希。原始第三方数据仅保存在本地忽略目录，不再分发。',
        '\n[S_PBO] [The Probability of Backtest Overfitting](https://carmamaths.org/resources/jon/backtest2.pdf)。本报告是历史研究证据，不构成收益承诺。']
    write_json(out/'factor_summaries.json',summaries)
    (out/'因子参数稳健性研究.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(out/'因子参数稳健性研究.md')
