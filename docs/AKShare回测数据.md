# AKShare A 股日线下载

使用项目内的独立 Python 环境，不需要 DeepSeek API Key 或 Tushare Token。

## 安装和下载

在仓库根目录运行 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-data.txt

# 按本机配置下载（模板默认为 20 只均匀抽样；limit: 0 为全量）
.\.venv\Scripts\python.exe -m data.akshare_loader

# 当前全 A 股名单，完整下载（耗时随网络和源站限流变化）
.\.venv\Scripts\python.exe -m data.akshare_loader --all --start 2019-01-01

# 指定代码/区间；支持沪深京股票
.\.venv\Scripts\python.exe -m data.akshare_loader --codes 600519.SH,000001.SZ --start 2021-01-01 --end 2025-12-31

# 当前沪深 300 成分股；这是当前成分回溯，不能当历史成分回测
.\.venv\Scripts\python.exe -m data.akshare_loader --universe csi300 --all

# 查看进度；同一配置再次运行可从经验证的断点续传
.\.venv\Scripts\python.exe -m data.akshare_loader --status
```

配置位于 `config/akshare.yaml`；缺失时使用 `config/akshare.yaml.example`。
`limit: 0` 表示所选股票池的全部股票；非零为均匀抽样。
源站顺序、超时、重试和请求间隔均由配置提供，不在页面里写死。
请求当天尚未收盘时，下载器排除当日日线；日期以 Asia/Shanghai 为准。

东财日线失败时，依次尝试配置中的其他 AKShare 通道，逐股保留失败原因和实际成功通道。
通道连续失败达到配置门槛后会暂时冷却，避免对故障通道重复请求。下载器使用操作系统文件锁拒绝同一工作区的重复写入任务。
`--refresh` 会重新下载整个请求窗口。前复权价格会随除权除息改变，因此更新日期窗口后重新抓取窗口内历史，不只拼接新日期。
断点校验包含股票池、日期窗口、复权方式、通道顺序和 AKShare 版本；不把空结果标为成功。
从抽样扩大为全量时，只要日期、复权、通道和版本一致，已有下载报告中的成功股票会先检查缓存，再复用。
短于 `minimum_rows` 的真实新股历史仍会入库，以 `backtest_eligible_by_length: false` 标明暂不满足回测长度，不因样本短而丢弃数据。

## 文件和数据读取

默认数据目录为 `data/cache/`；也可通过 `LWQUANT_CACHE_DIR` 或全局参数的 `data.cache_dir` 指定。

| 文件 | 内容 |
|---|---|
| `bars.db` / `bars_incr.db` | 项目标准日线缓存，通过 `DailyCache` 合并读取 |
| `stock_basic.db` | 当前股票名单和当前简称；历史 IPO、行业信息有则保留，无则保持未知 |
| `akshare_universe_latest.csv` | 本次使用的动态股票名单快照 |
| `akshare_checkpoint_*.json` | 逐股下载和断点审计记录 |
| `output/akshare_download.json` | 最新进度、覆盖、失败和限制说明 |
| `output/akshare_download_*.json` | 每次下载的结束报告 |

数据仅在本机持有，缓存和输出均已排除出 Git。不要提交或再分发行情数据。

策略和回测统一通过缓存读取：

```python
from data.cache import DailyCache

cache = DailyCache()
bars = cache.get_daily("600519.SH", start="2021-01-01", end="2025-12-31", adjust="qfq")
# 如果下载的是抽样池，上例股票不一定入选；请使用报告中实际成功的 code。
```

启动 `启动.cmd` 后，数据页的「AKShare 历史日线」从 `/api/data/akshare` 读取进度和逐股覆盖。
源代码启动脚本会优先使用项目 `.venv`（发布包仍优先使用随包 Python）。

## 字段口径和回测限制

统一存储：日期为 `YYYY-MM-DD`，成交量为股，成交额为元，换手率和涨跌幅为百分数。
东财成交量由手转股；新浪/当前腾讯接口的换手率从小数转百分数。
具体输入单位核对了 [AKShare 官方股票接口文档](https://akshare.akfamily.xyz/data/stock/stock.html)。

历史 ST 状态没有可靠来源时写入 NULL，不用当前名称推断历史状态，也不写成「非 ST」。
缺失成交额、换手率保留空值；2019 年前换手率置空。下载器检查 OHLC、日期、行数和写入后读回，不向前填补缺失行情。
前复权负价格会被拒绝，并记录失败原因；如需其他复权口径，可调整配置，但现有默认回测读取 `qfq`。

当前股票名单不含完整历史退市样本，存在幸存者偏差；新股实际覆盖可能晚于请求开始日期。
仅下载行情不等于完成基本面、历史市值、历史 ST、涨跌停价、公司行为和交易日历的补齐。

旧 `backtest.bt_runner` 的股票池依赖 `hist_mv.db`，还包含固定历史窗口和停牌填充等近似。
本下载器不会伪造该市值库，也不会把数据下载通过解释成策略通过验收。
正式策略回测仍需按项目 `backtest-acceptance` 核对 T+1 开盘执行、不可成交过滤、成本和样本边界。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest validation.test_akshare_pipeline -v
```

离线检查覆盖三通道单位、排序后的涨跌幅、ST 空值读写、2019 前换手屏蔽、价格异常拒绝和代理环境恢复。
网络可用性和实际下载行数以本机最新下载报告为准。
