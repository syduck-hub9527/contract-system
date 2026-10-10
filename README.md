# 币安合约 5 分钟 DIF 实时监控

实时扫描所有**交易中的永续合约**（默认 U 本位 + 币本位），计算：

- `EMA21`、`EMA50`、`EMA200`，均基于**5分钟收盘价**。
- `DIF = EMA21 - EMA50`。
- 价格归一化 `DIF% = (EMA21 - EMA50) / 最新价 × 100%`，用于不同币价之间的筛选。
- **盘中金叉 / 死叉**：当前**尚未收盘**的5分钟K线，DIF已经越过零轴；可能反复改变。
- **确认金叉 / 死叉**：5分钟K线已收盘，DIF相对前一根已收盘K线越过零轴。最近5分钟的确认信号可见。
- 价格高于还是低于 EMA200，用于辅助判断趋势；默认不强制过滤。

## 启动方法

1. 下载ZIP并解压到一个文件夹。电脑安装 Python 3.10 或更高版本。
2. **Windows**：可双击 `启动监控_Windows.bat`，它会自动安装所需的2个依赖包并运行程序；然后打开浏览器 `http://127.0.0.1:8765`。
3. **Mac/Linux或想手动运行**：在终端切换到解压后的本文件夹，执行：

```bash
python -m pip install -r requirements.txt
python binance_live_dif.py
```

4. 浏览器打开 `http://127.0.0.1:8765`，**无需刷新网页**。
5. 关闭监控：终端按 `Ctrl + C`。

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
- 仅访问币安**公开行情**，不使用API Key、无账户权限、不会自动下单、不会保存交易明细。

**提醒**：

- 不是说EMA21和EMA50完全相等才发信号。精确浮点数为零很少出现，默认筛选 `|DIF%| <= 0.020%`，页面可以调整阈值。
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

`EMA(L) = EMA_previous + 2/(L+1) * (price - EMA_previous)`，历史上从首根收盘价初始化；K线未收盘时使用最新K线价对上一根**已收盘**EMA作临时计算，**不**把盘中每个tick累加为新EMA。这样的算法可以避免一根K线内多次采样导致EMA计算错误。
