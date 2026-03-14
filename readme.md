# Intraday Quant Engine

CME 微期货**多标的**实时流动性监控 + 经济物理学概率密度分析工具，接入 IBKR TWS，终端全屏 TUI 展示。

---

## 功能概览

| 模块 | 说明 |
|------|------|
| **多标的并行引擎** | `MultiEngine` 管理任意数量标的，各自独立 Feed + 引擎，共享 DisplayBridge |
| **CME 流动性引擎** | 按交易时段自动切换窗口大小，计算价格离散度、冲击成本、Delta、VWAP |
| **ΔP / φ(ΔP) 概率密度** | 纯 Python 实现中心极限定理，自动累积 ΔP 样本，估算 CLT 均值/方差/95% CI |
| **指数衰减加权统计** | `DecayWeightedTracker` — 权重 $w_i = e^{-k(t_{now}-t_i)}$，衰减系数 $k$ 随流动性动态梯度调整，基础覆盖锚定 300s |
| **时段自治切换** | COMEX 维护期 / 亚盘 / 欧美重叠盘 / 美国下午盘，window_sec / min_samples / history_size 自动调整 |
| **信号引擎** | 每窗口结算后评估冲击突增、厚尾加剧、买卖失衡、成交量异常、流动性枯竭，通过回调广播 |
| **Rich TUI** | 终端全屏多标的布局：Header / 各标的指标列 / 信号告警 / 历史窗口表 |
| **IBKR 接入** | tickByTick Last 逐笔推送，支持 paper / live，自动选最近未到期主力合约 |
| **持久化（DuckDB）** | 每窗口写入 `window_results` + `physics_stats` 两张表，批量缓冲，支持导出 Parquet |
| **Parquet 高频快照** | `SnapshotExporter` 每隔 N 秒用 DuckDB `COPY ... TO` 导出最新数据，< 1 ms 无锁 |
| **Streamlit 实时看板** | `dashboard/app.py` 读取快照，Plotly 双轴图自动刷新；原始数据 + 三大分析模块 |

---

## 环境要求

- Python **3.10+**
- IBKR TWS 或 IB Gateway（已登录，API 已启用）
- conda / venv 均可

---

## 安装

```bash
# 1. 克隆 / 解压项目
cd /path/to/project          # 父目录，intraday/ 在此目录下

# 2. 创建并激活环境（以 micromamba / conda 为例）
micromamba create -n quant python=3.10 -y
micromamba activate quant

# 3. 安装主引擎依赖
pip install ib_insync pytz rich duckdb

# 4. 安装看板依赖（Streamlit + 图表）
pip install streamlit plotly pandas
```

> **无 numpy / scipy 依赖**，全部核心计算为纯 Python。

---

## TWS / IB Gateway 配置

```
TWS → Edit → Global Configuration → API → Settings
  ✅ Enable ActiveX and Socket Clients
  ✅ Allow connections from localhost only
  Socket port: 7497  （模拟账户）
              7496  （实盘账户）
```

---

## 启动

```bash
cd /path/to/project          # 进入 intraday/ 的父目录
python -m intraday.main
```

首次启动打印：

```
◈ 连接 TWS 7497  (GC / ES / NQ)...
  ✅ GC    → GCM6
  ✅ ES    → ESM6
  ✅ NQ    → NQM6
```

随后进入 TUI 全屏，**Ctrl+C** 退出并打印各标的最终状态。

---

## 用户配置

编辑 `intraday/main.py` 中的配置段：

```python
TWS_PORT    = 7497       # paper=7497 / live=7496
MIN_SAMPLES = 5          # 每窗口最少 N 笔即结算（低频时段兜底）

SYMBOLS = [
    SymbolSpec(GC_CONFIG, last_trade_date="202606"),
    SymbolSpec(ES_CONFIG, last_trade_date="202606"),
    SymbolSpec(NQ_CONFIG, last_trade_date="202606"),
]
```

- `last_trade_date` 留空 `""` = 自动选最近未到期主力合约
- 可随意增删 `SYMBOLS` 列表中的条目，最多受 TWS API clientId 数量限制（默认 base=10，自动递增）

### 支持品种

| 代码 | 名称 | 交易所 | 合约乘数 |
|------|------|--------|--------|
| GC  | 黄金 | COMEX | 100 oz/手 |
| ES  | E-mini 标普 500 | CME | $50/点 |
| NQ  | E-mini 纳指 100 | CME | $20/点 |

> 如需新增品种，在 `config/products.py` 中添加 `ProductConfig` 实例，并在 `SYMBOLS` 列表中引用即可。

---

## 项目结构

```
project/
└── intraday/
    ├── main.py                      # 入口：用户配置（TWS_PORT / SYMBOLS / DB_PATH / SNAPSHOT_*）
    ├── query.py                     # 独立查询脚本（交互菜单 + CLI 参数）
    ├── config/
    │   ├── products.py              # 品种静态参数（tick_size, multiplier, 信号阈值…）
    │   └── sessions.py              # 时段时间边界（COMEX 四段，hhmm 精度）
    ├── core/
    │   ├── types.py                 # 数据结构：Tick / WindowResult / PhysicsStatsResult
    │   ├── signals.py               # SignalEvent / SignalType / Severity 枚举
    │   ├── price_distribution.py    # PriceDistributionTracker — CLT / ΔP 等权概率密度（备用）
    │   ├── decay_tracker.py         # DecayWeightedTracker — 指数衰减加权统计，动态覆盖窗口
    │   ├── liquidity_engine.py      # LiquidityEngine — CME 流动性窗口计算
    │   ├── physics_stats.py         # EconophysicsStats — 主统计用衰减追踪器，备用等权追踪器
    │   ├── persistence.py           # Persistence — DuckDB 批量写入，Parquet 导出
    │   └── snapshot_exporter.py     # SnapshotExporter — DuckDB COPY TO Parquet 高频快照
    ├── analytics/
    │   ├── signal_engine.py         # SignalEngine — 每窗口结算后多维信号评估与广播
    │   └── session_adapter.py       # SessionAwareAdapter — 时段切换驱动参数动态调整
    ├── app/
    │   ├── main_engine.py           # MainQuantEngine — 单标的调度核心（时段/流动性/物理/信号/持久化）
    │   └── multi_engine.py          # MultiEngine — 多标的管理器，并行连接，共享 Bridge + Persistence
    ├── data/
    │   └── ibkr_feed.py             # IBKRTickFeed — tickByTick Last 异步订阅
    ├── display/
    │   ├── bridge.py                # DisplayBridge — 观察者模式，解耦引擎与显示层
    │   └── terminal_rich.py         # RichTerminalDisplay — Rich TUI 全屏多标的布局
    └── dashboard/
        └── app.py                   # Streamlit 实时看板 — 读取 Parquet 快照，Plotly 图表
```

---

## 数据流

```
TWS / IB Gateway
  └─ ib_insync (tickByTick Last)  ×N 标的（各占独立 clientId 线程）
      └─ IBKRTickFeed._dispatch(price, volume, ts, side)
          └─ MainQuantEngine.on_tick_received()
              ├─ LiquidityEngine  →  WindowResult（每窗口结算）
              │   ├─ EconophysicsStats.update(vwap, ts, volume)
              │   │   ├─ DecayWeightedTracker  →  k = k_base×(1+λ×r_liq)  动态调整
              │   │   │   └─ w_i = exp(-k*(t_now-t_i))  加权矩 → DecayStats
              │   │   └─ PriceDistributionTracker  →  等权备用 → PhysicsStatsResult
              │   ├─ Persistence.write_window()  ┐
              │   ├─ Persistence.write_physics() ┘  批量缓冲 → DuckDB
              │   ├─ SignalEngine.evaluate()  →  List[SignalEvent]（信号广播）
              │   └─ DisplayBridge.emit(WindowResult)
              │       └─ RichTerminalDisplay.on_window()  →  TUI 刷新
              └─ SessionAwareAdapter  →  动态调整 window_sec / min_samples / history_size
```

---

## 动态衰减覆盖

超短线场景下，等权统计会让旧数据与新数据权重相同，导致信号滞后。`DecayWeightedTracker` 引入指数衰减权重，并根据当前流动性动态调整衰减速度。

### 权重公式

$$w_i = e^{-k \cdot (t_{now} - t_i)}$$

### 动态衰减系数（流动性梯度）

$$k_{eff} = k_{base} \times (1 + \lambda \times r_{liq}), \quad r_{liq} = \frac{V_{window}}{V_{avg20}}$$

| 流动性状态 | $r_{liq}$ | $k_{eff}$（默认参数） | 有效半衰期 | 覆盖时长（99%） |
|-----------|----------|-------------------|-----------|---------------|
| 低流动性   | 0.3      | ≈ 0.00162         | ≈ 428s    | ≈ 2840s        |
| 标准       | 1.0      | ≈ 0.00693         | ≈ 100s    | ≈ 665s         |
| 高流动性   | 3.0      | ≈ 0.02080 (clip)  | ≈ 33s     | ≈ 222s         |

> 基础参数：`k_base=0.00231`（半衰期 300s）、`λ=2.0`、`k_min=0.00050`、`k_max=0.05`

### 参数调优

在 `config/products.py` 或 `main.py` 中为不同品种单独设置：

```python
from intraday.core.decay_tracker import DecayConfig

# ES / NQ 超短线激进模式
es_decay = DecayConfig(k_base=0.00462, lam=3.0, k_min=0.001, k_max=0.08)
# GC 黄金保守模式
gc_decay = DecayConfig(k_base=0.00115, lam=1.0, k_min=0.0002, k_max=0.02)
```

通过 `MainQuantEngine(decay_config=es_decay)` 传入（`MultiEngine` 扩展后支持）。

### TUI 显示

φ(ΔP) 面板新增衰减元信息行：

| 字段 | 含义 |
|------|------|
| 衰减 k | 当前有效衰减系数及对应半衰期 |
| 有效覆盖 | 99% 权重集中的时间范围（秒） |
| 流动性倍 | 当前窗口成交量 / 近 20 窗口均量 |
| 等效样本 | $\sum w_i$（加权等效样本量） |

---

## 持久化（DuckDB）

每个窗口结算后自动写入本地 DuckDB 数据库，所有品种共享同一连接。

### 数据库表结构

**`window_results`** — 基础行情

| 字段 | 类型 | 说明 |
|------|------|------|
| ts / dt | DOUBLE / TIMESTAMPTZ | 窗口结束时间戳 |
| symbol / session | VARCHAR | 品种 / 时段 |
| vwap / high_price / low_price | DOUBLE | 价格 |
| total_volume / tick_count | BIGINT / INT | 成交量 / 笔数 |
| price_levels / price_range_abs | INT / DOUBLE | 价格离散度 |
| impact_bps / impact_dollar | DOUBLE | 冲击成本 |
| buy_volume / sell_volume / delta / delta_ratio | — | 订单流 |

**`physics_stats`** — 衰减统计快照

| 字段 | 类型 | 说明 |
|------|------|------|
| delta_p / mean / std / skewness / kurtosis | DOUBLE | ΔP 统计矩 |
| ci_lo / ci_hi | DOUBLE | 95% CLT 置信区间 |
| k_effective / half_life_sec / coverage_sec | DOUBLE | 衰减元信息 |
| liquidity_ratio / eff_n | DOUBLE | 流动性倍数 / 等效样本量 |

### 持久化配置（main.py）

```python
DB_PATH     = None   # None = ~/results/intraday.duckdb
PARQUET_DIR = None   # None = ~/results/parquet/
BATCH_SIZE  = 10     # 每累积 10 条批量写入（同时每条即时落盘）

# Parquet 快照配置（供 Streamlit 看板读取）
SNAPSHOT_DIR      = None   # None = ~/results/snapshots/
SNAPSHOT_INTERVAL = 3.0    # 导出间隔（秒）
SNAPSHOT_TAIL     = 500    # 每次导出最新 N 行
ENABLE_SNAPSHOT   = True   # False = 全局关闭快照
```

### 导出 Parquet

```python
# 程序内
me.export_parquet()             # 导出今日
me.export_parquet("20260219")   # 导出指定日期
```

```bash
# 命令行
python -m intraday.query --export 20260219
```

---

## Streamlit 实时看板

主引擎每隔 `SNAPSHOT_INTERVAL` 秒通过 DuckDB `COPY ... TO` 把最新数据无锁导出为两个 Parquet 文件（< 1 ms）：

```
~/results/snapshots/
    snapshot_wr.parquet   ← window_results 最新 N 行
    snapshot_phy.parquet  ← physics_stats  最新 N 行
```

Streamlit 用 `duckdb.query("SELECT * FROM read_parquet(...)")` 直接读取，每 3 秒 `st.rerun()` 自动刷新。

### 时区

看板所有时间戳自动读取**服务器系统时区**，无需任何配置：

```python
# dashboard/app.py（自动生效，无需手动修改）
LOCAL_TZ = datetime.datetime.now().astimezone().tzinfo
```

| 部署场景 | 行为 |
|---------|------|
| 服务器系统时区 = `America/Chicago`（CME 所在地） | 显示 CST / CDT |
| 服务器系统时区 = `Asia/Shanghai` | 显示 CST +8 |
| 任意其他时区 | 自动跟随系统设置 |

如需强制指定时区，将 `LOCAL_TZ` 改为 `zoneinfo.ZoneInfo("America/Chicago")` 等即可。

### 启动看板

```bash
# 终端 1：主引擎
python -m intraday.main

# 终端 2：看板
streamlit run intraday/dashboard/app.py

# 远程服务器（绑定公网端口）
streamlit run intraday/dashboard/app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true

# SSH 隧道访问远程服务器
ssh -L 8501:localhost:8501 user@your-server
# 本地浏览器打开 http://localhost:8501
```

### 看板布局

| 区域 | 内容 |
|------|------|
| **侧边栏** | 快照目录 / 刷新间隔 / 加载行数 / 装甲阈值 / 峰度基准线 / 快照在线状态（含快照更新时间） |
| **KPI 横排** | 每标的：VWAP / Impact bps / Delta Ratio / Vol×Levels |
| **Tab：原始数据** | `window_results` + `physics_stats` 可交互表格，各附一键下载 CSV 按钮 |
| **Tab：分析看板** | 按标的分 Tab，包含三大模块（Plotly 图表） |

#### 三大分析模块

**🏰 模块 1 — 价格轨迹 vs 装甲厚度（VWAP & Price Levels）**

Plotly 双轴图：左轴 VWAP 折线 + High/Low 区间带，右轴 Price Levels 条形图条件着色：
- 🟢 D ≤ 3：装甲坚固，适合均值回归
- 🟡 D 4~9：正常区间
- 🔴 D ≥ 10：流动性真空，立刻警惕

阈值在侧边栏可实时拖动调整。

**☢️ 模块 2 — 尾部风险雷达（Kurtosis & Skewness）**

- 峰度走势面积线（紫色）+ 偏度虚线 + k_effective 参考线
- 橙色虚线标注高斯基准（默认 3.0，超额峰度填 0.0）
- 峰度"刺穿"基准线时视觉冲击极强，作为均值回归策略的熔断信号

**🔬 模块 3 — 引擎变焦区（吸收率与自适应记忆）**

- 左：`half_life_sec` 面积图 — 直观看到引擎记忆随行情加速而缩短
- 右：VWAP vs 吸收率 V/D（volume / price_levels）双轴背离图
  - VWAP 创新高而 V/D 下降 → 买盘虚浮，顶部信号

### 数据链路

```
MainQuantEngine（每窗口结算）
  └─ Persistence.write_window() / write_physics()
      └─ SnapshotExporter.maybe_export(conn)   ← 在写锁内，复用同一连接
          ├─ COPY window_results TO snapshot_wr.parquet  (ZSTD)
          └─ COPY physics_stats  TO snapshot_phy.parquet (ZSTD)
                                                    │
                                         Streamlit st.rerun() 每 N s
                                                    │
                              duckdb.query("SELECT * FROM read_parquet(...)")
```

---

## 数据查询

### 交互式菜单

```bash
cd /path/to/project
conda run -p /path/to/envs/quant python -m intraday.query
```

菜单包含：今日汇总 / 最新记录 / 厚尾风险 / 小时聚合 / 衰减趋势 / 流动性真空 / 自定义 SQL。

> **主程序运行中也可同时查询**：query 脚本检测到写锁冲突时会自动复制数据库快照，不影响主程序写入。

### CLI 参数

以下示例均需在正确的 conda 环境下运行，以 `conda run -p /path/to/envs/quant` 作为前缀（下为简写）：

```bash
# 最新 30 条窗口记录（ES）
python -m intraday.query --symbol ES --tail 30

# 最新 20 条衰减统计（全部品种）
python -m intraday.query --physics 20

# 今日各标的汇总
python -m intraday.query --summary

# 按小时聚合（NQ，今天）
python -m intraday.query --symbol NQ --hourly

# 衰减系数 k 变化趋势（最近 3 小时）
python -m intraday.query --symbol GC --decay 3

# 厚尾风险时段
python -m intraday.query --risk

# 流动性真空时段
python -m intraday.query --vacuum --symbol ES

# 数据库行数统计
python -m intraday.query --count

# 自定义 SQL
python -m intraday.query --sql "SELECT symbol, count(*) FROM window_results GROUP BY 1"

# 导出今日 Parquet
python -m intraday.query --export 20260219
```

### 直接用 DuckDB CLI

```bash
duckdb ~/results/intraday.duckdb

-- 今日各标的成交量
SELECT symbol, sum(total_volume) AS vol
FROM window_results
WHERE strftime(dt, '%Y%m%d') = '20260219'
GROUP BY 1;

-- 衰减 k 走势（ES 最近 1 小时）
SELECT strftime(dt,'%H:%M:%S'), k_effective, coverage_sec, liquidity_ratio
FROM physics_stats
WHERE symbol='ES' AND dt >= now() - INTERVAL 1 HOUR
ORDER BY ts DESC;
```

---

## 时段参数

`SessionAwareAdapter` 在每次 tick 时检查当前时段，切换时自动更新三个参数：

| 时段 | window_sec | min_samples | history_size | 覆盖时长 |
|------|-----------|-------------|-------------|--------|
| Maintenance（维护期） | 5s | 1 | 60 | 300s |
| Asian_Session（亚盘） | 5s | 2 | 60 | 300s |
| Euro_US_Overlap（欧美高峰） | 5s | 10 | 60 | 300s |
| US_Afternoon（美盘下午） | 5s | 5 | 60 | 300s |

---

## 指标说明

### CME 流动性面板

| 指标 | 说明 |
|------|------|
| Price Levels | 窗口内出现的独立价格档位数 |
| Impact bps | 估算市场冲击成本（基点） |
| Delta Ratio | (买量−卖量)/(买量+卖量)，正=多头主导 |
| VWAP | 成交量加权均价 |

### φ(ΔP) 概率密度面板

| 指标 | 说明 |
|------|------|
| μ (CLT) | 衰减加权均值，趋近于 0 表示随机游走 |
| σ (CLT) | 衰减加权标准差，衡量价格扩散速度 |
| 95% CI | 下一聚合窗口价格变动的 95% 置信区间 |
| 超额峰度 | 正态基准=0；>5 厚尾风险，<1 接近理想扩散 |
| 偏度 | 正=右偏（上涨尾部更厚），负=左偏 |
| 衰减 k | 当前有效衰减系数（含对应半衰期） |
| 有效覆盖 | 当前统计覆盖时长，随流动性动态伸缩 |
| 流动性倍 | 当前窗口相对近 20 窗口的流动性倍数 |
| 等效样本 | 衰减加权等效样本量 Σwᵢ |

### 信号类型

| 信号 | 严重级别 | 触发条件 |
|------|---------|---------|
| `冲击突增` | WARN / ALERT | impact_bps 超过品种阈值（warn_bps / alert_bps） |
| `厚尾加剧` | WARN / ALERT | 超额峰度超过 kurt_warn / kurt_alert |
| `买卖失衡` | WARN | delta_ratio 绝对值超过 delta_imbal_warn（默认 0.65） |
| `成交量异常` | WARN | 本窗口成交量 > 近 20 窗口均值 × volume_surge_x（默认 3×） |
| `流动性枯竭` | ALERT | 成交量为 0 或 tick_count = 0 |

---

## 常见问题

**连接超时 / 找不到合约**
- 确认 TWS 已登录并处于运行状态
- 检查 API 设置中端口号与 `TWS_PORT` 一致
- `last_trade_date` 留空让程序自动选合约，避免填入已到期月份

**`RuntimeError: There is no current event loop`**
- 已修复：`ibkr_feed.py` 在线程入口自动创建事件循环，Python 3.10+ 适用

**TUI 显示乱码**
- 终端需支持 UTF-8 和 256 色，推荐 iTerm2 / macOS Terminal（Solarized 主题）

**部分标的连接失败**
- 程序启动时打印 `❌ 部分标的连接失败`，检查对应品种合约月份是否已到期
- 每个标的占用独立的 IBKR clientId（从 `base_client_id=10` 起递增），确认 TWS 允许足够的并发连接数