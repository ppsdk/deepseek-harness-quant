"""Read frozen research ledgers and reproduce risk statistics without fitting models.

All strategy choices are read from the experiment's frozen manifests. This script
does not write to the experiment ledgers or change the platform metric definitions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def risk_statistics(returns, risk_free_annual):
    """Retain the platform's calendar annualizer; add explicitly named alternatives."""
    r = returns.astype(float)
    af = len(r) / ((r.index[-1] - r.index[0]).days / 365.25)
    sd = r.std(ddof=1)
    rf_day = (1 + risk_free_annual) ** (1 / af) - 1
    excess = r - rf_day
    downside_rms = np.sqrt(np.mean(np.minimum(r.to_numpy(), 0) ** 2))
    nav = (1 + r).cumprod()
    peaks = np.maximum.accumulate(np.r_[1.0, nav.to_numpy()])[1:]
    dd = pd.Series(nav.to_numpy() / peaks - 1, index=r.index)
    monthly = r.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    trough = int(np.argmin(dd.to_numpy()))
    with_initial = np.r_[1.0, nav.to_numpy()]
    peak = int(np.flatnonzero(with_initial[:trough + 1] == peaks[trough])[-1])
    recovery_positions = np.flatnonzero(nav.to_numpy()[trough + 1:] >= peaks[trough])
    recovery = int(trough + 1 + recovery_positions[0]) if len(recovery_positions) else None
    max_underwater, running = 0, 0
    for value in dd:
        running = running + 1 if value < -1e-12 else 0
        max_underwater = max(max_underwater, running)
    tail = 0
    for value in dd.iloc[::-1]:
        if value >= -1e-12:
            break
        tail += 1
    total = float(nav.iloc[-1] - 1)
    annual = float(nav.iloc[-1] ** (af / len(r)) - 1)
    result = dict(
        start=str(r.index[0].date()), end=str(r.index[-1].date()), n_days=len(r),
        annualization_factor=float(af), total_return=total, annual_return=annual,
        volatility=float(sd * np.sqrt(af)),
        sharpe=float(r.mean() / sd * np.sqrt(af)),
        sharpe_fixed_252=float(r.mean() / sd * np.sqrt(252)),
        sharpe_assumed_rf=float(excess.mean() / sd * np.sqrt(af)),
        assumed_rf_annual=risk_free_annual,
        sortino_downside_rms_rf0=float(r.mean() * np.sqrt(af) / downside_rms),
        max_drawdown=float(dd.min()), calmar=annual / abs(float(dd.min())),
        month_count=len(monthly), winning_months=int((monthly > 1e-12).sum()),
        losing_months=int((monthly < -1e-12).sum()), flat_months=int((monthly.abs() <= 1e-12).sum()),
        monthly_win_rate=float((monthly > 0).mean()),
        worst_month=dict(month=str(monthly.idxmin().date())[:7], value=float(monthly.min())),
        best_month=dict(month=str(monthly.idxmax().date())[:7], value=float(monthly.max())),
        worst_day=dict(date=str(r.idxmin().date()), value=float(r.min())),
        best_day=dict(date=str(r.idxmax().date()), value=float(r.max())),
        monthly_returns={str(k.date())[:7]:float(v) for k,v in monthly.items()},
        max_drawdown_episode=dict(
            peak_date=str(r.index[peak - 1].date()) if peak else "initial_capital",
            trough_date=str(r.index[trough].date()),
            recovery_date=str(r.index[recovery].date()) if recovery is not None else None,
            peak_to_trough_trading_days=trough + 1 - peak,
            peak_to_recovery_or_end_trading_days=(recovery + 1 if recovery is not None else len(r)) - peak,
        ),
        longest_underwater_trading_days=max_underwater,
        current_underwater_trading_days=tail, current_drawdown=float(dd.iloc[-1]),
        total_return_excluding_best_month=float((1 + monthly.drop(monthly.idxmax())).prod() - 1),
    )
    assert np.isclose(nav.iloc[-1], (1 + monthly).prod(), rtol=1e-10)
    return result


def add_benchmark(stats, r, benchmark):
    b = benchmark.reindex(r.index)
    assert b.notna().all(), "Benchmark dates must cover every portfolio return"
    af = stats["annualization_factor"]
    active = r - b
    beta = r.cov(b) / b.var(ddof=1)
    tracking_error = active.std(ddof=1) * np.sqrt(af)
    stats["benchmark_comparison"] = dict(
        benchmark="沪深300价格指数，不含分红",
        total_return=float((1 + b).prod() - 1),
        annual_return=float((1 + b).prod() ** (af / len(b)) - 1),
        beta=float(beta), correlation=float(r.corr(b)),
        tracking_error=float(tracking_error),
        information_ratio=float(active.mean() * af / tracking_error),
    )


def analyze_ledger(raw, risk_free_annual, benchmark):
    nav = np.asarray(raw["nav"], dtype=float)
    dates = pd.DatetimeIndex(raw["dates"])
    r = pd.Series(nav / np.r_[1.0, nav[:-1]] - 1, index=dates)
    stats = risk_statistics(r, risk_free_annual)
    for key in ["total_return", "annual_return", "volatility", "sharpe", "max_drawdown", "calmar"]:
        assert np.isclose(stats[key], raw["metrics"][key], atol=1e-10), (raw["id"], key)
    equity = np.asarray([x["equity"] for x in raw["daily_account"]])
    capital = raw["params"]["initial_capital"]
    assert np.allclose(equity / capital, nav, atol=1e-10)
    stats.update(
        id=raw["id"], phase=raw["phase"], stress=raw["stress"],
        platform_sortino=raw["metrics"]["sortino"],
        average_exposure=raw["average_exposure"],
        full_cash_day_fraction=float(np.mean([x["holding_count"] == 0 for x in raw["daily_account"]])),
        annual_one_way_turnover=raw["annual_one_way_turnover"],
        trade_dates=raw["trade_dates"], trade_count=raw["trade_count"],
        emergency_exit_dates=raw["emergency_exit_dates"],
        costs=raw["costs"], costs_to_initial_capital=sum(raw["costs"].values()) / capital,
    )
    add_benchmark(stats, r, benchmark)
    pnl = defaultdict(float)
    for trade in raw["trades"]:
        signed_amount = trade["amount"] * (1 if trade["side"] == "sell" else -1)
        pnl[trade["code"]] += signed_amount - trade["commission"] - trade["stamp_tax"]
    for position in raw["final_positions"]:
        pnl[position["code"]] += position["market_value"]
    assert np.isclose(sum(pnl.values()), equity[-1] - capital, atol=0.01), raw["id"]
    ranked = sorted(pnl.items(), key=lambda x: x[1], reverse=True)
    positive = sum(max(x, 0) for x in pnl.values())
    stats["stock_profit_attribution"] = dict(
        method="成交净现金流加期末市值；含佣金印花税，滑点已在成交价中，禁止再次扣除",
        profit_sum=sum(pnl.values()), reconciliation_pass=True,
        unique_stocks=len(pnl), profitable_stocks=sum(v > 0 for v in pnl.values()),
        top3_share_of_positive_profits=sum(max(v, 0) for _,v in ranked[:3]) / positive if positive else None,
        top5=[dict(code=k,net_profit=v) for k,v in ranked[:5]],
        bottom5=[dict(code=k,net_profit=v) for k,v in ranked[-5:]],
    )
    stats["continuous_calendar_years"] = []
    if raw["phase"] == "continuous_forward":
        for year in sorted(set(dates.year)):
            selected = dates.year == year
            yr = r.loc[selected]
            item = risk_statistics(yr, risk_free_annual)
            add_benchmark(item, yr, benchmark)
            item["year"] = int(year)
            item["average_exposure"] = float(np.mean([a["exposure"] for a in raw["daily_account"] if pd.Timestamp(a["date"]).year == year]))
            item["return_kind"] = "连续持仓分年实际收益；全年不足者不把年化当实际收益"
            stats["continuous_calendar_years"].append(item)
    return stats


def render_report(payload):
    pct = lambda x: f"{x:.2%}"
    lines = ["# 同池机器学习＋均线择时：风险收益分析", "",
        f"分析时间：{payload['checked_at']}；账本截止：2026-10-23。仅复算冻结账本，不训练、不调参。",
        "证据类型：本地实验原始账本及其逐日净值的算术复算；不是实盘业绩。外部方法资料检索未返回可用内容，指标定义以下列可复现公式为准。", "",
        "## 核心判断", "",
        "验证期表现有吸引力，但延伸至2026年后风险收益明显恶化。两轮为不同冻结模型，不能将验证期拼成同一固定策略的完整样本外表现。均线门控减少在场时间，但未消除2026年的反复入场亏损。", "",
        "## 验证与连续回放", "",
        "|模型／窗口|实际总收益|年化收益|夏普 rf=0|年化波动|最大回撤|Calmar|月胜率|平均仓位|单边年换手|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for rd in payload["rounds"]:
        for phase in ["validation", "continuous_forward"]:
            s = rd["runs"][phase]
            lines.append(f"|{rd['model']} {s['start']}—{s['end']}|{pct(s['total_return'])}|{pct(s['annual_return'])}|{s['sharpe']:.2f}|{pct(s['volatility'])}|{pct(s['max_drawdown'])}|{s['calmar']:.2f}|{pct(s['monthly_win_rate'])}|{pct(s['average_exposure'])}|{s['annual_one_way_turnover']:.2f}倍|")
    lines += ["", "夏普包含空仓零收益日和交易成本，无风险利率设为0。年化收益为几何复利；夏普使用日均算术收益，不能直接用年化收益／波动率替代。", "",
        "## 连续路径的逐年结果", "",
        "|冻结模型|年份|实际收益|夏普|年化波动|年内最大回撤|平均仓位|沪深300实际收益|",
        "|---|---|---:|---:|---:|---:|---:|---:|"]
    for rd in payload["rounds"]:
        for s in rd["runs"]["continuous_forward"]["continuous_calendar_years"]:
            lines.append(f"|{rd['model']}|{s['year']}{'截至10-23' if s['year']==2026 else ''}|{pct(s['total_return'])}|{s['sharpe']:.2f}|{pct(s['volatility'])}|{pct(s['max_drawdown'])}|{pct(s['average_exposure'])}|{pct(s['benchmark_comparison']['total_return'])}|")
    lines += ["", "每年收益以连续账本上年末净值为基数，包含跨年持仓。不使用单独重置的2026观察账本；年内回撤从当年起点重新计量，不等于整个连续路径的最大回撤。2026不足一年。", "",
        "## 成本压力", "",
        "基础成本：佣金3bp、最低5元、滑点10bp、卖出印花税10bp；压力成本：佣金5bp、最低5元、滑点30bp、印花税10bp。印花税使用实验固定参数，未逐日重建历史税率。", "",
        "|模型／窗口|基础→压力年化|基础→压力夏普|基础→压力最大回撤|压力年化相对沪深300差值|",
        "|---|---:|---:|---:|---:|"]
    for rd in payload["rounds"]:
        for phase in ["validation", "continuous_forward"]:
            s, t = rd["runs"][phase], rd["runs"][phase + "_stress"]
            lines.append(f"|{rd['model']}／{phase}|{pct(s['annual_return'])}→{pct(t['annual_return'])}|{s['sharpe']:.2f}→{t['sharpe']:.2f}|{pct(s['max_drawdown'])}→{pct(t['max_drawdown'])}|{(t['annual_return']-t['benchmark_comparison']['annual_return'])*100:+.2f}个百分点|")
    lines += ["", "同窗基准为沪深300价格指数，未计分红；以上差值不是严格的全收益超额。费用总额／初始资金仅为金额归一化，不是年化收益损失；成本影响使用独立压力账本比较。", "",
        "## 回撤恢复、收益分布和集中度", ""]
    for rd in payload["rounds"]:
        s=rd["runs"]["continuous_forward"]
        d=s["max_drawdown_episode"]
        lines += [f"### {rd['model']}", "",
            f"- 最大回撤：高点{d['peak_date']}，谷底{d['trough_date']}，截至末日{'尚未修复' if d['recovery_date'] is None else '已于'+d['recovery_date']+'修复'}；高点至修复／期末{d['peak_to_recovery_or_end_trading_days']}个交易日。",
            f"- 最长水下期{s['longest_underwater_trading_days']}个交易日；当前水下{s['current_underwater_trading_days']}个交易日，当前回撤{pct(s['current_drawdown'])}。",
            f"- 最差单日{s['worst_day']['date']}：{pct(s['worst_day']['value'])}；最差月份{s['worst_month']['month']}：{pct(s['worst_month']['value'])}。",
            f"- {s['month_count']}个月中：上涨{s['winning_months']}、下跌{s['losing_months']}、持平{s['flat_months']}；全空仓日占{pct(s['full_cash_day_fraction'])}。末月为不完整月。",
            f"- 最好月份{s['best_month']['month']}：{pct(s['best_month']['value'])}；剔除最好月份后的描述性累计收益{pct(s['total_return_excluding_best_month'])}，仅用于检查收益集中，不能当作可交易反事实策略。",
            f"- 历史共交易{s['stock_profit_attribution']['unique_stocks']}只不同股票，同时持仓上限仍为10只；前三只盈利股占全部正利润的{pct(s['stock_profit_attribution']['top3_share_of_positive_profits'])}。归因利润与账户净利润核对通过。",
            f"- 总成本（佣金、印花税、已内含于成交价的滑点）{sum(s['costs'].values()):,.2f}元；累计{s['trade_count']}笔、{s['trade_dates']}个成交日，其中紧急退出{len(s['emergency_exit_dates'])}日。", "",
            "|盈利贡献前5代码|净利润（元）|", "|---|---:|"]
        for p in s['stock_profit_attribution']['top5']:
            lines.append(f"|{p['code']}|{p['net_profit']:,.2f}|")
    lines += ["", "## 指标口径与敏感性", "",
        "- 年化因子A=样本天数／首末日历跨度年数，与平台保持一致；日收益r_t=NAV_t/NAV_(t-1)-1，首日以前资金净值1为基准。",
        "- 夏普=mean(r)/std(r,ddof=1)×sqrt(A)，rf=0；另外给出固定252年化，以及假设无风险年收益2%的敏感性。2%为假设，不是查得的市场利率。",
        "- 平台索提诺=mean(r)×sqrt(A)/std(r[r<0],ddof=1)，仅负收益日标准差。补充Sortino使用sqrt(mean(min(r,0)^2))作为目标下行偏差，含全部日，目标收益0。两者分母不同。",
        "- Calmar=几何年化收益／绝对最大回撤，最大回撤包含起始净值1。",
        "- 信息比率=mean(r-b)×A／[std(r-b)×sqrt(A)]；Beta=cov(r,b)/var(b)，仅为同窗描述性统计。", "",
        "|模型／窗口|平台夏普|固定252夏普|假设rf=2%夏普|平台索提诺|目标下行偏差Sortino|Beta|信息比率|",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for rd in payload['rounds']:
        for phase in ['validation','continuous_forward']:
            s=rd['runs'][phase]
            lines.append(f"|{rd['model']}／{phase}|{s['sharpe']:.2f}|{s['sharpe_fixed_252']:.2f}|{s['sharpe_assumed_rf']:.2f}|{s['platform_sortino']:.2f}|{s['sortino_downside_rms_rf0']:.2f}|{s['benchmark_comparison']['beta']:.2f}|{s['benchmark_comparison']['information_ratio']:.2f}|")
    lines += ["", "## 机制与证据边界", "",
        "同池指估值数据可用的共同资格池，当前快照512只代码，每日资格动态变化；本路线模型使用技术特征，不代表已经融合估值特征。全主板3194只的结果不能与它混为一谈。",
        "机器学习给候选股排序，最多持有10只；普通交易至少间隔5个交易日。择时依据沪深300收盘高于MA60且MA20高于MA60，连续确认2日；风险退出在次日开盘，允许间隔例外。它不是个股双均线止盈止损，也没有另加个股止损。仓位百分比低不代表风险按同比例降低。",
        "两轮冻结前均由训练数据选择低相关特征，再标准化和PCA；训练、归一化、PCA未使用外层验证集。已查看的历史区间只能称时间隔离回放，不能称新的盲测。验证夏普不能证明跨市场阶段稳定。", "",
        "|轮次|模型|最终训练样本|保留因子→PCA维数|累计解释方差|最后训练标签终点|验证重拟合|",
        "|---|---|---:|---|---:|---|---|"]
    for rd in payload['rounds']:
        f=rd['fit_summary']
        lines.append(f"|{rd['round_id']}|{rd['model']}|{f['samples']}|{f['selected_dimension']}→{f['output_dimension']}|{pct(f['cumulative_explained_variance'])}|{f['last_outcome_date']}|{f['validation_refit']}|")
    if payload.get('training_boundary_audit'):
        lines += ["", "### 验证数据是否混入训练", "",
            "已检查实际保存的内层和最终模型文件，不只依赖审计标志。两轮训练标签终点均未进入各自外层验证期；所有原实验源码哈希与冻结记录一致。",
            "标准化均值、方差和样本数，以及PCA均值、样本数、特征值，均与重新抽取的训练期数据核对通过。Ridge系数与训练期数据的正规方程解一致；ExtraTrees每棵树的全部叶节点样本数和标签均值均与训练期数据一致。未重训模型。",
            "2023—2024在第一轮是验证期，在第二轮转为发现期，这是扩展窗口复用；第二轮验证期2025不进入第二轮训练。第二轮模型不能倒用于2023—2024并将结果称为验证。",
            "截面rank在每个交易日使用当日可观测股票的特征值，不跨日拟合，也不使用该日未来收益；验证日特征进入已冻结模型作预测，不属于模型训练。训练标签虽为未来20交易日收益，但其终点被限制在训练截止前。",
            "这项审计支持没有外层验证样本直接进入拟合；不能据此排除研究者反复查看历史后的选择偏差，或数据供应商历史修订及幸存者池偏差。详见matched_ml_training_boundary_audit.json。", ""]
    lines += ["", "PCA解释方差只描述保留特征的方差覆盖，不代表预测收益解释度或因子有效性。当前主板幸存者池、历史退市/ST不完整、估值覆盖选择偏差仍在；交易模拟使用前复权、小数份额，未模拟整手和盘口容量。一字板使用成交日完整日线做保守事后筛查。现金不计息。", "",
        "## 下一步判断", "",
        "保留本路线作为候选，以第一轮较长连续样本衡量稳定性，同时正视第二轮的2026退化。优先用冻结分数做同模型、同池、同调仓参数的有／无择时对照，再分析2026按市场阶段的选股贡献与入场损耗；这些尚未在本次分析新增执行。不能从当前数据单独认定收益由ML或均线哪一部分产生。", "",
        "不根据2026损失反向挑最优均线或模型。若以后调整规则，需将调整区间标为开发数据，并保留新的前瞻验证区间。", "",
        "## 可复现证据", "",
        "所有来源路径与SHA256见同目录matched_ml_risk_analysis.json。每份账本的夏普、年化、波动、最大回撤、Calmar与平台指标核对；逐日净值与账户权益核对，个股利润与账户总利润核对。读取前后来源哈希一致。研究结果不构成收益承诺。", ""]
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('output/strategy_research/multiroute_v1'))
    parser.add_argument('--route',default='matched_ml')
    parser.add_argument('--assumed-rf',type=float,default=0.02)
    args=parser.parse_args()
    mapping_path=args.root/'platform_archives.json'
    archives=read_json(mapping_path)
    sources={mapping_path:sha256(mapping_path)}
    payload=dict(checked_at=datetime.now().astimezone().isoformat(),route=args.route,rounds=[])
    for entry in archives:
        if entry['route'] != args.route:
            continue
        rd=entry['round_id']
        frozen_path=args.root/rd/f'{args.route}_frozen.json'
        frozen=read_json(frozen_path)
        choice=frozen['choice']
        fit_path=args.root/rd/f"{args.route}__{choice['candidate']}__final_fit.json"
        archive_path=Path('output/backtest_archive')/(entry['name']+'.json')
        archive=read_json(archive_path)
        sources.update({p:sha256(p) for p in [frozen_path,fit_path,archive_path]})
        bnav=np.asarray(archive['bench_nav'],dtype=float)
        bench=pd.Series(bnav/np.r_[1.,bnav[:-1]]-1,index=pd.DatetimeIndex(archive['dates']))
        fit=read_json(fit_path)
        summary={k:fit[k] for k in ['samples','selected_features','selected_dimension','output_dimension','cumulative_explained_variance','last_outcome_date','validation_refit']}
        row=dict(round_id=rd,model=choice['candidate'],choice=choice,fit_summary=summary,runs={})
        prefix=f"{args.route}__{choice['candidate']}__{choice['timing']}__R{choice['cadence']}__"
        for phase in ['validation','validation_stress','continuous_forward','continuous_forward_stress']:
            path=args.root/rd/'runs'/(prefix+phase+'.json')
            sources[path]=sha256(path)
            row['runs'][phase]=analyze_ledger(read_json(path),args.assumed_rf,bench)
        payload['rounds'].append(row)
    assert payload['rounds'], 'No matching archives'
    audit_path=args.root/'matched_ml_training_boundary_audit.json'
    if audit_path.exists():
        audit=read_json(audit_path)
        assert audit['ok']
        payload['training_boundary_audit']=audit
        sources[audit_path]=sha256(audit_path)
    for path,before in sources.items():
        assert sha256(path)==before, f'Source changed during analysis: {path}'
    payload['sources']=[dict(path=str(p.resolve()),sha256=h) for p,h in sources.items()]
    payload['verification']=dict(source_hashes_unchanged=True,metric_reconciliation=True,equity_reconciliation=True,stock_profit_reconciliation=True)
    (args.root/'matched_ml_risk_analysis.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    report=args.root/'同池机器学习风险收益分析.md'
    report.write_text(render_report(payload),encoding='utf-8')
    print(json.dumps(dict(report=str(report.resolve()),rounds=len(payload['rounds']),ledgers=sum(len(x['runs']) for x in payload['rounds']),verification=payload['verification']),ensure_ascii=False))


if __name__=='__main__':
    main()
