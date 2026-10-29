"""Render the four-axis evidence, rejection list and correlation limits in Chinese."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from contextlib import closing
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config,factor_definitions
from factors.research_controls import policy as control_policy
from scripts.mainboard_strategy_research import write_json


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='four_axis_factor_mining');args=parser.parse_args(argv)
    cfg=read_config(args.config);out=ROOT/cfg['output_dir'];defs={**control_policy()['legacy_indicators'],**factor_definitions()}
    result=json.loads((out/'factor_results.json').read_text(encoding='utf-8'))
    audit=json.loads((out/'correlation_pca.json').read_text(encoding='utf-8'))
    download=json.loads((out/'valuation_download.json').read_text(encoding='utf-8'))
    requested=cfg['valuation_source']['sample_stocks'];rule=cfg['effectiveness'];u=cfg['universe']
    price_stocks=max(r['universe_stocks'] for r in result['records'] if r['category']!='valuation')
    horizons='/'.join(map(str,cfg['ic']['horizons']))
    with closing(sqlite3.connect(ROOT/cfg['valuation_source']['db'])) as con:
        rows,stocks,first,last=con.execute('SELECT count(*),count(distinct code),min(date),max(date) FROM valuations WHERE source=?',(cfg['valuation_source']['source'],)).fetchone()
    sources=[dict(source_id='S_PRICE',title='本地冻结主板日线与资格审计',provider='platform_cache',access_class='not_applicable',
                  evidence_label='实测历史样本',snapshot_hash=result['snapshot_hash'],period=cfg['research_windows']['full'],
                  canonical_path=str(out/'data_audit.json'),redistribution=False),
             dict(source_id='S_VALUE',title='东方财富个股历史估值 RPT_VALUEANALYSIS_DET',provider='Eastmoney',access_class='public_full',
                  evidence_label='供应商历史序列',canonical_url='https://datacenter-web.eastmoney.com/api/data/v1/get',
                  query=f'reportName=RPT_VALUEANALYSIS_DET;固定{requested}只代码样本;每股一次历史查询',rows=rows,stocks=stocks,first_date=first,last_date=last,
                  original_financial_vintage_verified=False,redistribution=False),
             dict(source_id='S_DOC',title='AKShare 官方股票估值数据接口文档',provider='AKShare',access_class='public_full',
                  evidence_label='接口一手文档',canonical_url='https://github.com/akfamily/akshare/blob/main/docs/data/stock/stock_valuation.md',
                  supports='stock_value_em 日度历史 PE、PB、PS、经营现金流市现率、PEG 等字段定义')]
    write_json(out/'source_register.json',dict(checked_at=datetime.now(timezone.utc).isoformat(),sources=sources))
    lines=['# 四类因子挖掘：历史统计证据与去冗余',
        f"\n研究口径：A股主板，{cfg['research_windows']['full']['start']}—{cfg['data_end']}；本次执行日 {cfg['as_of']}，Asia/Shanghai。动量、波动率和流动性使用{price_stocks}只冻结代码池；估值使用分析收益前预定的{requested}只等距代码样本，已取得{stocks}只、{rows:,}条历史估值记录。股票数据未延伸至执行日。[S_PRICE][S_VALUE]",
        '\n本报告的“通过”只表示在指定历史样本内满足预设统计门槛，不等同于成本后可交易超额收益；没有在本轮完成四类因子组合的持仓、换手或回撤回测。',
        '\n## 筛选规则与结论',
        f"\n信号日收盘计算因子；标签为下一交易日开盘至其后{horizons}个交易日开盘收益，进入和退出日要求正成交量、最高价高于最低价。每{cfg['ic']['sample_every_days']}日取样；每次至少{cfg['ic']['min_stocks']}只有效股票。只使用当日满足{u['min_history_days']}日观察历史、{u['liquidity_window']}日均成交额不少于{u['min_average_amount']/10000:g}万元的股票。未可靠覆盖历史ST状态，不能宣称已完整排除ST。",
        f"\n{rule['primary_horizon']}日主检验要求 |RankIC|≥{rule['min_abs_rank_ic']}、覆盖率≥{rule['min_coverage']:.0%}、同向年份比例≥{rule['min_year_direction_fraction']:.0%}、有效横截面≥{rule['min_observations']}、HAC自相关修正后 BH-FDR q≤{rule['fdr']}、至少{rule['min_same_direction_horizons']}个期限方向一致、经验优选方向的五分组多空差为正。HAC滞后按重叠持有期设置；FDR覆盖全部{len(result['records'])}个预设假设。年份方向、优选符号均为全样本描述，未冻结用于独立验证。",
        '\n| 类别 | 检验数 | 统计通过数 | 类内相关性筛选保留数 |', '|---|---:|---:|---:|']
    for key,label in cfg['category_labels'].items():
        tested=sum(r['category']==key for r in result['records'])
        lines.append(f"|{label}|{tested}|{result['effective_counts'][key]}|{audit['per_category_counts'][key]}|")
    lines+=['\nFDR只校正本轮固定假设，不覆盖以前研究的全部参数与策略搜索，不能消除历史多轮选择偏差。这里没有新的盲测。',
             '\n“类内保留数”使用按统计排序的贪心筛选，代表当前筛选结果，并非最大独立集合的数学上界。量价类的负IC表示短期反转/回落偏好，不能解释为正动量追涨已获支持。',
             '\n## 各类优先跟踪因子', '\n以下每类最多列5个统计通过候选，按 |20日RankIC| 排序；完整假设和失败项保留在 CSV/JSON。它们未必相互独立。']
    for key,label in cfg['category_labels'].items():
        lines+=['\n### '+label,'\n| 因子 | 经验优选 | 20日RankIC | FDR q | 同向年份 | 覆盖率 | 20日优选组－劣选组 |','|---|---|---:|---:|---:|---:|---:|']
        for r in [r for r in result['records'] if r['category']==key and r['effective']][:cfg['minimum_per_category']]:
            lines.append(f"|{r['label']} `{r['id']}`|{'高值' if r['direction']>0 else '低值'}|{r['rank_ic']:.4f}|{r['q_value']:.3g}|{r['year_stability']:.0%}|{r['coverage']:.1%}|{r['spread']:.2%}|")
        if result['effective_counts'][key]<cfg['minimum_per_category']:lines.append('\n**本类尚未达到5个通过项，不能把待验证候选补写为有效因子。**')
    lines+=['\n## 相关性与PCA',
        f"\n为保持可比性，所有通过项统一使用同一个{requested}代码池，按日横截面百分位归一化，再取固定的{audit['sample_rows']:,}个有效观测；共同完整观测{audit['complete_rows']:,}个。相关性门槛和PCA使用完全相同的完整行，避免缺失值改变样本后门槛失效。",
        f"\n以 |r|≤{audit['max_representative_abs_corr']} 为上限，按类别轮转贪心选出 {len(audit['representatives'])} 个跨类代表，实际最大绝对相关系数{audit['actual_max_abs_corr']:.4f}。相关系数是同样本上归一化因子的Pearson相关，不是未来时期的保证。每类至少5个统计通过，不等于每类至少5个低相关独立因子。",
        '\n跨类保留：'+ '、'.join('`'+n+'`' for n in audit['representatives'])+'。']
    for key,title in [('pca_all_effective','全部统计通过项'),('pca_low_correlation','先去相关代表集')]:
        p=audit[key]
        if p['state']=='complete':lines.append(f"\n{title}：{p['input_dimensions']}维 → {p['components']}维，累计解释方差{p['cumulative_explained_variance']:.2%}；样本内主成分最大交叉相关{p['max_abs_training_pc_corr']:.3g}。按全体输入统一PCA，**不是按四类分别分配主成分**。低相关输入通常需要保留较多维，不能预先强制固定为5维。")
    lines+=['\n此PCA仅为技术研究的全段描述。若进入机器学习，因子方向、有效性筛选、缺失处理、相关性选列、标准化及PCA必须全部只在每轮训练集拟合，再冻结应用到验证集。这里生成的全段选择结果不能直接作为无泄漏的ML特征筛选结果。',
        '\n## 全部参数与计算定义',
        '\n窗口均为交易日观察窗口；原始价格类因子的复权与缺失规则沿用冻结快照。估值倒数允许负分母的亏损值，零值转缺失；1/PEG另要求PEG>0。PB/PS为历史供应商口径；PCF为经营现金流TTM口径。复合价值、相对历史估值和估值差属于衍生口径，并非新取得的独立财务基本面。',
        '\n| 编号 | 平台因子编号 | 计算操作 | 默认参数 | 数据依赖 |','|---|---|---|---|---|']
    for r in result['records']:
        definition=r['definition'];canonical=definition.get('source_factor','axis_'+r['id']);spec=defs.get(canonical,definition)
        lines.append('|'+ '|'.join([r['id'],canonical,str(spec.get('operation',spec.get('formula','平台既有计算'))).replace('|','/'),json.dumps(spec.get('params',{}),ensure_ascii=False),', '.join(spec.get('requires',[]))])+'|')
    lines+=['\n## 未通过项与边界', '\n| 因子 | 失败门槛 |','|---|---|']
    for r in result['records']:
        if not r['effective']:lines.append(f"|{r['label']} `{r['id']}`|{', '.join(k for k,v in r['gates'].items() if not v)}|")
    lines+=['\n供应商历史序列有日期，不等于已核验原始财报版本。未核验追溯重述、财报发布时点和修订，因此估值结论为供应商历史样本探索证据，仍需真正的财报版本库复验。序列按日期精确匹配，没有使用当前值回填历史，也没有对缺失的历史估值向前或向后填充。[S_VALUE][S_DOC]',
        f"\n下载成功{download['completed']}/{download['requested']}，剩余失败{download['failed']}；固定样本未因收益优劣替换股票。股票池存在幸存者偏差，未重建完整历史退市池；历史ST字段不足。换手类证据仅从2019年开始使用。未做行业、市值和市场beta中性化，成交额及估值因子可能含规模/行业暴露。低流动性偏好仍须遵守可成交性和容量约束。",
        f'\n因子覆盖率只统计当日合资格股票，估值分母限固定{requested}只样本；价格类分母为全{price_stocks}只代码池中的当日合资格股票，因此不能把不同范围的覆盖率直接等同。五分组差是单次持有期的毛多空价差，未计交易成本、融券约束，不能当成年化或多头组合收益。',
        '\n## 数据与证据登记',
        '\n- [S_PRICE] `data_audit.json`、`policy.json`、`factor_results.json/csv`：真实本地历史计算，可复跑；第三方原始行情不对外再分发。',
        '- [S_VALUE] `valuation_download.json`、`valuation_universe.json`：下载状态和收益分析前确定的代码池；历史记录存本地忽略的SQLite。',
        '- [S_DOC] [AKShare官方估值接口文档](https://github.com/akfamily/akshare/blob/main/docs/data/stock/stock_valuation.md)：仅支持字段来源与接口定义，不为本地统计表现背书。',
        '- `factor_rank_correlations.csv`、`factor_pairwise_available_correlations.csv`、`correlation_pca.json`：共同样本/可用成对样本相关矩阵、保留因子及PCA载荷。',
        '- `source_register.json`：来源、访问状态、期间和版本限制。研究结果仅供策略开发，不构成收益承诺。']
    (out/'四类因子挖掘报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(out/'四类因子挖掘报告.md')


if __name__=='__main__':main()
