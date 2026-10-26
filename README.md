# DeepSeek HARNESS Quant

面向 A 股的量化研究工作区：获取数据、研究因子、参数化回测、检查交易成本并归档实验。

研究主入口：[量化研究工作台](http://127.0.0.1:8787/research)（首页同源）。可在页面添加因子组合策略、配置方向与权重、按历史截止日选股、查看候选股蜡烛图，再进入[成本回测](http://127.0.0.1:8787/backtest)。回测可设置初始资金、研究区间、持仓数、调仓间隔、双边佣金、最低佣金、卖出印花税和单边滑点。指标周期可以直接在网页调整，回测支持总仓位/单股上限、等额或逆波动分配、止损止盈和移动止损；在[仓位管理](http://127.0.0.1:8787/positions)查看实验期末成本、盈亏并计算名单预算。AI 用于协助编写策略和分析结果；研究直接在本地缓存上运行，无需模型 API。

[因子管理](http://127.0.0.1:8787/factors)支持条件组合、公式检查、真实数据预览与专业因子检验；[候选股信号](http://127.0.0.1:8787/signals)支持先筛名单、再等待入场/退出条件并带入回测。股票更新后会检查并尝试补齐缺日和缺字段，残留问题在数据页明确显示。

原有市场门户保留在 `/overview`。缺少日报或择时产物时显示数据缺口，不阻断研究；扩展因子包为可选模块。

使用步骤和当前成交模型边界见 [量化研究与回测](docs/量化研究与回测.md)。

### 近期新增

- **双层动量策略**（[策略管理](http://127.0.0.1:8787/strategies)页内运行）：每隔 N 个交易日按横截面因子（默认 20 日涨幅）选出前 topN 只作为候选名单；名单内股票每天再看自身时间序列动量，只有“在名单内且动量 > 阈值”才持有，每只 1/topN，其余留现金。收盘出信号、次日开盘成交，停牌、一字板、涨跌停不成交，扣佣金、印花税和滑点。
  - 每次同时跑四组对照：A 股票池等权、B 纯横截面选股、C 纯个股择时、D 双层，报告年化、夏普、回撤、平均仓位、换手和成本；另给出“把 B 缩放到 D 的平均仓位”的对照，排除“只是多留现金”的解释。
  - 增量检验：名单内 E[R | 择时成立] − E[R | 择时不成立]，按训练期/样本外分开，用不重叠样本计算 t 值；同时给出横截面因子 IC。
  - 参数稳健性：遍历动量窗口、调仓间隔和阈值，只按训练期夏普挑参数，再看它在样本外的排名和邻近参数表现。
  - 股票池支持沪深300/中证500/中证1000（当前成分股，存在幸存者偏差）或本机已下载股票；参数、因子候选、对照组说明均在 `config/dual_momentum.yaml.example`。
- **结论分级与筛选**：回测结果按年化、夏普、回撤分为 优秀 / 良好 / 观察 / 勉强 / 无效 五级，回测管理、筛选结果和双层动量历史都可按评级筛选。
- **个股资料**：研究工作台的个股页显示行情快照、公司概况、财务摘要和本机统计，并可让 AI 生成个股研究摘要（配置见 `config/stock_profile.yaml.example`）。
- **每日大盘分析**：[大盘分析](http://127.0.0.1:8787/market)页按趋势、宽度、情绪、量能、恐慌五个维度打分，一键发给 HARNESS 生成 AI 日报（配置见 `config/market_analysis.yaml.example`）。
- **K 线指标叠加**：同一指标可用不同参数反复叠加（如 MA5/MA10/MA40），支持日 K、周 K、月 K 切换。
- **自动化任务**：在 [AI 辅助](http://127.0.0.1:8787/control)页设定定时任务，按顺序执行行情刷新、大盘日报、筛选入池、信号检查、回测和 AI 复核。
- **HARNESS 数据读取**：AI 可通过 `/quant/data?type=` 读取因子、实验、股票池、信号、择时、大盘、双层动量等本机研究结果；编写因子与回测的规则见 `.dsh/skills/agent-factor-backtest.md`。
- 因子库新增动量（1/3/6/12 月）、均线多头排列、量价背离、应计比率、现金流质量、ROE 趋势、毛利率变化等因子。

AI 不预测个股。这是硬约束，不是选项。

```
驱动层  语言模型        控制 · 挖因子 · 审计 · 牛散蒸馏
执行层  写死引擎        评分 · 回测 · 风控 · 扫描（确定性 Python，可复现）
事实层  数据系统        PIT · T+1 · 覆盖率分年核查（可证伪）
```

## 开始

新用户直接走 [一键部署](docs/一键部署.md)。接入 AI API 后，数据源、配置、验证全交给 AI 向导，不必读本页其余内容。

## 接入 AI API（默认 DataEyes）

接入 API 后，语言模型接管驱动层。当前默认使用 DataEyes 中转站的 `deepseek-v4.1-flash-1017`，也可切换到 DeepSeek 官方或其他兼容 OpenAI 的服务。

**前提**：完整包（zip）已内置 HARNESS 运行时；单文件（exe）不含 HARNESS，AI 控制台不可用。系统需 Node.js 24+（https://nodejs.org），无则 HARNESS 自动跳过、量化系统照常。

**最快方式**：双击 `接入API.cmd`，粘贴 Key，自动写入并验证。

```bash
# 1. 获取 Key（当前默认 DataEyes）
#    https://platform.dataeyes.ai

# 2. 复制凭据模板，填入 Key（或直接用接入API.cmd）
copy harness\home\.credentials.yaml.example harness\home\.credentials.yaml
#    编辑 .credentials.yaml：
#    version: 1
#    refs:
#      DATAEYES_API_KEY: <your-dataeyes-key>

# 3. 启动（打印「启动 DeepSeek HARNESS」即集成成功）
python launcher.py
#    http://127.0.0.1:8787/control
```

接入后，直接对话：

- **控制系统**：发「1」触发自主推进（审计 / 修复 / 归档，系统自我进化）
- **挖因子**：说「研究散户情绪量化」——AI 拆假设 → 生成因子 → 九步验证 → 入池或证伪
- **审计**：让 AI 自查前视、覆盖率、共线性、过拟合
- **牛散**：7 位牛散人格对话选股，决策自动入远期池验证
- **魔改**：动态 Cordis 插件热更新，系统行为运行时改，不重编译

其余数据源（Tushare 等）接入后让 AI 指导完成。核心逻辑：先接 AI 的 API，AI 帮你接剩下的。

## 架构

| 模块 | 职责 |
|---|---|
| data/ | 数据获取 + 本地缓存 + Point-in-Time（cache.py 唯一读取接口） |
| factors/ | 因子引擎 + 机会扫描 + 远期池（五池 T+1/5/20/60 验证） |
| strategy/ | 决策链（L0 择时 → L2 Pitch 审批 → T+1 执行）+ 组合构建 |
| risk/ | 风控七道（数据审计 / 因子健康 / FRC 排雷 / Beneish / 竞价 / 单因子 / L0 门控） |
| backtest/ | 回测引擎（T+1 开盘 / 一字板过滤 / 成本模型 / 结论分级） |
| etf/ | ETF 映射（策略暴露 → 可交易配置） |
| deck/ ui_v2/ | Web 决策台（9 页，前端零硬编码） |
| harness/ | DeepSeek HARNESS 运行时（AI 控制台 / 牛散桥 / 动态插件） |
| config/ | 策略注册表 / ETF 池 / 阈值，全部配置化 + .example 模板 |

## 因子

123+ 因子，全部 A 股本地实证（PIT / T+1 / 分年度）。九步入池，17+ 项证伪留档。

| 维度 | 代表 | 实证 |
|---|---|---|
| 换手率 | turn_mid_prox / turnover / turn_std20 | turn_mid_prox ICIR 0.87 |
| 低波动 | lowvol / std20 / downside_vol | 防守底仓主力 |
| 反转 | reversal20 / o2c 日内反转族 | limup_ex_5 ICIR 1.24 |
| 流动性 | amihud | 0.43，多空夏普 1.12 |
| 彩票/偏度 | max_ret20 / skew20 / rmax | max_ret20 0.64 |
| 振幅/动量 | amp20 / open_prem_20 | open_prem_20 0.96 |
| 基本面低频 | f_score / sue / accruals / asset_growth / bp | f_score 120日 0.49 |
| 短线涨跌停 | limit_up_* / consec_limit_down | 跌停排雷 0.97 |
| 机构行为 | lhb_jg_cnt_20 / shebao_chg | lhb 0.90 |
| 行业层 | ind_crowd_60 / ind_rs_20 | 拥挤 0.30 |
| Alpha101 | alpha015 / alpha050 / alpha006 / alpha003 / alpha044 | alpha015 0.76 |

来源：学术复现 · 开源策略库复刻 · 事件驱动实证 · 牛散蒸馏 · AI 自动挖掘 · 机构资金行为。

## Skill

12 个技能文件，封装方法论 + 资产 + 踩坑记录，AI 按需加载。

- 因子挖掘：factor-mining-workflow · alpha-gpt-factor-mining · alpha-gpt-researcher · backtest-acceptance
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
> **不要**下载页面底部的 "Source code (zip)" —— 那是源码包，harness/node_modules 被排除，没有 AI 控制台。
> 源码包也可使用 AI 控制台：安装 Node.js 24+ 后运行 `harness/install.cmd` 补装运行时。
> 判断：解压后 `harness\node_modules` 文件夹存在 = 完整包；不存在 = 需要补装运行时。

运行要求：Python 3.10+（源码）/ 无（exe）。HARNESS 控制台需 Node.js 24+（可选）。

## 数据

数据由用户自行获取。系统不分发数据。

使用 AKShare 获取 A 股历史日线：按 [AKShare 回测数据指南](docs/AKShare回测数据.md) 安装 `requirements-data.txt`，再运行 `python -m data.akshare_loader`；支持逐股失败隔离、断点续传和数据页动态进度。

- 行情来自第三方（Tushare 等）。仓库只含获取脚本 + 合成演示数据。
- 换手率 2019 年前缺失，2019 前换手类结论作废。
- 配置：`config/params.yaml.example`（Tushare token）。

## 许可

MIT。仅供研究学习。不构成投资建议。

## 文档

[资产盘点](docs/资产盘点.md) · [架构说明](docs/架构.md) · [一键部署](docs/一键部署.md) · [快速开始](docs/快速开始.md) · [HARNESS 接入](docs/HARNESS接入.md) · [数据说明](docs/数据说明.md) · [分钟数据接入](docs/分钟数据接入说明.md)

更新机制：`scripts/update.py`（manifest 驱动，用户配置保护，应用前自动备份）。

研究工作台现有158项内置指标（含33项技术指标、12项基本面指标、101条Alpha101和5项机器学习评分）。提供独立因子管理、策略管理、回测管理与机器学习页，可编写公式、检验分组表现、按日期训练模型和比较费用；K线支持缩放、尺寸调整与技术指标，股票名称/代码自动匹配。数据页可手动更新股票、目录、指数和arXiv论文。基本面数据可通过 AKShare 获取或导入带可用日期的 CSV；缺少数据的项目会明确标注。详见 [量化研究与回测](docs/量化研究与回测.md)。
