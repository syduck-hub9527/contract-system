# 币安合约 5 分钟 EMA21 急拉急跌监控

实时扫描所有**交易中的永续合约**（默认 U 本位 + 币本位），页面专注于 **EMA21 急拉 / 急跌**，分为“EMA21 急拉”“EMA21 急跌”“信号记录”三个标签页。系统计算：

- `EMA21`、`EMA50`、`EMA200`，均基于**5分钟收盘价**。
- 价格相对 EMA50（约4小时方向）与 EMA200（约1天方向）的位置，显示在“趋势”列；默认不强制过滤。
- **EMA21 斜率突变（急拉 / 急跌）**：斜率 = EMA21 每根5分钟K线的变化百分比。把当前K线的斜率与**此前5根已收盘K线的平均斜率**比较，差值 `delta = 当前斜率 − 此前5根平均斜率`；当 `|delta| ≥ max(最小变化, 倍数 × 此前5根平均斜率幅度)` 时触发，`delta > 0` 为 **EMA21 急拉**（重大拉升），`delta < 0` 为 **EMA21 急跌**（重大下跌）。页面可调“斜率倍数”（默认3.0）与“最小变化”（默认0.100%）；刚转向（例如此前持续下行、现在突然上行）同样会触发。当前K线未收盘时用盘中EMA21，没有进行中的K线时用最近一根已收盘K线。
- **24h 成交量**：列表中每个合约的滚动24小时成交量，以“万 / 亿”缩写显示，悬停可查看精确值；默认每60秒批量刷新。
- **资金费率**：U本位取自全市场标记价格流 `!markPrice@arr@1s` 的 `r` 字段（约每秒更新），断流时自动回退到每60秒获取 `premiumIndex`；币本位没有全市场标记价格流，取自 `premiumIndex` 的 `lastFundingRate`（官方称“最近更新的资金费率”），每60秒获取。正值表示多头付给空头，悬停可查看下次结算时间。
- **量能**：当前K线到目前为止的成交量是此前20根已收盘K线平均成交量的几倍，显示在信号旁（≥2倍高亮）。可用“最低量能”只看放量信号；统计卡片“放量 急拉 / 急跌”按2倍计数。
- **信号记录**标签页：服务端按 `--slope-mult` / `--slope-min`（默认与页面相同：3.0倍 / 0.100%）判定，每根K线每个方向第一次触发时记录一条：触发时间与开盘后秒数、触发价、量能、EMA50/EMA200 位置、24h成交额、资金费率；之后跟踪1小时，记录 5/15/30/60 分钟后的价格、1小时内的最高与最低、以及所在K线收盘时信号是否仍成立。页面按信号方向（急跌按做空）并扣除“往返成本”显示收益，顶部汇总15分钟后的平均收益与盈利占比。页面上调整的阈值只影响列表显示，不影响记录。内存保留最近2000条；启动时加 `--data-dir` 会同时追加写入 `events.jsonl`，重启后仍可查看。
- **最低成交额过滤**：只显示滚动24小时成交额不低于设定值（万U）的合约，过滤冷门合约的噪声信号；U本位按 USDT 成交额，币本位按币数量×价格折算。统计卡片同步按此过滤。
- **稳定排序与持续时长**：列表按信号首次出现的时间由新到旧排列，新信号出现在顶部，已有的行不再随每次行情更新跳动；信号旁显示已持续的时间。
- **新信号提醒**：点击“提醒”开启后，当前标签页出现新信号时发出浏览器通知和提示音；页面在后台时标题会显示未看信号数。设置保存在浏览器中。

## 启动方法

1. 下载ZIP并解压到一个文件夹。电脑安装 Python 3.12 或更高版本。
2. 在终端（Windows 用 PowerShell 或 CMD）切换到解压后的本文件夹，执行：

```bash
python -m pip install -r requirements.txt
python binance_live_dif.py
```

3. 浏览器打开 `http://127.0.0.1:8765`，**无需刷新网页**。
4. 关闭监控：终端按 `Ctrl + C`。

可选：只看 U 本位 / 币本位，或更改端口：

```bash
python binance_live_dif.py --market um
python binance_live_dif.py --market cm
python binance_live_dif.py --port 8888
```

推荐加上数据目录，用于保存运行状态和信号记录（目录会自动创建）：

```bash
python binance_live_dif.py --data-dir radar-data
```

保存状态后，8小时内重启只需为每个合约补拉缺失的几根K线（请求权重1），不再重新下载580根，几秒到二十秒即可恢复全部合约。

## 运行原理

- 启动时：REST 获取永续合约名单，每个合约获取580根5分钟K线并建立 EMA21/EMA50/EMA200，同时显示初始化进度。580根是 v4 按“15分钟 EMA50 种子残留≤0.05%”预留的暖机长度；当前版本尚未实现15m指标，该长度对5分钟 EMA21/EMA50 已远超所需，对 EMA200 的残留约0.3%。依据与限流测算见 [架构与维护说明](docs/architecture.md) 的“暖机长度与限流测算”一节。
- 历史载入期间：逐批建立 WebSocket 订阅（不是反复下载CSV/Excel）。
- 运行时：币安有新的K线数据就重新计算**盘中临时 EMA** 与 EMA21 斜率；K线收盘 `x=true` 后更新已收盘 EMA。
- 浏览器：**每2秒**读取本机实时状态。网页版无需重新打开或按刷新。
- 网络断线：WebSocket 自动重连；若有错过的整根5分钟K线，先只补拉缺失的K线（最多96根，请求权重1）并从原有 EMA 状态继续计算，结果与完整重算一致；不满足条件时才重新下载580根。
- 时钟校准：启动时读取币安服务器时间，用它判断K线是否收盘，本机时钟快慢不会影响EMA。
- 无成交的K线：币安不返回没有成交的5分钟K线，程序按图表软件的做法用上一根收盘价补齐。
- 限流：按请求权重计费，每5权重占用 `request_spacing` 秒，所有REST请求合计不超过每分钟1818权重；同时读取币安返回的 `X-MBX-USED-WEIGHT-1M`，达到 `weight_target` 时暂停到下一分钟。遇到 HTTP 418/429 时所有请求线程一起退避；重同步失败按指数退避重试，不会阻塞其他合约。
- 状态保存：配置 `--data-dir` 后每60秒及退出时原子写入 `state.json`。
- 合约列表：每小时刷新一次，新上市的合约自动加入，已下架的自动移除。
- 仅访问币安**公开行情**，不使用API Key、无账户权限、不会自动下单、不会保存交易明细。

**提醒**：

- EMA21 斜率信号是盘中信号，K线收盘前可能变化或消失；默认参数（3.0倍 / 0.100%）可按币种活跃度在页面调整。
- 刚上市的合约可能没有210根完整5分钟K线，无法正常计算EMA200，会被略过；已跳过数量会显示。
- 部分不活跃合约可能没有持续交易，因此最新K线时间会略旧。不要把过时数据当作即时信号。
- 全球各地对币安 API 的网络访问可能不同。如果交易所服务在当前网络不可用，页面会显示连接异常；程序不能绕过当地法律或限制。
- EMA21 急拉、急跌是行情辅助信号，不能保证盈利，特别是杠杆合约存在显著风险。

## 使用接口

- U本位公开 REST `https://fapi.binance.com/fapi/v1/klines` 和 `exchangeInfo`。
- U本位 2026 新 WebSocket `wss://fstream.binance.com/market/stream`（K线流与全市场标记价格流 `!markPrice@arr@1s`）。
- 币本位公开 REST `https://dapi.binance.com/dapi/v1/klines` 和 `exchangeInfo`。
- 币本位 WebSocket `wss://dstream.binance.com/stream`。

## 技术说明

`EMA(L) = EMA_previous + 2/(L+1) * (price - EMA_previous)`，历史上从首根收盘价初始化（部分看盘软件用 SMA 作起点，580 根之后 EMA200 残余差异约 0.3%，对 EMA21 斜率影响很小）；K线未收盘时使用最新K线价对上一根**已收盘**EMA作临时计算，**不**把盘中每个tick累加为新EMA。这样的算法可以避免一根K线内多次采样导致EMA计算错误。


## 工程化运行

前端沿用原有配色、CSS 与卡片，在最新价格后增加“24h 成交量”与“资金费率”列。页面只有一份源文件 `contract_radar/assets/dashboard.html`，服务与安装包都直接使用它。原启动命令和 `/api/state`、`/dif-api/state` 继续兼容。

推荐使用独立Python环境（Linux/macOS）：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python binance_live_dif.py
```

Windows可用 `.venv\Scripts\activate` 激活后执行同样的安装、启动命令。Python最低3.12。

也支持模块与安装后的命令行入口：

```bash
python -m contract_radar --market um --port 8765
python -m pip install --no-deps .
contract-radar --help
```

`requirements.txt` 固定部署依赖及其间接依赖；`pyproject.toml` 定义包、构建、CLI与开发工具。更新依赖时重新运行测试并调整固定版本。正常运行不需要Node。

### 配置与日志

配置优先级：默认值 < `RADAR_*` 环境变量 < 命令行。全部配置通过 `--help` 查看，格式与范围在启动前校验。Python启动不会自动读取`.env`。

```bash
export RADAR_MARKET=um
export RADAR_LOG_LEVEL=INFO
python -m contract_radar --log-file logs/radar.log
```

Linux通过环境变量设置配置：

```bash
export RADAR_MARKET=um
export RADAR_PORT=8765
python -m contract_radar
```

默认只输出控制台日志。`--log-file` 同时输出UTF-8文件，每份5MiB，保留3份旧文件。日志带时间、级别和线程名。Ctrl+C或SIGTERM停止后台任务、取消重试定时器、关闭WS/HTTP与REST连接。正在进行的HTTP请求受连接/读取超时约束；默认停止最多等待后台线程25秒，超时会记录日志，不能保证强制中断已在系统网络调用中的线程。

| 常用变量 | 默认值 | 用途 |
|---|---|---|
| `RADAR_HOST` / `RADAR_PORT` | 127.0.0.1 / 8765 | 监听地址与端口 |
| `RADAR_MARKET` | all | all / um / cm |
| `RADAR_WORKERS` / `RADAR_RESYNC_WORKERS` | 7 / 2 | 初始化与同步并发 |
| `RADAR_WS_BATCH` | 75 | 每条WS订阅数 |
| `RADAR_REFRESH_SECONDS` | 3600 | 合约列表与时钟刷新周期 |
| `RADAR_VOLUME_REFRESH_SECONDS` | 60 | 成交量与币本位资金费率的刷新周期（U本位资金费率在标记价格流断流时也按此回退），允许30至300秒 |
| `RADAR_REQUEST_SPACING` | 0.165 | 每5请求权重占用的秒数（按权重计费）：580根K线请求占0.165秒，权重1的请求占0.033秒；全部REST合计≤60/0.165×5=1818权重/分钟≤所有者目标1920 |
| `RADAR_WEIGHT_TARGET` | 1920 | 币安返回的每分钟已用权重达到此值时，所有REST请求暂停到下一分钟 |
| `RADAR_DATA_DIR` | 空 | 数据目录：保存 `state.json`（快速重启）与 `events.jsonl`（信号记录）；为空时不写文件 |
| `RADAR_SLOPE_MULT` / `RADAR_SLOPE_MIN` | 3.0 / 0.1 | 服务端记录急拉急跌使用的斜率倍数与最小变化（%）；页面阈值只影响显示 |
| `RADAR_CONNECT_TIMEOUT` / `RADAR_READ_TIMEOUT` | 5 / 15 | REST超时/秒 |
| `RADAR_RETRY_ATTEMPTS` | 3 | REST最大尝试次数 |
| `RADAR_RESYNC_CAPACITY` | 1000 | 同步队列上限 |
| `RADAR_HTTP_WORKERS` / `RADAR_HTTP_TIMEOUT` | 32 / 10 | HTTP并发连接上限与连接读写超时 |
| `RADAR_LOG_LEVEL` / `RADAR_LOG_FILE` | INFO / 空 | 日志级别与可选文件 |
| `RADAR_ALLOWED_HOSTS` | 127.0.0.1,localhost,[::1] | 允许Host，保留DNS重绑定防护 |

### 健康接口

- `/health`：进程可响应且未停止时200，不代表交易所已连接。
- `/ready`：至少一个合约有90秒内的新鲜WS行情、未同步且服务未停止时200，否则503；表示有行情可用，不代表所有合约都在线。
- 初次无法获取合约列表时每30秒后台重试；已有合约后按配置周期刷新。WS断线继续按退避重连。
- 两者返回的诊断字段还包括：`gap_resyncs` / `gap_resyncs_top`（K线缺口次数），`resync_incremental` / `resync_full`（增量补齐与完整重载次数），`warm_loaded`（启动时从保存状态恢复的合约数），`used_weight`（各 host 最近一次报告的每分钟已用权重），`mark_stream_at`（U本位标记价格流最近推送时间），`events`（内存中的信号记录条数）。

### 远程访问

通过远程浏览器访问时，需显式设置 `RADAR_HOST=0.0.0.0` 与允许的 `RADAR_ALLOWED_HOSTS`，并按网络环境配置访问权限。本项目HTTP服务没有登录认证，默认保持本机访问。

### 开发与验证

```bash
python -m pip install -r requirements.txt
python -m pip install '.[dev]'
ruff check .
ruff format --check .
python -m unittest discover -s tests -v
python -m build
python tests/check_wheel.py
```

前端兼容测试（仅开发需要Node22）：

```bash
npm install --no-save jsdom@30.1.2
node tests/test_ui.cjs
node tests/test_ui_v11.cjs
```

测试锁定原CSS、页面关键结构（标签页与脚本依赖的元素）与原版EMA计算/盘中更新快照，并验证增量补齐与完整重算逐位一致；覆盖成交量单位、缺失/零值/过期、缓存恢复、配置、限流重试、线程停止、队列、HTTP状态接口和安装包资源。上述检查在本地手动执行，使用Python3.12。

模块职责见 [架构与维护说明](docs/architecture.md)。

## 状态API字段

`/api/state` 与 `/dif-api/state` 返回相同内容（`/dif-api` 只是沿用的路径名）。消费方应按名字访问，并容忍未来新增字段。本版本已删除 DIF 与金叉死叉相关字段（`dif`、`dif_pct`、`live_cross`、`confirmed_cross`、`confirmed_at`、`live_cross_since`、`recent_cross`）。

顶层字段：

| 字段 | 含义 |
|---|---|
| `server_time` | 交易所校准时钟，毫秒 |
| `phase` | 当前阶段文字 |
| `total` | 已发现的合约总数 |
| `loaded` | 已完成初始化的合约数 |
| `skipped` | 已跳过数 |
| `ws_online` | 在线 WebSocket 连接数 |
| `ws_expected` | 预期 WebSocket 连接数 |
| `errors` | 最近 3 条错误，新的在前 |
| `rows` | 合约行数组 |

`rows` 数组中每个合约行的字段（共 30 个）：

| 字段 | 含义 | 前端是否使用 |
|---|---|---|
| `market` | `UM` 或 `CM` | 是 |
| `symbol` | 合约名 | 是 |
| `price` | 最新价 | 是 |
| `ema21` | 盘中 EMA21（用当前价对上一根已收盘 EMA 计算的临时值） | 否 |
| `ema50` | 盘中 EMA50（同上） | 否 |
| `ema200` | 盘中 EMA200（同上） | 否 |
| `above200` | `price > ema200` | 是 |
| `above50` | `price > ema50`（盘中） | 是 |
| `ema200_rising` | 已收盘 EMA200 是否高于前一根 | 否 |
| `updated_at` | 最近一次行情更新时间 | 是 |
| `current_candle` | 是否存在进行中的 K 线 | 否 |
| `ema21_slope_pct` | 当前 EMA21 斜率（%/根），数据不足时为 `null` | 是 |
| `ema21_slope_base_pct` | 此前 5 根已收盘 K 线的平均斜率（%/根），数据不足时为 `null` | 是 |
| `ema21_slope_base_abs_pct` | 此前 5 根的平均斜率幅度（%/根），数据不足时为 `null` | 是 |
| `ws_connected` | WebSocket 是否在线 | 是 |
| `resync_pending` | 是否正在重同步 | 是 |
| `volume` | 滚动 24h 成交量，见“列表成交量”一节 | 是 |
| `volume_unit` | 成交量单位（币本位为“张”） | 是 |
| `base_volume` | 基础资产数量（币本位为币数量） | 是 |
| `quote_volume` | 成交额（币本位无，为 `null`） | 是 |
| `base_asset` | 基础资产 | 是 |
| `quote_asset` | 报价资产 | 是 |
| `volume_updated_at` | 成交量获取时间（REST 响应接收时间） | 是 |
| `volume_close_time` | 成交量窗口结束时间（币安 `closeTime`） | 是 |
| `volume_stale` | 成交量是否超过三个刷新周期 | 是 |
| `volume_ratio` | 量能倍数：当前K线到目前为止的成交量 ÷ 此前20根已收盘K线平均成交量；没有进行中的K线时为最近一根已收盘K线与其之前20根相比；基准为0时为 `null` | 是 |
| `funding_rate` | 最近更新的资金费率（U本位来自标记价格流 `r`，币本位来自 `premiumIndex.lastFundingRate`；正值表示多头付给空头） | 是 |
| `next_funding_time` | 下次结算时间 | 是 |
| `funding_updated_at` | 资金费率获取时间 | 是 |
| `funding_stale` | 资金费率是否过期（距上次成功获取超过三个刷新周期，或尚无数据） | 是 |

待核实：币安文档未说明 `lastFundingRate`（以及标记价格流的 `r`）是最近一次已结算的费率还是本周期的实时预估值，因此 README 与页面列头暂用中性表述。核实方法是在同一个 `nextFundingTime` 内每分钟采样 `premiumIndex` 至少20次，并与 `/fapi/v1/fundingRate` 最新一条对比；确认后同步修改这里、上方功能说明和 `contract_radar/assets/dashboard.html` 的列头三处文案。

## 信号记录接口

`/api/events` 与 `/dif-api/events` 返回 `{"server_time", "events"}`，`events` 为最近2000条急拉/急跌记录，新的在前（`?kind=surge` 为兼容保留，结果相同）。旧版本记录的金叉死叉事件在启动时不再载入。

每条记录的字段：

| 字段 | 含义 |
|---|---|
| `id` | 递增编号 |
| `market` / `symbol` | 市场与合约 |
| `kind` | `surge_up`（急拉）或 `surge_down`（急跌） |
| `at` | 触发时间（毫秒） |
| `candle_t` / `elapsed_s` | 触发所在K线的开盘时间，以及触发时距开盘的秒数 |
| `price` | 触发时的最新价 |
| `slope` / `base` / `delta` | 当时的EMA21斜率、此前5根平均斜率、两者之差（%/根） |
| `threshold` / `gate` | 触发门槛，以及门槛由倍数（`mult`）还是最小变化（`min`）决定 |
| `volume_ratio` | 触发时的量能倍数，数据不足时为 `null` |
| `turnover` / `funding_rate` | 当时的24h成交额与资金费率，未知时为 `null` |
| `above50` / `above200` | 触发时最新价是否在 EMA50、EMA200 上方 |
| `held_at_close` | 所在K线收盘时信号是否仍成立；未观察到收盘时为 `null` |
| `ret_5m` / `ret_15m` / `ret_30m` / `ret_60m` | 触发后对应时间的价格相对 `price` 的涨跌幅（%，未按方向换算、未扣成本）；断线错过时为 `null` |
| `high_pct` / `low_pct` | 触发后1小时内的最高、最低价相对 `price` 的涨跌幅（%） |
| `done` | 是否已跟踪满1小时、不再更新 |

配置 `--data-dir` 时，结束的记录每60秒追加写入 `events.jsonl`（每行一条JSON），退出时未结束的记录也会写入；启动时读取最近2000条。

## 列表成交量

通过币安公开REST接口获取**滚动24小时**统计，不是自然日统计，也不是当前一根5分钟K线的成交量。5分钟K线与EMA信号算法保持原样。

| 市场 | 接口 | 列表数值与单位 | 悬停详情 |
|---|---|---|---|
| U本位 | `/fapi/v1/ticker/24hr` | `volume`，基础资产数量，例如 BTC | `quoteVolume` 成交额及报价资产单位，例如 USDT/USDC |
| 币本位 | `/dapi/v1/ticker/24hr` | `volume`，合约张数 | `baseVolume` 币数量及基础资产单位，例如 BTC |

币种单位取自对应市场的 `exchangeInfo`，不会仅凭合约名称猜测，也不会将币本位 `baseVolume` 错当成 USD 成交额。币本位接口没有 `quoteVolume`，该字段返回 `null`。

默认每60秒对每个启用市场发一次不带symbol的批量请求，覆盖所有在交易的永续合约；每次全市场请求权重40。独立后台任务使用已有全局节流、418/429退避、超时与停止事件，网络请求不持有状态锁。

尚未获取或缓存超过三个刷新周期时显示 `—`；悬停可查看原因。真实零成交量显示 `0`。接口失败保留上次缓存及其获取时间，超期后停止展示数值；不会把失败伪装成零成交量。获取时间表示REST响应的接收时间，窗口结束时间来自币安 `closeTime`，两者在悬停详情与API中独立提供。K线重同步不会丢失成交量，新上市合约自动纳入，下架时清理缓存。

状态API每行新增：`volume`、`volume_unit`、`base_volume`、`quote_volume`、`base_asset`、`quote_asset`、`volume_updated_at`、`volume_close_time`、`volume_stale`。数值缺失时为 `null`；`volume_stale=true` 时仍可能保留旧数值供诊断，消费方必须检查新鲜度。

```bash
python binance_live_dif.py --volume-refresh-seconds 60
```

官方文档：[USDⓈ-M 市场数据](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)、[COIN-M 市场数据](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-coin-m-futures/api/rest-api/market-data)。
