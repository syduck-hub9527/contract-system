# 币安合约 5 分钟 DIF 实时监控

多空概率扩展正在按 [开发阶段与G1状态](docs/probability-implementation-status.md) 实施。
已新增指标与 [离线研究管线](research/README.md)，尚无通过G1的概率模型。方案A已更新为14项特征v2，真实采集方法见研究文档。
`RADAR_SIGNALS_ENABLED=0` 为当前默认，保留原页面/API；设1仅采集内部收盘指标。

实时扫描所有**交易中的永续合约**（默认 U 本位 + 币本位），计算：

- `EMA21`、`EMA50`、`EMA200`，均基于**5分钟收盘价**。
- `DIF = EMA21 - EMA50`。
- 价格归一化 `DIF% = (EMA21 - EMA50) / 最新价 × 100%`，用于不同币价之间的筛选。
- **盘中金叉 / 死叉**：当前**尚未收盘**的5分钟K线，DIF已经越过零轴；可能反复改变。
- **确认金叉 / 死叉**：5分钟K线已收盘，DIF相对前一根已收盘K线越过零轴。最近5分钟的确认信号可见。
- 价格高于还是低于 EMA200，用于辅助判断趋势；默认不强制过滤。
- **EMA21 斜率突变（急拉 / 急跌）**：斜率 = EMA21 每根5分钟K线的变化百分比。把当前K线的斜率与**此前5根已收盘K线的平均斜率**比较，差值 `delta = 当前斜率 − 此前5根平均斜率`；当 `|delta| ≥ max(最小变化, 倍数 × 此前5根平均斜率幅度)` 时触发，`delta > 0` 为 **EMA21 急拉**（重大拉升），`delta < 0` 为 **EMA21 急跌**（重大下跌）。页面可调“斜率倍数”（默认3.0）与“最小变化”（默认0.100%）；刚转向（例如此前持续下行、现在突然上行）同样会触发。当前K线未收盘时用盘中EMA21，没有进行中的K线时用最近一根已收盘K线。
- **24h 成交量**：列表中每个合约的滚动24小时成交量，以“万 / 亿”缩写显示，悬停可查看精确值；默认每60秒批量刷新。
- **资金费率**：当前结算周期的资金费率（正值表示多头付给空头），悬停可查看下次结算时间；与成交量同步每60秒批量刷新。

## 启动方法

1. 下载ZIP并解压到一个文件夹。电脑安装 Python 3.10 或更高版本。
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

## 运行原理

- 启动时：REST 获取永续合约名单，每个合约获取499根5分钟K线，以便计算EMA200，并显示初始化进度。
- 历史载入期间：逐批建立 WebSocket 订阅（不是反复下载CSV/Excel）。
- 运行时：币安有新的K线数据就重新计算**盘中临时 DIF**；K线收盘 `x=true` 后再计算确认信号。
- 浏览器：**每2秒**读取本机实时状态。网页版无需重新打开或按刷新。
- 网络断线：WebSocket 自动重连；若有错过的整根5分钟K线，会使用REST重新同步，以免假信号。
- 时钟校准：启动时读取币安服务器时间，用它判断K线是否收盘，本机时钟快慢不会影响EMA。
- 无成交的K线：币安不返回没有成交的5分钟K线，程序按图表软件的做法用上一根收盘价补齐。
- 限流：遇到 HTTP 418/429 时所有请求线程一起退避；重同步失败按指数退避重试，不会阻塞其他合约。
- 合约列表：每小时刷新一次，新上市的合约自动加入，已下架的自动移除。
- 仅访问币安**公开行情**，不使用API Key、无账户权限、不会自动下单、不会保存交易明细。

**提醒**：

- EMA21 斜率信号是盘中信号，K线收盘前可能变化或消失；默认参数（3.0倍 / 0.100%）可按币种活跃度在页面调整。
- 刚上市的合约可能没有210根完整5分钟K线，无法正常计算EMA200，会被略过；已跳过数量会显示。
- 部分不活跃合约可能没有持续交易，因此最新K线时间会略旧。不要把过时数据当作即时信号。
- 全球各地对币安 API 的网络访问可能不同。如果交易所服务在当前网络不可用，页面会显示连接异常；程序不能绕过当地法律或限制。
- DIF金叉、死叉是行情辅助信号，不能保证盈利，特别是杠杆合约存在显著风险。

## 使用接口

- U本位公开 REST `https://fapi.binance.com/fapi/v1/klines` 和 `exchangeInfo`。
- U本位 2026 新 WebSocket `wss://fstream.binance.com/market/stream`。
- 币本位公开 REST `https://dapi.binance.com/dapi/v1/klines` 和 `exchangeInfo`。
- 币本位 WebSocket `wss://dstream.binance.com/stream`。

## 技术说明

`EMA(L) = EMA_previous + 2/(L+1) * (price - EMA_previous)`，历史上从首根收盘价初始化（部分看盘软件用 SMA 作起点，499 根之后残余差异约 0.7%，对 DIF 影响很小）；K线未收盘时使用最新K线价对上一根**已收盘**EMA作临时计算，**不**把盘中每个tick累加为新EMA。这样的算法可以避免一根K线内多次采样导致EMA计算错误。


## 工程化运行

前端沿用原有配色、CSS、卡片、排序与筛选，在最新价格后增加“24h 成交量”与“资金费率”列，数据来自币安 `premiumIndex` 公开接口。原启动命令和 `/api/state`、`/dif-api/state` 继续兼容。

推荐使用独立Python环境（Linux/macOS）：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python binance_live_dif.py
```

Windows可用 `.venv\Scripts\activate` 激活后执行同样的安装、启动命令。Python最低3.10。

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
| `RADAR_VOLUME_REFRESH_SECONDS` | 60 | 成交量刷新周期，允许30至300秒 |
| `RADAR_REQUEST_SPACING` | 0.10 | 全局REST间隔/秒 |
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

### Linux / 阿里云 systemd

`deploy/contract-radar.service` 按 `/home/admin/contract-system` 和admin用户提供模板；按实际安装路径、用户名修改。先创建venv并安装依赖；可自行创建`.env`环境文件设置配置。若启用文件日志，提前创建logs目录并确保admin可写。

```bash
sudo cp deploy/contract-radar.service /etc/systemd/system/contract-radar.service
sudo systemctl daemon-reload
sudo systemctl enable --now contract-radar
sudo systemctl status contract-radar
sudo journalctl -u contract-radar -f
```

更新并重启：

```bash
git pull origin main
.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart contract-radar
```

通过远程浏览器访问时，可继续现有反向代理；`deploy/nginx.conf.example`提供转发模板，设置上游Host为127.0.0.1以兼容Host检查。直接远程监听需显式设置 `RADAR_HOST=0.0.0.0` 与允许的 `RADAR_ALLOWED_HOSTS`，并按部署环境配置网络访问权限。本项目HTTP服务没有登录认证，默认保持本机访问。

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
```

测试锁定增加成交量后的UI副本、原CSS与原版EMA计算/盘中更新快照；覆盖成交量单位、缺失/零值/过期、缓存恢复、配置、限流重试、线程停止、队列、HTTP状态接口和安装包资源。上述检查在本地手动执行，可使用Python3.10或3.12。

模块职责见 [架构与维护说明](docs/architecture.md)。

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
