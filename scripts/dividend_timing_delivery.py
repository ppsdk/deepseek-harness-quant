"""Regenerate the Chinese evidence report from saved ledgers, without market data export."""
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from scripts.dividend_timing_research import load_data,panels_for,write
from backtest.research_dividend_timing import volatility_budget


def main():
    base=read_config('dividend_timing_research');cfg=read_config('dividend_volatility_research')
    folder=ROOT/cfg['output_dir'];data=json.loads((folder/'results.json').read_text(encoding='utf-8'))
    cash_folder=ROOT/base.get('cashflow_output_dir','output/strategy_research/dividend_timing_cashflow_v1')
    cash_vol_folder=ROOT/cfg.get('cashflow_output_dir','output/strategy_research/dividend_volatility_cashflow_v1')
    cash_data=json.loads((cash_folder/'results.json').read_text(encoding='utf-8')) if (cash_folder/'results.json').exists() else None
    cash_vol_data=json.loads((cash_vol_folder/'results.json').read_text(encoding='utf-8')) if (cash_vol_folder/'results.json').exists() else None
    overlay_folder=ROOT/cfg.get('overlay_output_dir','output/strategy_research/dividend_overlay_v1')
    overlay_cash_folder=ROOT/cfg.get('overlay_output_dir','output/strategy_research/dividend_overlay_v1').replace('_v1','_cashflow_v1')
    overlay_data=json.loads((overlay_folder/'results.json').read_text(encoding='utf-8')) if (overlay_folder/'results.json').exists() else None
    overlay_cash_data=json.loads((overlay_cash_folder/'results.json').read_text(encoding='utf-8')) if (overlay_cash_folder/'results.json').exists() else None
    trend=json.loads((ROOT/base['output_dir']/'results.json').read_text(encoding='utf-8'))
    rows=data['records'];selected=data['selected'];frames,audit=load_data(base)
    def get(code,phase,variant,stress=False):
        return next(r for r in rows if r['code']==code and r.get('phase')==phase and r['variant']['id']==variant and r['stress']==stress)
    def get_cash(code,phase,variant,stress=False):
        return next(r for r in cash_data['records'] if r['code']==code and r.get('phase')==phase and r['variant']['id']==variant and r['stress']==stress)
    def get_cash_vol(code,phase,variant,stress=False):
        return next(r for r in cash_vol_data['records'] if r['code']==code and r.get('phase')==phase and r['variant']['id']==variant and r['stress']==stress)
    def get_overlay(code,phase,overlay,stress=False,cash=False):
        source=overlay_cash_data if cash else overlay_data
        return next(r for r in source['records'] if r['code']==code and r.get('phase')==phase and r['overlay']==overlay and r['stress']==stress)
    lines=['# 红利低波个股与基金择时研究', '',
           f"研究截止日：{base['as_of']}（Asia/Shanghai）；资料核验时间：{base['checked_at']}。行情实际截止：{base['data_end']}。",
           '', '## 结论和两套规则', '',
           '本次建议采用低频仓位管理作为风险控制候选，不把它称为已验证的收益增强策略。四只水电/公用事业个股、两只ETF用于交叉检验；股票不是全市场历史时点红利低波组合。初轮均线择时未通过效果检验，已保留失败结果。', '',
           '|对象|波动估计|目标仓位公式|调整频率|', '|---|---|---|---|']
    latest={}
    for group,spec in base['groups'].items():
        v=selected[group];floor=cfg['floors'][group];code=spec['primary']
        budget,vol=volatility_budget(panels_for(frames[code],code),v['window'],v['target_volatility'],floor,cfg['annual_days'])
        latest[group]=dict(code=code,date=str(budget.index[-1]),realized_volatility=float(vol.iloc[-1]),target_exposure_pct=float(budget.iloc[-1]))
        lines.append(f"|{spec['assets'][code]}等{'个股' if group=='stock' else '场内基金'}|过去{v['window']}个交易日收盘简单收益标准差 × √{cfg['annual_days']}|w = min(100%, max({floor:.0%}, {v['target_volatility']:.0%} / σ))|每{cfg['rebalance_days']}交易日检查，信号次日开盘尝试调整|")
    lines += ['', 'w 是为该标的预留的子账户内仓位，不是全部家庭资产或投资账户的单股配置比例。12%是风险预算参数，不是收益目标，也不保证实际波动或回撤。波动很低时可能满仓子账户；波动增加时逐步减仓。两类策略都不依赖抢分红、追涨或预测来水。', '',
              '例如：年化波动为20%时，12%预算对应60%仓位；年化波动为10%时，上限为100%。个股保留30%下限是本次测试的参数，不能理解为公司基本面恶化时仍必须持有。没有可用波动估计时保持现金。', '',
              '严格使用收盘后已知数据生成仓位，下一交易日开盘尝试成交；普通交易间隔不少于10个交易日，无法成交时沿用原账本等待。仓位目标在计划期内冻结。实盘可执行份额需按100股/份取整，本次模拟使用复权价格和调整份额。', '',
              '## 主样本完整区间比较', '',
              '以下是各主样本2020年起至行情截止日的连续账本，包含发现期；不能替代样本外证据。匹配仓位对照的目标取自动态策略事后平均仓位，只用于解释少持仓效应，且因调仓阈值实际平均仓位会略有差异。', '',
              '|主样本|方案|实际起点|年化|最大回撤|夏普 rf=0|平均仓位|成交笔数|', '|---|---|---|---:|---:|---:|---:|---:|']
    for group,spec in base['groups'].items():
        code=spec['primary']
        for variant,title in [('buy_hold','买入持有'),('volatility_budget','波动预算择时'),('matched_static_exposure','匹配目标仓位对照')]:
            r=get(code,'full',variant);m=r['metrics']
            lines.append(f"|{spec['assets'][code]}|{title}|{r['actual_start']}|{m['annual_return']:.2%}|{m['max_drawdown']:.2%}|{m['sharpe']:.2f}|{m['average_exposure']:.1%}|{m['trades']}|")
    lines += ['', '## 时间隔离窗口和压力成本', '',
              '初轮均线参数仅按2020—2022发现期选取；观察到均线失败后，冻结波动预算补测网格（20/60日、8%/10%/12%），再仅按发现期跨标的中位数Calmar选取。后续窗口未参与波动参数排序，但此前已被查看，因此整体属于探索性时间隔离回放，不是全新盲测。各窗口独立从现金开始，窗口内账户连续，跨年不清仓；完整区间另行连续运行，未拼接重置账户的收益。', '',
              '|主样本|窗口|波动择时年化/回撤|压力年化/回撤|买入持有年化/回撤|', '|---|---|---|---|---|']
    for group,spec in base['groups'].items():
        code=spec['primary']
        for phase in ['discovery','validation','forward_replay']:
            r=get(code,phase,'volatility_budget');s=get(code,phase,'volatility_budget',True);b=get(code,phase,'buy_hold')
            m,sm,bm=r['metrics'],s['metrics'],b['metrics'];period=base['periods'][phase]
            lines.append(f"|{spec['assets'][code]}|{period['start']}—{period['end']}|{m['annual_return']:.2%}/{m['max_drawdown']:.2%}|{sm['annual_return']:.2%}/{sm['max_drawdown']:.2%}|{bm['annual_return']:.2%}/{bm['max_drawdown']:.2%}|")
    lines += ['', '## 跨标的检验', '', '|标的|实际起点|波动择时年化/回撤|压力年化/回撤|买入持有年化/回撤|', '|---|---|---|---|---|']
    for group,spec in base['groups'].items():
        for code,name in spec['assets'].items():
            r=get(code,'full','volatility_budget');s=get(code,'full','volatility_budget',True);b=get(code,'full','buy_hold')
            m,sm,bm=r['metrics'],s['metrics'],b['metrics']
            lines.append(f"|{name} {code}|{r['actual_start']}|{m['annual_return']:.2%}/{m['max_drawdown']:.2%}|{sm['annual_return']:.2%}/{sm['max_drawdown']:.2%}|{bm['annual_return']:.2%}/{bm['max_drawdown']:.2%}|")
    lines += ['', '大部分绝对回撤改善伴随平均仓位降低；对照表不能证明择时有稳定超额。长江电力、512890的近期回放都没有明显收益优势，采用该规则的理由只能是风险预算和仓位纪律。不同基金跟踪不同指数，515450不能被当作512890完全等价替代；它的上市和最大网格预热期较晚，实际起点单列，不能直接比较不同起点的年化。', '',
              '## 被否决的慢均线方案', '',
              '初轮个股选择MA120、上下2%缓冲并连续3日确认，初始30%底仓与70%择时仓分别记账，桶间不再平衡；ETF选择MA200同样缓冲和确认并允许空仓。结果不支持将其作为首选。', '',
              '|主样本|初轮方案完整区间年化/回撤|同期持有年化/回撤|', '|---|---|---|']
    for group,spec in base['groups'].items():
        code=spec['primary'];key=trend['selected'][group]['id']
        r=next(r for r in trend['records'] if r['code']==code and r.get('phase')=='full' and r['variant']['id']==key and not r['stress'])
        b=next(r for r in trend['records'] if r['code']==code and r.get('phase')=='full' and r['variant']['id']=='buy_hold' and not r['stress'])
        m,bm=r['metrics'],b['metrics'];lines.append(f"|{spec['assets'][code]}|{m['annual_return']:.2%}/{m['max_drawdown']:.2%}|{bm['annual_return']:.2%}/{bm['max_drawdown']:.2%}|")
    lines += ['', '## 现金分红与份额拆分账本', '',
              '主结果同时保留供应商前复权代理和未复权现金流账本。前复权结果适合快速比较但会随供应商后续公司行为修订；现金流账本按登记日、除权日、派息日逐笔入账，个股卖出按FIFO计算差别化红利税，ETF按基金免税口径处理。', '',
              '|主样本|口径|买入持有年化|最大回撤|现金分红总额|已缴红利税|净分红|', '|---|---|---:|---:|---:|---:|---:|']
    if cash_data and cash_vol_data:
        for group,spec in base['groups'].items():
            code=spec['primary']; q=get(code,'full','buy_hold'); c=get_cash(code,'full','buy_hold'); d=c.get('cash_distributions',{})
            lines.append(f"|{spec['assets'][code]}|前复权代理|{q['metrics']['annual_return']:.2%}|{q['metrics']['max_drawdown']:.2%}|—|—|—|")
            lines.append(f"|{spec['assets'][code]}|未复权现金流|{c['metrics']['annual_return']:.2%}|{c['metrics']['max_drawdown']:.2%}|{d.get('gross_dividends',0.):.2f}|{d.get('dividend_tax_paid',0.):.2f}|{d.get('net_dividends',0.):.2f}|")
        lines += ['', '|主样本|波动预算现金流年化|最大回撤|现金分红总额|已缴红利税|', '|---|---:|---:|---:|---:|']
        for group,spec in base['groups'].items():
            code=spec['primary']; c=get_cash_vol(code,'full','volatility_budget'); d=c.get('cash_distributions',{})
            lines.append(f"|{spec['assets'][code]}|{c['metrics']['annual_return']:.2%}|{c['metrics']['max_drawdown']:.2%}|{d.get('gross_dividends',0.):.2f}|{d.get('dividend_tax_paid',0.):.2f}|")
        lines += ['', '现金流账本覆盖当前本地已取得的公告和基金分红数据；行情与公司行为文件位于 `data/private/`，不随代码提交。若数据源后续修订公告，应重新获取并保留数据指纹。']
    if overlay_data and overlay_cash_data:
        labels={'base':'波动预算基线','market_gate':'沪深300门控','bollinger':'布林带限制','kdj':'KDJ限制','combo':'三者组合'}
        lines += ['', '## 技术叠加层对照', '',
                  '在冻结个股60日、ETF20日、目标年化波动12%的波动预算后，补测沪深300趋势门控、布林带过热限制、KDJ高位转弱限制及三者组合。叠加层只压低目标仓位，仍由同一个T+1开盘账本执行；现金流版本继续计入现金分红、个股红利税和ETF份额拆分。', '',
                  '|标的|方案|年化|最大回撤|夏普|平均仓位|', '|---|---|---:|---:|---:|---:|']
        for group,spec in base['groups'].items():
            for code in [spec['primary']]:
                for overlay_name in cfg['overlay']['variants']:
                    r=get_overlay(code,'full',overlay_name,cash=True);m=r['metrics']
                    lines.append(f"|{spec['assets'][code]} {code}|{labels.get(overlay_name,overlay_name)}|{m['annual_return']:.2%}|{m['max_drawdown']:.2%}|{m['sharpe']:.2f}|{m['average_exposure']:.1%}|")
        lines += ['', '样本外 forward replay（2025-01-01 至 2026-10-29）显示，长江电力基线与三者组合的年化/回撤分别为 '
                  f"{get_overlay('600900.SH','forward_replay','base',cash=True)['metrics']['annual_return']:.2%}/{get_overlay('600900.SH','forward_replay','base',cash=True)['metrics']['max_drawdown']:.2%} 和 "
                  f"{get_overlay('600900.SH','forward_replay','combo',cash=True)['metrics']['annual_return']:.2%}/{get_overlay('600900.SH','forward_replay','combo',cash=True)['metrics']['max_drawdown']:.2%}；512890基线与三者组合分别为 "
                  f"{get_overlay('512890.SH','forward_replay','base',cash=True)['metrics']['annual_return']:.2%}/{get_overlay('512890.SH','forward_replay','base',cash=True)['metrics']['max_drawdown']:.2%} 和 "
                  f"{get_overlay('512890.SH','forward_replay','combo',cash=True)['metrics']['annual_return']:.2%}/{get_overlay('512890.SH','forward_replay','combo',cash=True)['metrics']['max_drawdown']:.2%}。沪深300门控和组合主要改善回撤但牺牲收益，布林带边际影响很小；KDJ在长江电力样本触发有限。当前应把它们作为可解释的风险控制候选，不称为稳定收益增强策略。"]
    lines += ['', '## 场外基金和实际执行', '',
              '上述已运行的基金策略是场内ETF策略。若持有ETF联接、普通指数基金或其他场外基金，可以把对应ETF的仓位信号作为研究参考，但应在信号出现后按该基金申购赎回规则执行，不能假定以ETF下一开盘成交。场外基金的未知净值、确认时间、赎回到账、短期赎回费及销售服务费必须单独回测；本次没有完成这部分验证。不能把ETF结果原样承诺给场外基金。', '',
              '个股需关注已公开的分红实施、盈利和现金流变化；本次现金流账本只使用已实施且已取得的历史公司行为，不把未来分红方案回填到历史。当前股息率应以已知分红与真实未复权价格计算，不能拿复权价作为分母。', '',
              '## 数据与成本边界', '',
              '- 个股使用既有缓存；ETF通过AKShare腾讯备用源下载到独立SQLite，只经DailyCache读取，行情不公开分发。长江电力少于同区间其他股票的记录未被补齐或冒充交易日，缺源与停牌需进一步区分。',
              '- 前复权结果是收益代理；现金流结果使用未复权价格、实际现金分红和份额拆分，并将个股红利税计入卖出现金流。现金分红不自动再投资，闲置现金收益为0。未来公司行为可能重写供应商复权值，但不会改变已归档的现金行动账本。',
              '- 基础成本为双边佣金3bp、最低5元、每边滑点10bp；压力成本佣金5bp、滑点30bp。股票卖出印花税固定10bp（未按2023年调整分段，对近年较保守），ETF印花税为0。基金自身运作费用已反映在价格/净值，不重复扣费。[CSRC-ETF-2023]',
              '- 个股红利税按FIFO持有期采用20%/10%/0%三档；ETF现金分红按基金免税口径处理。实盘税务仍以账户实际扣缴为准。[CYPC-DIV-2025]',
              '- 闲置资金收益设为0；未模拟债券或货币基金轮换、整手、真实涨跌停队列、盘口冲击及成交量参与率。一字行情过滤使用成交日完整日线，属于保守事后可成交筛查。风险预算不是最大回撤止损线。', '',
              '## 来源与复现', '',
              '外部事实仅用于核验标的、分红及交易制度，不参与发现期参数选择。查询渠道为公开网页检索及公司/交易所/监管原文；未查询付费研报或未授权数据库。CYPC公司站PDF超时后改用指定披露平台巨潮资讯同一公告全文，未用搜索摘要代替公告。', '',
              '|source_id|原文及发布日期|访问状态|支持范围|', '|---|---|---|---|']
    for s in base['sources']:
        lines.append(f"|{s['id']}|[{s['title']}]({s['url']})，{s['published_at']}|{s['access_class']}|{s['evidence']}|")
    lines += ['', '复现命令：', '', '```powershell', 'python -X utf8 scripts/dividend_timing_research.py --download',
              'python -X utf8 scripts/dividend_timing_research.py --cashflow',
              'python -X utf8 scripts/dividend_volatility_research.py --cashflow',
              'python -X utf8 scripts/dividend_volatility_research.py --overlays --cashflow',
              'python -X utf8 scripts/dividend_volatility_research.py --overlays',
              'python -X utf8 validation/audit_dividend_timing.py',
              'python -X utf8 scripts/dividend_volatility_research.py','python -X utf8 scripts/dividend_timing_delivery.py',
              'python -X utf8 -m unittest validation.test_dividend_timing -v','```', '',
              f"前复权初轮账本：`{base['output_dir']}/runs/`；现金流初轮账本：`{base.get('cashflow_output_dir','output/strategy_research/dividend_timing_cashflow_v1')}/runs/`；前复权补测账本：`{cfg['output_dir']}/runs/`；现金流补测账本：`{cfg.get('cashflow_output_dir','output/strategy_research/dividend_volatility_cashflow_v1')}/runs/`；前复权技术叠加账本：`{cfg.get('overlay_output_dir','output/strategy_research/dividend_overlay_v1')}/runs/`；现金流技术叠加账本：`{cfg.get('overlay_output_dir','output/strategy_research/dividend_overlay_v1').replace('_v1','_cashflow_v1')}/runs/`；每轮保留配置、数据指纹、冻结选择及全部对照。",
              '', '本报告提供可执行的研究规则和已运行证据，不将历史结果解释为未来收益承诺。']
    path=ROOT/'docs/红利低波个股与基金择时研究.md'
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    write(folder/'latest_signal_diagnostic.json',latest)
    print(path)
    print(json.dumps(latest,ensure_ascii=False))


if __name__=='__main__':main()
