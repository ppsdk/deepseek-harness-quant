# DeepSeek HARNESS Quant

面向 A 股的量化研究工作区：获取数据、研究因子、参数化回测、检查交易成本并归档实验。

```
驱动层  语言模型        控制 · 挖因子 · 审计 · 牛散蒸馏
执行层  写死引擎        评分 · 回测 · 风控 · 扫描（确定性 Python，可复现）
事实层  数据系统        PIT · T+1 · 覆盖率分年核查（可证伪）
```

AI 不预测个股。这是硬约束，不是选项。

## 功能页面

| 页面 | 地址 | 主要用途 |
|---|---|---|
| 研究工作台 | /research | K 线图表、个股行情、市场观察 |
| 因子管理 | /factors | 因子分组检验、编写公式因子、查看 IC |
| 策略管理 | /strategies | 99 套内置策略，按分类筛选，可直接选股或回测 |
| 运行回测 | /backtest | 配置参数、选股票池（含指数成分股）、运行回测 |
| 回测管理 | /backtests | 查看、对比、下载存档回测，五级评级筛选 |
| 机器学习 | /ml | 训练并回测 10 种 ML 模型 |
| 候选股信号 | /signals | 先筛名单，设置入场/退出条件 |
| 市场择时 | /timing | 查看历史信号，有无择时对比回测 |
| 仓位管理 | /positions | 期末成本、盈亏、名单预算 |
| 大盘分析 | /market | 五维评分（趋势/宽度/情绪/量能/恐慌）+ AI 日报 |
| 股票与数据 | /data | 更新行情、管理基本面数据、arXiv 论文 |
| AI 辅助 | /control | 定时自动化任务（市场数据/大盘分析/选股/信号/回测）|

## 开始

新用户直接走 [一键部署](docs/一键部署.md)。接入 AI API 后，数据源、配置、验证全交给 AI 向导。

## 接入 AI API（默认 DataEyes）

当前默认使用 DataEyes 中转站的 `deepseek-v4.1-flash-1017`，也可切换到 DeepSeek 官方或其他兼容 OpenAI 的服务。

**最快方式**：双击 `接入API.cmd`，粘贴 Key，自动写入并验证。

```bash
# 1. 获取 Key（当前默认 DataEyes）
#    https://platform.dataeyes.ai

# 2. 复制凭据模板，填入 Key（或直接用接入API.cmd）
copy harness\home\.credentials.yaml.example harness\home\.credentials.yaml

# 3. 启动
python launcher.py
#    http://127.0.0.1:8787/control
```

**HARNESS 控制台**需 Node.js 24+（https://nodejs.org）；完整包已内置运行时，单文件（exe）不含控制台。

接入后可以直接对话：

- **挖因子**：「研究散户情绪量化」—— AI 拆假设 → 生成因子 → 验证 → 入池或证伪
- **审计**：让 AI 自查前视、覆盖率、共线性、过拟合
- **牛散**：7 位牛散人格对话选股，决策自动入远期池验证
- **自动化**：设定定时任务，按顺序执行市场刷新、大盘日报、选股入池、信号检查、回测和 AI 复核

## 架构

| 模块 | 职责 |
|---|---|
| data/ | 数据获取 + 本地缓存 + Point-in-Time（cache.py 唯一读取接口） |
| factors/ | 因子引擎 + 机会扫描 + 远期池（T+1/5/20/60 验证） |
| strategy/ | 决策链（L0 择时 → L2 Pitch 审批 → T+1 执行）+ 组合构建 |
| risk/ | 风控七道（数据审计 / 因子健康 / FRC 排雷 / Beneish / 竞价 / 单因子 / L0 门控） |
| backtest/ | 回测引擎（T+1 开盘 / 一字板过滤 / 成本模型 / 五级结论分级 / 持仓日频过滤） |
| etf/ | ETF 映射（策略暴露 → 可交易配置） |
| deck/ ui_v2/ | Web 决策台（11 页，前端零硬编码） |
| harness/ | DeepSeek HARNESS 运行时（AI 控制台 / 牛散桥 / 动态插件） |
| config/ | 策略注册表 / 因子库 / 阈值，全部配置化 + .example 模板 |
| validation/ | 39 个 test_*.py 测试套件 + dsh_compat_smoke.py 集成测试 |

## 因子库

**83 个技术因子**、**12 个基本面因子**、**101 条 Alpha101**、**35 个形态信号**、**10 个 ML 评分**，共 241 个。

| 类别 | 代表因子 | IC 实证（A股，stocks=500）|
|---|---|---|
| 日内结构 | intraday_range_20 · o2c_20 · overnight_gap_20 | IC 0.10–0.12，胜率 75–77% |
| 波动率结构 | max_daily_return_20 · downside_realized_vol_20 | IC 0.08–0.11，胜率 70–79% |
| 量价结构 | price_pressure_20 · amihud_illiquidity_20 | IC 0.09–0.12，胜率 70–80% |
| 价格位置 | new_high_strength_250 · price_percentile_60 | IC 0.04–0.06，胜率 65–71% |
| 动量/涨幅 | momentum_3m · rps_120 · roc_60 | A 股反转效应显著 |
| 均线/趋势 | ma_alignment_4 · ema_trend_gap · adx_14 | 趋势确认效果稳定 |
| 换手率 | turnover_acceleration_5 · turnover_shrink_20 | IC 0.03–0.05 |
| 基本面质量 | f_score_simple · cashflow_quality · roe_trend | 需财务数据覆盖 |
| Alpha101 | alpha101_012 · alpha101_033 · alpha101_053 | 52 条可用 |

所有新因子经 IC 实证验证方向（stocks=500，2021—2026），发现 A 股四个强反转规律：
**追涨效应**（买方压力、最大涨幅）、**非流动性溢价**、**日内振幅反转**、**开收比反转**。

## 策略库

**99 套内置选股策略**，按六个分类组织：

| 分类 | 数量 | 代表策略 |
|---|---|---|
| 技术指标 | 22 | ADX 趋势确认、布林带收窄突破、MFI 超卖 |
| 量价与形态 | 13 | 道氏结构、威科夫量价、唐奇安通道突破 |
| 动量 | 9 | 横截面+时序双层动量（三种过滤器）、3/6/12 月横截面动量 |
| 基本面 | 11 | 质量+成长双因子、ROE 持续改善、收入利润双增长 |
| 因子与Alpha | 26 | 彩票反转、买方压力反转、流动性溢价、低日内振幅、Alpha101 示例 |
| 机器学习 | 18 | 单模型（10 种）+ 多模型共识 + ML+技术/基本面组合 |

### 持仓日频过滤（holding_filter）

策略配置里可以指定 `holding_filter` 字段，在换股间隔外每日检查持仓条件：不满足时次日开盘卖出，空位等到下一个换股日补。这让"两层逻辑"成为普通策略配置的一个参数，无需独立模块。示例：

```yaml
dual_cs_ts_ma_trend:
  factor_list:
    - name: rps_120
      sign: -1
  holding_filter:
    factor: ma_trend
    min: 0          # 均线趋势低于 0 则次日卖出
```

## 回测

T+1 开盘成交，停牌/一字板不成交，扣双边佣金（默认 2.6 bps + 最低 5 元）、卖出印花税（5 bps）和单边滑点（10 bps）。

**股票池**：全部本机缓存 / 沪深 300 / 中证 500 / 中证 1000 当前成分股（选指数时只回测本机已缓存的成分股）。

**结论五级分级**：优秀 / 良好 / 观察 / 勉强 / 无效，按年化、夏普、回撤划分。

支持等额或逆波动分配、总仓位/单股上限、止损止盈和移动止损。参数化指标周期可在回测页直接调整。

## 自动化任务

在 AI 辅助页（/control）设定定时任务，内置三个模板：

- **每日盘后**：更新市场数据 → 生成大盘分析
- **收盘后例行**：更新市场 → 大盘分析 → 选股存池 → 检查信号
- **每周复盘**：回测 → AI 复核（每周五）

调度器随平台启动，每分钟检查一次到期任务，共用研究计算锁避免冲突。

## Skill

13 个技能文件，封装方法论 + 资产 + 踩坑记录，AI 按需加载。

- 因子挖掘：factor-mining-workflow · alpha-gpt-factor-mining · alpha-gpt-researcher · backtest-acceptance
- 因子回测规则：agent-factor-backtest（T+1 口径、五级分级、质量门控）
- 牛散蒸馏：niu-san-distillation + 林园 / 陈小群 / 章盟主 / 赵老哥 / 炒股养家 / 冯柳
- 系统维护：github-maintainer

## 安装与运行

```bash
# 源码
pip install -r requirements.txt
npm ci --prefix harness             # 安装 HARNESS 运行时
python data/demo/build_demo_db.py    # 生成演示数据
python launcher.py                   # deck:8787 + HARNESS:3080

# 单文件
QuantDeck.exe                        # 双击即用，自动开浏览器

# 完整包
DSHQuant-v1.0.9-Release.zip          # 解压即用，含 HARNESS 运行时
```

> **下载注意**：从 Release 页 **Assets 区**下载完整包（含 `harness\node_modules`）；Source code (zip) 不含运行时，需要另外安装 Node.js 24+ 后运行 `harness/install.cmd`。

运行要求：Python 3.10+（源码）/ 无（exe）。HARNESS 控制台需 Node.js 24+（可选）。

## 数据

数据由用户自行获取。系统不分发数据。

使用 AKShare 获取 A 股历史日线：按 [AKShare 回测数据指南](docs/AKShare回测数据.md) 安装 `requirements-data.txt`，再运行 `python -m data.akshare_loader`；支持逐股失败隔离、断点续传和数据页动态进度。

- 行情来自第三方（AKShare / Tushare 等）。仓库只含获取脚本 + 合成演示数据。
- 换手率 2019 年前缺失，2019 前换手类结论作废。
- 财务数据可通过 AKShare 获取或导入带可用日期的 CSV（在数据页 `/data` 管理）。

## 许可

MIT。仅供研究学习。不构成投资建议。

## 文档

[资产盘点](docs/资产盘点.md) · [架构说明](docs/架构.md) · [一键部署](docs/一键部署.md) · [快速开始](docs/快速开始.md) · [HARNESS 接入](docs/HARNESS接入.md) · [数据说明](docs/数据说明.md) · [量化研究与回测](docs/量化研究与回测.md)

更新机制：`scripts/update.py`（manifest 驱动，用户配置保护，应用前自动备份）。
