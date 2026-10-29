"""Audit real fills and produce a bounded Chinese event-trading research report."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from factors.research_library import read_config
from scripts.mainboard_strategy_research import write_json

def main():
    policy=read_config('mainboard_research');event=read_config('event_trading_research')
    out=ROOT/policy['output_dir']/event['output_subdir'];results=json.loads((out/'results.json').read_text(encoding='utf-8'))
    audits=[];archives=[];table=[]
    for spec in results['rounds']:
        folder=out/spec['round_id']
        for record in spec['experiments']:
            path=folder/'runs'/(record['strategy']+'_'+record['variant']+'_'+record['period']+'.json')
            run=json.loads(path.read_text(encoding='utf-8'));positions={d:i for i,d in enumerate(run['dates'])};by_day={}
            for trade in run['trades']:
                assert trade['signal_date']<trade['date'],str(path)
                assert trade['trigger']!='market_risk_exit' or trade['side']=='sell'
                by_day.setdefault(trade['date'],[]).append(trade)
            last=None
            for day,trades in sorted(by_day.items()):
                assert len({t['code'] for t in trades})<=run['params']['max_traded_codes_per_day']
                if any(t['trigger']!='market_risk_exit' for t in trades) and last is not None:
                    assert positions[day]-positions[last]>=run['params']['min_trade_interval_days']
                last=day
            assert max(row['holding_count'] for row in run['daily_account'])<=policy['targets']['max_holdings']
            audits.append(dict(round_id=spec['round_id'],strategy=record['strategy'],variant=record['variant'],period=record['period'],fills=len(run['trades']),passed=True))
        for item in json.loads((folder/'platform_archives.json').read_text(encoding='utf-8')):
            archived=json.loads((ROOT/'output/backtest_archive'/(item['name']+'.json')).read_text(encoding='utf-8'))
            original=next(r for r in spec['validation'] if r['strategy']==item['strategy'])
            assert archived['params']['trade_mode']=='event' and archived['archived']
            assert abs(archived['metrics']['annual_return']-original['metrics']['annual_return'])<1e-10
            assert not archived['research_audit']['formal_acceptance']
            archives.append(dict(round_id=spec['round_id'],name=item['name'],path=str(ROOT/'output/backtest_archive'/(item['name']+'.json'))))
        for r in spec['validation']:
            m=r['metrics'];table.append(f"| {spec['round_id']} | {r['title']} | {r['variant_label']} | {m['annual_return']:.2%} | {m['annual_excess_pp']:.2f} | {abs(m['max_drawdown']):.2%} | {r['annual_one_way_turnover']:.2f} | {r['cash_day_fraction']:.1%} | {r['emergency_exit_dates']} |")
    passed=sum(r['target_pass'] for s in results['rounds'] for r in s['validation'])
    write_json(out/'execution_audit.json',dict(ok=True,runs=len(audits),validation_archives=len(archives),t_plus_one=True,ordinary_frequency_verified=True,emergency_exits_separate=True,daily_traded_symbol_cap=5,holdings_cap=5,details=audits))
    write_json(out/'platform_archives.json',archives)
    text='''# 均线条件触发与强风险空仓研究

研究记录日期：2026-10-27，Asia/Shanghai。使用本机截至2026-10-23的既有股票快照；本轮未更新行情、未重新判断当前大盘。第三方原始行情仅保留在本机。

## 已确认约束与实现

- 最大回撤指策略自身净值最大回撤，目标不超过10%；收益目标沿用绝对年化20%或相对沪深300价格指数的年化超额20个百分点。
- 最多持有5只；每个交易日买卖涉及的股票合并去重不超过5只。
- 普通交易至少间隔5个交易日；比较另一个至少间隔20个交易日的版本。这是成交之间的最低间隔，没有到期强制换股。没有条件就继续持有或空仓。
- 用户允许紧急清仓例外：强风险信号出现后次日开盘尝试退出；退出后的普通买入仍等待最低间隔且满足入场条件。紧急退出次数单列。
- 所有信号使用前一收盘数据，下一交易日开盘执行；过滤一字行情、停牌及无效价格，计佣金、卖出税费和滑点。无法成交会保留仓位，不保证清仓或10%回撤。

## 本轮设计

每轮沿用该发现期已经冻结的3种因子策略及其选股机制，分别比较4个执行方案，共12个候选组合。第一轮2019—2022发现→2023—2024验证；第二轮2023—2024发现→2025验证。按发现期的联合目标、频率约束和Calmar选执行方案，然后冻结验证。

基础方案：收盘高于MA20且MA20高于MA60连续2日才允许买入；连续2日低于MA20时允许退出。三个对照分别只改变一个设置：最低交易间隔改为20日、退出均线改为MA60、目标仓位改为60%。因子不新增、不重新在验证期挑选。

指数强风险规则：指数低于MA120且MA20低于MA60，或距60日高点回撤超过10%时进入空仓状态；指数收盘高于MA60且MA20高于MA60连续3日后才解除。存在状态滞后，解除风险与股票入场仍需同时满足。

所有选择、阈值与变体来自配置，页面通过研究配置API读取。回测页新增“均线条件触发”模式；选择后隐藏固定调仓间隔，显示均线周期与最小成交间隔。

## 验证结果

完成24次发现期、6次验证期、6次全区间诊断和6次提高成本压力实验，共42次。6个验证方案中通过收益与回撤联合门槛的数量：PASS_COUNT。

| 轮次 | 策略 | 发现期选定方案 | 年化 | 年化超额（百分点） | 最大回撤 | 单边年换手倍数 | 完全空仓天数占比 | 紧急退出日期数 |
|---|---|---|---:|---:|---:|---:|---:|---:|
TABLE_ROWS

本轮全部42次实验的普通交易频率、最多持仓数和每日交易股票数检查通过。6个验证方案均满足10%回撤约束，但收益不足，2025年3个方案均为负收益。因此不将这批方案标为达标策略。

第二轮发现期部分结果较好，例如低波动因子＋MA60退出在2023—2024年化18.13%、最大回撤8.28%，但其2025验证年化为-3.76%。这个差异不能通过改选2025表现较好的参数消除；否则会进一步污染验证。

低换手因子的第一轮冻结方案在2023—2024年化5.31%、最大回撤6.42%、单边年换手1.43倍；全区间诊断年化3.41%、最大回撤9.21%。它说明低风险和低频可以实现，但尚未满足20%收益目标。全区间数据包含发现期及其之前的年份，不能当作新增样本外证明。

## 审计与边界

- 验证年份在之前探索中已经查看过，本轮属于按时间冻结方案的探索性回放，不是新的未触碰盲测。
- 历史ST字段及退市全集仍缺失，存在覆盖与幸存者偏差。2019初期历史长度不足的空仓期保留在全窗口指标中。
- 2025按用户指定自然年验证，缓存包含243个交易日；不因年化数字就认定长期有效。
- 沪深300参考为价格指数，股票使用前复权口径，不能声称相对全收益指数的严格净超额。
- 6个验证结果已进入现有平台档案；逐笔成交、每日账户、拒单和规则参数单独保存。参数快照和归档重放的年化收益一致。
- 61项相关测试通过；回测页面及择时API返回200；Chrome验证了模式切换、自定义均线、最小间隔提交及旧模式保留，无JavaScript异常。现有运行中的后端未重启，需重启后端并刷新页面启用新配置与逻辑。

## 文件与复现

- `results.csv`：42次实验指标；`results.json`：各轮冻结选择。
- `round_1/runs/`、`round_2/runs/`：逐笔成交、净值、每日账户与拒单。
- `execution_audit.json`：按真实成交独立核对T+1、普通交易间隔和股票数限制。
- `platform_archives.json`：6个正式平台存档的位置；存档不等于策略正式验收通过。
- `browser_verification.json`：页面与API回归；`event-controls.png`：界面预览。
- 运行 `.venv/Scripts/python.exe scripts/mainboard_event_research.py` 重放研究；运行 `.venv/Scripts/python.exe scripts/mainboard_event_report.py` 审计并生成本报告。

下一步可研究的方向是减少多层趋势条件叠加带来的入场滞后，并检验候选股在不同市场状态下的横截面收益来源；属于后续候选假设，本轮未证明有效。不能仅靠继续增加均线和风险开关推定能达到目标。
'''.replace('PASS_COUNT',str(passed)).replace('TABLE_ROWS','\n'.join(table))
    (out/'研究结论.md').write_text(text,encoding='utf-8')
    print(json.dumps(dict(ok=True,audited_runs=len(audits),archives=len(archives),passing_validation=passed),ensure_ascii=False))

if __name__=='__main__':main()
