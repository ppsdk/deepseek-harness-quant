---
name: agent-factor-backtest
description: Agent 编写因子、运行回测、优化策略的操作规则与质量门控
---

# Agent 因子研究与回测规则

> 本文件约束所有通过 AI agent（包括 DeepSeek HARNESS、Claude、本地脚本）
> 自动编写因子或运行回测的行为。违反任意一条硬规则视为无效产出。

---

## 一、因子编写规则（硬规则）

### 1.1 禁止前视偏差

- 因子计算只能使用 `asof` 日期**之前**已可获得的数据
- 财报数据必须通过 `_available_date()` 映射后使用，不得直接用报告期末
- 技术指标只能用截至信号日收盘的历史数据（`shift(1)` 保证 T+1 可用）
- 换手率（`turn`）**2019 年前数据缺失**，凡依赖换手率的结论一律限定在 2019-01-01 之后

### 1.2 表达式因子格式

新因子必须以 YAML 块写入 `config/factor_library.yaml`（或通过 `/api/studio/factors` 保存），格式：

```yaml
my_factor_key:
  label: 中文名称（≤20字）
  description: 单句说明计算含义和使用注意事项
  category: 动量因子 | 基本面质量 | 量价与换手 | 趋势与结构 | 常用技术指标
  kind: technical | expression | fundamental
  operation: <operation_name>   # kind=technical 时必填
  formula: "<alpha101 表达式>"  # kind=expression 时必填
  params:
    n: 20                        # 所有数值参数均用 params 传递，禁止硬编码
  requires:
    - close
    - fund:roe                   # 财报字段用 fund: 前缀
  default_sign: 1                # 1=高值优先，-1=低值优先
  chart: false
  dependencies: []               # expression 类型的依赖因子 key 列表
```

### 1.3 同系列指标参数化

- **禁止**为同一指标不同周期各建独立 key（如 `ma_5 / ma_10 / ma_20`）
- **改为**一个 key + `params.n` 参数，通过 `indicator_params` 在运行时覆盖
- 已有独立 key 的系列不得重复注册，直接引用现有 key 并通过 `params` 调整

### 1.4 operation 实现要求

在 `factors/research_indicators.py` 新增 operation 时：

```python
# 模板
if op == 'my_op':
    n = int(a.get('n', 20))           # 从 params 读取，给默认值
    # 计算逻辑，只用 p['close'] / p['high'] / p['low'] / p['volume']
    # 禁止访问 p 中不存在的 key（先检查 p.get()）
    return result  # DataFrame，形状与 panels['close'] 相同
```

必须在同文件末尾的 `raise ValueError('未知指标算法：' + op)` **之前**插入。

---

## 二、回测规则（硬规则）

### 2.1 执行口径

| 规则 | 要求 |
|---|---|
| 信号生成 | 前一交易日收盘后计算，T+1 开盘执行 |
| 一字板过滤 | `high == low` 的日期视为不可成交，跳过 |
| 停牌过滤 | 缺 OHLCV 数据的日期不成交 |
| 费用模型 | 佣金（bps，含最低门槛）+ 印花税（卖出）+ 滑点（开盘价基础上） |
| 复权方式 | 前复权（qfq），禁止使用不复权价格计算信号 |

### 2.2 参数下限

回测区间必须满足：

- 至少 **1 年**（252 个交易日）才能输出结论
- 有效交易日数 < 126 时，输出 `verdict='无效'`，不得升级为任何正向结论

### 2.3 结论分级标准（五档）

| 档位 | 年化收益 | 夏普 | 最大回撤 | 含义 |
|---|---|---|---|---|
| 优秀 | ≥ 15% | ≥ 1.2 | > -25% | 可重点研究，需人工复核 |
| 良好 | ≥ 8%  | ≥ 0.7 | > -35% | 有参考价值，需结合市场环境 |
| 观察 | > 0%  | > 0   | > -50% | 正收益但整体一般，留档观察 |
| 勉强 | > -5% | —     | —      | 微亏，参数或逻辑待优化 |
| 无效 | ≤ -5% | —     | —      | 该参数组不可用 |

**Agent 不得自行修改分级阈值**，如需调整须在本文件中提交变更。

### 2.4 禁止的回测行为

- 禁止使用未来信息（前视偏差），包括但不限于：用当天最高价触发买入、用期末涨幅筛选样本
- 禁止在回测结束后手动筛选"好看的"时间段，必须使用完整指定区间
- 禁止基于回测结果反向修改因子参数后不重新标注为新实验（参数挖掘必须留档）

---

## 三、Agent 工作流规则

### 3.1 先建数据通道，再建因子

```
1. 确认依赖数据已在缓存中（GET /api/studio/data-quality）
2. 定义因子（POST /api/studio/factors 或直接写入 YAML）
3. 在研究工作台验证因子可计算（GET /api/studio/config 确认 available=true）
4. 保存候选池（POST /api/studio/pools 或 GET /api/studio/pools/screen?save=1）
5. 运行回测（POST /api/studio/backtest）
6. 读取结论（GET /api/studio/experiments?id=...）
```

### 3.2 结果上报格式

Agent 完成一次因子研究后，必须输出以下结构化摘要：

```json
{
  "factor_key": "my_factor",
  "backtest_id": "research_my_factor_<hash>",
  "verdict": "观察",
  "annual_return": 0.082,
  "sharpe": 0.71,
  "max_drawdown": -0.22,
  "data_range": "2019-01-01 ~ 2025-12-31",
  "notes": "换手率因子 2019 前无效已跳过；样本覆盖 A 股约 4200 只"
}
```

### 3.3 迭代优化限制

- 同一因子 key 每轮最多调整 **1 个参数**，并另存新实验（不覆盖旧实验）
- 连续 3 轮结论均为"无效"或"勉强"，停止迭代并输出失败分析
- 参数搜索空间超过 20 个组合时，必须先做单因子截面检验（IC/RankIC），再选前 3 个参数组合回测

### 3.4 DeepSeek HARNESS 数据访问

HARNESS 可通过以下端点读取量化数据（只读，不触发修改）：

| 端点 | 用途 |
|---|---|
| `GET /quant/data?type=factors` | 因子库摘要（分类 + 可用性） |
| `GET /quant/data?type=experiments` | 最近 20 次回测结论 |
| `GET /quant/data?type=pools` | 已保存候选池列表 |
| `GET /quant/data?type=signals` | 信号计划列表 |
| `GET /quant/data?type=screener&factors=f1,f2&top_n=30` | 实时截面筛选 |

**禁止**：通过 HARNESS 直接写入 config 文件、触发回测、删除实验记录。

---

## 四、质量门控检查清单

Agent 在提交任何因子或回测结论前，必须自检：

- [ ] 因子无前视偏差（财报用 available_date，技术指标用前收盘）
- [ ] 参数全部通过 `params` 传递，无硬编码周期数
- [ ] 回测区间 ≥ 1 年
- [ ] 费用模型已启用（commission_bps ≥ 3，stamp_tax_bps ≥ 10，slippage_bps ≥ 5）
- [ ] 换手率因子的结论限定在 2019-01-01 之后
- [ ] 实验已存档（archived=true）并记录了参数快照
- [ ] 结论分级由 `_grade_verdict()` 自动计算，未被手动覆盖
