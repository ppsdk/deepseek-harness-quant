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
| 研究工作台 | /research | 查 K 线、选股、配置策略、触发回测 |
| 因子管理 | /factors | 因子分组检验、编写公式因子、查看 IC |
| 策略管理 | /strategies | 93 套内置策略 + 双层动量，按分类筛选 |
| 回测管理 | /backtest | 查看、对比、下载存档回测，五级评级筛选 |
| 机器学习 | /ml | 训练并回测 10 种 ML 模型 |
| 市场择时 | /timing | 看历史信号，有无择时对比回测 |
| 候选股信号 | /signals | 先筛名单，再等入场/退出条件 |
| 仓位管理 | /positions | 期末成本、盈亏、名单预算 |
| 大盘分析 | /market | 五维评分（趋势/宽度/情绪/量能/恐慌）+ AI 日报 |
| 数据管理 | /data | 手动更新行情、管理基本面数据、arXiv 论文 |
| AI 辅助 | /control | 自动化定时任务、与 HARNESS 对话 |

## 开始

新用户直接走 [一键部署](docs/一键部署.md)。接入 AI API 后，数据源、配置、验证全交给 AI 向导。

## 接入 AI API（默认 DataEyes）

当前默认使用 DataEyes 中转站的 `deepseek-v4.1-flash-1017`，也可切换到 DeepSeek 官方或其他兼容 OpenAI 的服务。

**前提**：完整包（zip）已内置 HARNESS 运行时；单文件（exe）不含 HARNESS，AI 控制台不可用。系统需 Node.js 24+（https://nodejs.org），无则 HARNESS 自动跳过、量化系统照常。

**最快方式**：双击 `接入API.cmd`，粘贴 Key，自动写入并验证。

```bash
# 1. 获取 Key（当前默认 DataEyes）
#    https://platform.dataeyes.ai

# 2. 复制凭据模板，填入 Key（或直接用接入API.cmd）
copy harness\home\.credentials.yaml.example harness\home\.credentials.yaml
#    version: 1
#    refs:
#      DATAEYES_API_KEY: <your-key>

# 3. 启动
python launcher.py
#    http://127.0.0.1:8787/control
```

接入后可以直接对话：

- **挖因子**：「研究散户情绪量化」——AI 拆假设 → 生成因子 → 验证 → 入池或证伪
- **审计**：让 AI 自查前视、覆盖率、共线性、过拟合
- **牛散**：7 位牛散人格对话选股，决策自动入远期池验证
- **自动化**：设定定时任务，按顺序执行行情刷新、大盘日报、筛选入池、信号检查、回测和 AI 复核

## 架构

| 模块 | 职责 |
|---|---|
| data/ | 数据获取 + 本地缓存 + Point-in-Time（cache.py 唯一读取接口） |
| factors/ | 因子引擎 + 机会扫描 + 远期池（T+1/5/20/60 验证） |
| strategy/ | 决策链（L0 择时 → L2 Pitch 审批 → T+1 执行）+ 组合构建 |
| risk/ | 风控七道（数据审计 / 因子健康 / FRC 排雷 / Beneish / 竞价 / 单因子 / L0 门控） |
| backtest/ | 回测引擎（T+1 开盘 / 一字板过滤 / 成本模型 / 五级结论分级） |
| etf/ | ETF 映射（策略暴露 → 可交易配置） |
| deck/ ui_v2/ | Web 决策台（11 页，前端零硬编码） |
| harness/ | DeepSeek HARNESS 运行时（AI 控制台 / 牛散桥 / 动态插件） |
| config/ | 策略注册表 / 因子库 / 阈值，全部配置化 + .example 模板 |

## 因子库

**75+ 技术因子**（含 11 个日内结构 Alpha 因子）、**12 个基本面因子**、**101 条 Alpha101**、**35 个形态信号**、**10 个 ML 评分**。

| 类别 | 代表因子 | 实证（A股，stocks=500）|
|---|---|---|
| 日内结构 | intraday_range_20 · o2c_20 · overnight_gap_20 | IC 0.10–0.12，胜率 71–77% |
| 动量/涨幅 | momentum_3m · roc_60 · rps_120 | A股反转效应显著 |
| 均线/趋势 | ma_alignment_4 · ema_trend_gap · adx_14 | 趋势确认效果稳定 |
| 量价结构 | turnover_acceleration_5 · obv_momentum_20 · cmf_20 | 放量反转信号 IC 0.05 |
| 波动率 | volatility_20 · downside_volatility_20 · boll_width | 低波动防守效果好 |
| 基本面质量 | f_score_simple · cashflow_quality · roe_trend | 需财务数据覆盖 |
| Alpha101 | alpha101_012 · alpha101_033 · alpha101_053 | 52 条可用 |

来源：学术复现 · 开源策略库复刻 · 事件驱动实证 · 牛散蒸馏 · AI 自动挖掘 · IC 实证入池。

## 策略库

**93 套内置选股策略**，按六个分类组织：

| 分类 | 数量 | 代表策略 |
|---|---|---|
| 技术指标 | 22 | ADX 趋势确认、布林带收窄突破、MFI 超卖 |
| 量价与形态 | 13 | 道氏结构、威科夫量价、唐奇安通道突破 |
| 动量 | 7 | 双层动量（横截面+时间序列）、3/6/12 月横截面动量 |
| 基本面 | 12 | 质量+成长双因子、ROE 持续改善、收入利润双增长 |
| 因子与Alpha | 21 | 低日内振幅、隔夜跳空动量、Alpha101 示例 |
| 机器学习 | 18 | 单模型（10 种）+ 多模型共识 + ML+技术/基本面组合 |

### 双层动量

在策略管理页内运行。每隔 N 个交易日按横截面因子选出前 topN 只候选；候选内股票再按自身时间序列动量过滤，只有「在名单内且动量 > 阈值」才持有，其余留现金。

四组对照：A 基准等权、B 纯横截面、C 纯择时、D 双层，额外给出仓位缩放后的 B 对照排除「多留现金」解释。增量检验（t 值、IC）+ 参数网格稳健性 + AI 复核。

## 回测

T+1 开盘成交，停牌/一字板不成交，扣双边佣金（默认 2.6 bps + 最低 5 元）、卖出印花税（5 bps）和单边滑点（10 bps）。

**股票池**（新增）：可选全部本机缓存、沪深 300、中证 500 或中证 1000 当前成分股。选指数成分股时只回测本机已缓存的成分股。

**结论五级分级**：

| 评级 | 年化 | 夏普 | 最大回撤 |
|---|---|---|---|
| 优秀 | ≥15% | ≥1.2 | >-25% |
| 良好 | ≥8% | ≥0.7 | >-35% |
| 观察 | >0% | >0 | >-50% |
| 勉强 | >-5% | — | — |
| 无效 | ≤-5% | — | — |

支持等额或逆波动分配、总仓位/单股上限、止损止盈和移动止损。参数化指标（均线周期等）可直接在网页调整。

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
npm ci --prefix harness             # 源码安装 HARNESS（固定 0.2.1-rc.2）
python data/demo/build_demo_db.py    # 生成演示数据
python launcher.py                   # deck:8787 + HARNESS:3080

# 单文件
QuantDeck.exe                        # 双击即用，自动开浏览器

# 完整包
DSHQuant-v1.0.9-Release.zip          # 解压即用，含 HARNESS 运行时
```

> **下载注意**：从 Release 页 **Assets 区**下载 `DSHQuant-v1.0.9-Release.zip`（完整包，含 HARNESS 运行时）。
> **不要**下载页面底部的 "Source code (zip)"——那是源码包，harness/node_modules 被排除，没有 AI 控制台。
> 源码包也可使用 AI 控制台：安装 Node.js 24+ 后运行 `harness/install.cmd` 补装运行时。
> 判断：解压后 `harness\node_modules` 文件夹存在 = 完整包；不存在 = 需要补装运行时。

运行要求：Python 3.10+（源码）/ 无（exe）。HARNESS 控制台需 Node.js 24+（可选）。

## 数据

数据由用户自行获取。系统不分发数据。

使用 AKShare 获取 A 股历史日线：按 [AKShare 回测数据指南](docs/AKShare回测数据.md) 安装 `requirements-data.txt`，再运行 `python -m data.akshare_loader`；支持逐股失败隔离、断点续传和数据页动态进度。

- 行情来自第三方（AKShare / Tushare 等）。仓库只含获取脚本 + 合成演示数据。
- 换手率 2019 年前缺失，2019 前换手类结论作废。
- 财务数据可通过 AKShare 获取或导入带可用日期的 CSV（`config/akshare.yaml.example`）。

## 许可

MIT。仅供研究学习。不构成投资建议。

## 文档

[资产盘点](docs/资产盘点.md) · [架构说明](docs/架构.md) · [一键部署](docs/一键部署.md) · [快速开始](docs/快速开始.md) · [HARNESS 接入](docs/HARNESS接入.md) · [数据说明](docs/数据说明.md) · [分钟数据接入](docs/分钟数据接入说明.md) · [量化研究与回测](docs/量化研究与回测.md)

更新机制：`scripts/update.py`（manifest 驱动，用户配置保护，应用前自动备份）。
