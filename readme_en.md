# Intraday Quant Engine

A **multi-symbol** real-time liquidity monitor for CME Micro Futures with econophysics probability density analysis, connected to IBKR TWS and displayed in a full-screen terminal TUI.

---

## Feature Overview

| Module | Description |
|--------|-------------|
| **Multi-Symbol Parallel Engine** | `MultiEngine` manages any number of symbols, each with an independent Feed + Engine, sharing a single DisplayBridge |
| **CME Liquidity Engine** | Auto-switches window size by trading session; computes price dispersion, impact cost, Delta, VWAP |
| **ΔP / φ(ΔP) Probability Density** | Pure-Python CLT implementation; accumulates ΔP samples and estimates CLT mean/variance/95% CI |
| **Exponential Decay Weighted Stats** | `DecayWeightedTracker` — weight $w_i = e^{-k(t_{now}-t_i)}$; decay coefficient $k$ dynamically adjusted by liquidity gradient, base coverage anchored at 300s |
| **Autonomous Session Switching** | COMEX maintenance / Asian session / Euro-US overlap / US afternoon — `window_sec`, `min_samples`, `history_size` auto-adjusted |
| **Signal Engine** | After each window settlement, evaluates impact spikes, fat-tail escalation, order imbalance, volume anomaly, liquidity exhaustion, and broadcasts via callbacks |
| **Rich TUI** | Full-screen multi-symbol layout: Header / per-symbol metric columns / signal alerts / historical window table |
| **IBKR Integration** | tickByTick Last tick-by-tick push, supports paper / live, auto-selects nearest unexpired front-month contract |
| **Persistence (DuckDB)** | Writes `window_results` + `physics_stats` tables after each window settlement; buffered batch writes; supports Parquet export |
| **High-Frequency Parquet Snapshots** | `SnapshotExporter` exports latest data via DuckDB `COPY ... TO` every N seconds; < 1 ms, lock-free |
| **Streamlit Live Dashboard** | `dashboard/app.py` reads snapshots; Plotly dual-axis charts with auto-refresh; raw data + three analysis modules |

---

## Requirements

- Python **3.10+**
- IBKR TWS or IB Gateway (logged in, API enabled)
- conda / venv both supported

---

## Installation

```bash
# 1. Clone / extract the project
cd /path/to/project          # parent directory containing the intraday/ package

# 2. Create and activate environment (e.g. micromamba / conda)
micromamba create -n quant python=3.10 -y
micromamba activate quant

# 3. Install core engine dependencies
pip install ib_insync pytz rich duckdb

# 4. Install dashboard dependencies (Streamlit + charts)
pip install streamlit plotly pandas
```

> **No numpy / scipy dependency** — all core computations are pure Python.

---

## TWS / IB Gateway Configuration

```
TWS → Edit → Global Configuration → API → Settings
  ✅ Enable ActiveX and Socket Clients
  ✅ Allow connections from localhost only
  Socket port: 7497  (paper account)
              7496  (live account)
```

---

## Starting the Engine

```bash
cd /path/to/project          # enter the parent directory of intraday/
python -m intraday.main
```

On first launch you'll see:

```
◈ Connecting to TWS 7497  (GC / ES / NQ)...
  ✅ GC    → GCM6
  ✅ ES    → ESM6
  ✅ NQ    → NQM6
```

The full-screen TUI then takes over. Press **Ctrl+C** to exit and print each symbol's final state.

---

## User Configuration

Edit the configuration block in `intraday/main.py`:

```python
TWS_PORT    = 7497       # paper=7497 / live=7496
MIN_SAMPLES = 5          # min ticks per window before settlement (fallback for low-frequency sessions)

SYMBOLS = [
    SymbolSpec(GC_CONFIG, last_trade_date="202606"),
    SymbolSpec(ES_CONFIG, last_trade_date="202606"),
    SymbolSpec(NQ_CONFIG, last_trade_date="202606"),
]
```

- Leave `last_trade_date` as `""` to auto-select the nearest unexpired front-month contract.
- Add or remove entries in the `SYMBOLS` list freely; the limit is the number of TWS API clientIds (default `base=10`, auto-incremented).

### Supported Instruments

| Code | Name | Exchange | Contract Multiplier |
|------|------|----------|---------------------|
| GC  | Gold | COMEX | 100 oz/contract |
| ES  | E-mini S&P 500 | CME | $50/point |
| NQ  | E-mini Nasdaq 100 | CME | $20/point |

> To add a new instrument, add a `ProductConfig` instance in `config/products.py` and reference it in the `SYMBOLS` list.

---

## Project Structure

```
project/
└── intraday/
    ├── main.py                      # Entry point: user config (TWS_PORT / SYMBOLS / DB_PATH / SNAPSHOT_*)
    ├── query.py                     # Standalone query script (interactive menu + CLI args)
    ├── config/
    │   ├── products.py              # Static instrument parameters (tick_size, multiplier, signal thresholds…)
    │   └── sessions.py              # Session time boundaries (four COMEX segments, hhmm precision)
    ├── core/
    │   ├── types.py                 # Data structures: Tick / WindowResult / PhysicsStatsResult
    │   ├── signals.py               # SignalEvent / SignalType / Severity enums
    │   ├── price_distribution.py    # PriceDistributionTracker — CLT / ΔP equal-weight probability density (fallback)
    │   ├── decay_tracker.py         # DecayWeightedTracker — exponential decay weighted stats, dynamic coverage window
    │   ├── liquidity_engine.py      # LiquidityEngine — CME liquidity window calculations
    │   ├── physics_stats.py         # EconophysicsStats — primary decay tracker + fallback equal-weight tracker
    │   ├── persistence.py           # Persistence — DuckDB batch writes, Parquet export
    │   └── snapshot_exporter.py     # SnapshotExporter — DuckDB COPY TO Parquet high-frequency snapshots
    ├── analytics/
    │   ├── signal_engine.py         # SignalEngine — multi-dimensional signal evaluation & broadcast after each window
    │   └── session_adapter.py       # SessionAwareAdapter — session-driven dynamic parameter adjustment
    ├── app/
    │   ├── main_engine.py           # MainQuantEngine — single-symbol scheduling core (session/liquidity/physics/signal/persistence)
    │   └── multi_engine.py          # MultiEngine — multi-symbol manager, parallel connections, shared Bridge + Persistence
    ├── data/
    │   └── ibkr_feed.py             # IBKRTickFeed — tickByTick Last async subscription
    ├── display/
    │   ├── bridge.py                # DisplayBridge — observer pattern, decouples engine from display layer
    │   └── terminal_rich.py         # RichTerminalDisplay — Rich TUI full-screen multi-symbol layout
    └── dashboard/
        └── app.py                   # Streamlit live dashboard — reads Parquet snapshots, Plotly charts
```

---

## Data Flow

```
TWS / IB Gateway
  └─ ib_insync (tickByTick Last)  ×N symbols (each on independent clientId thread)
      └─ IBKRTickFeed._dispatch(price, volume, ts, side)
          └─ MainQuantEngine.on_tick_received()
              ├─ LiquidityEngine  →  WindowResult (settled each window)
              │   ├─ EconophysicsStats.update(vwap, ts, volume)
              │   │   ├─ DecayWeightedTracker  →  k = k_base×(1+λ×r_liq)  dynamic adjustment
              │   │   │   └─ w_i = exp(-k*(t_now-t_i))  weighted moments → DecayStats
              │   │   └─ PriceDistributionTracker  →  equal-weight fallback → PhysicsStatsResult
              │   ├─ Persistence.write_window()  ┐
              │   ├─ Persistence.write_physics() ┘  buffered batch → DuckDB
              │   ├─ SignalEngine.evaluate()  →  List[SignalEvent] (signal broadcast)
              │   └─ DisplayBridge.emit(WindowResult)
              │       └─ RichTerminalDisplay.on_window()  →  TUI refresh
              └─ SessionAwareAdapter  →  dynamic adjustment of window_sec / min_samples / history_size
```

---

## Dynamic Decay Coverage

In ultra-short-term scenarios, equal-weight statistics assign the same weight to old and new data, causing signal lag. `DecayWeightedTracker` introduces exponential decay weights and dynamically adjusts the decay speed based on current liquidity.

### Weight Formula

$$w_i = e^{-k \cdot (t_{now} - t_i)}$$

### Dynamic Decay Coefficient (Liquidity Gradient)

$$k_{eff} = k_{base} \times (1 + \lambda \times r_{liq}), \quad r_{liq} = \frac{V_{window}}{V_{avg20}}$$

| Liquidity State | $r_{liq}$ | $k_{eff}$ (default params) | Effective Half-life | Coverage (99%) |
|-----------------|----------|--------------------------|---------------------|----------------|
| Low liquidity   | 0.3      | ≈ 0.00162                | ≈ 428s              | ≈ 2840s        |
| Normal          | 1.0      | ≈ 0.00693                | ≈ 100s              | ≈ 665s         |
| High liquidity  | 3.0      | ≈ 0.02080 (clipped)      | ≈ 33s               | ≈ 222s         |

> Default params: `k_base=0.00231` (half-life 300s), `λ=2.0`, `k_min=0.00050`, `k_max=0.05`

### Parameter Tuning

Set per-instrument in `config/products.py` or `main.py`:

```python
from intraday.core.decay_tracker import DecayConfig

# ES / NQ — aggressive ultra-short-term mode
es_decay = DecayConfig(k_base=0.00462, lam=3.0, k_min=0.001, k_max=0.08)
# GC Gold — conservative mode
gc_decay = DecayConfig(k_base=0.00115, lam=1.0, k_min=0.0002, k_max=0.02)
```

Pass via `MainQuantEngine(decay_config=es_decay)` (supported after `MultiEngine` extension).

### TUI Display

The φ(ΔP) panel adds a decay metadata row:

| Field | Meaning |
|-------|---------|
| Decay k | Current effective decay coefficient and corresponding half-life |
| Eff. Coverage | Time range (seconds) capturing 99% of the weight |
| Liq. Ratio | Current window volume / average of last 20 windows |
| Eff. Samples | $\sum w_i$ (weighted equivalent sample count) |

---

## Persistence (DuckDB)

After each window settlement, data is automatically written to a local DuckDB database shared by all symbols.

### Database Schema

**`window_results`** — core market data

| Field | Type | Description |
|-------|------|-------------|
| ts / dt | DOUBLE / TIMESTAMPTZ | Window end timestamp |
| symbol / session | VARCHAR | Symbol / session |
| vwap / high_price / low_price | DOUBLE | Price levels |
| total_volume / tick_count | BIGINT / INT | Volume / tick count |
| price_levels / price_range_abs | INT / DOUBLE | Price dispersion |
| impact_bps / impact_dollar | DOUBLE | Market impact cost |
| buy_volume / sell_volume / delta / delta_ratio | — | Order flow |

**`physics_stats`** — decay statistics snapshot

| Field | Type | Description |
|-------|------|-------------|
| delta_p / mean / std / skewness / kurtosis | DOUBLE | ΔP statistical moments |
| ci_lo / ci_hi | DOUBLE | 95% CLT confidence interval |
| k_effective / half_life_sec / coverage_sec | DOUBLE | Decay metadata |
| liquidity_ratio / eff_n | DOUBLE | Liquidity ratio / effective sample count |

### Persistence Configuration (`main.py`)

```python
DB_PATH     = None   # None = ~/results/intraday.duckdb
PARQUET_DIR = None   # None = ~/results/parquet/
BATCH_SIZE  = 10     # Batch write every 10 records (also flushes immediately per record)

# Parquet snapshot config (for Streamlit dashboard)
SNAPSHOT_DIR      = None   # None = ~/results/snapshots/
SNAPSHOT_INTERVAL = 3.0    # Export interval (seconds)
SNAPSHOT_TAIL     = 500    # Latest N rows per export
ENABLE_SNAPSHOT   = True   # False = disable snapshots globally
```

### Exporting Parquet

```python
# In-process
me.export_parquet()             # export today
me.export_parquet("20260219")   # export specific date
```

```bash
# CLI
python -m intraday.query --export 20260219
```

---

## Streamlit Live Dashboard

The main engine exports the latest data lock-free to two Parquet files every `SNAPSHOT_INTERVAL` seconds via DuckDB `COPY ... TO` (< 1 ms):

```
~/results/snapshots/
    snapshot_wr.parquet   ← window_results latest N rows
    snapshot_phy.parquet  ← physics_stats  latest N rows
```

Streamlit reads them directly with `duckdb.query("SELECT * FROM read_parquet(...)")` and auto-refreshes every 3 seconds via `st.rerun()`.

### Timezone

All dashboard timestamps automatically use the **server's system timezone** — no configuration required:

```python
# dashboard/app.py (takes effect automatically, no manual edit needed)
LOCAL_TZ = datetime.datetime.now().astimezone().tzinfo
```

| Deployment | Behavior |
|------------|----------|
| Server timezone = `America/Chicago` (CME location) | Displays CST / CDT |
| Server timezone = `Asia/Shanghai` | Displays CST +8 |
| Any other timezone | Follows system setting automatically |

To force a specific timezone, set `LOCAL_TZ` to `zoneinfo.ZoneInfo("America/Chicago")` or equivalent.

### Starting the Dashboard

```bash
# Terminal 1: main engine
python -m intraday.main

# Terminal 2: dashboard
streamlit run intraday/dashboard/app.py

# Remote server (bind to public port)
streamlit run intraday/dashboard/app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true

# SSH tunnel for remote access
ssh -L 8501:localhost:8501 user@your-server
# Open http://localhost:8501 in local browser
```

### Dashboard Layout

| Area | Content |
|------|---------|
| **Sidebar** | Snapshot directory / refresh interval / row limit / armor threshold / kurtosis baseline / snapshot status (with last-updated time) |
| **KPI Row** | Per symbol: VWAP / Impact bps / Delta Ratio / Vol×Levels |
| **Tab: Raw Data** | Interactive `window_results` + `physics_stats` tables, each with a one-click CSV download button |
| **Tab: Analysis** | Per-symbol sub-tabs with three analysis modules (Plotly charts) |

#### Three Analysis Modules

**🏰 Module 1 — Price Trajectory vs. Armor Thickness (VWAP & Price Levels)**

Plotly dual-axis chart: left axis VWAP line + High/Low band; right axis Price Levels bar chart with conditional coloring:
- 🟢 D ≤ 3: strong armor, favorable for mean reversion
- 🟡 D 4–9: normal range
- 🔴 D ≥ 10: liquidity vacuum, immediate caution

Threshold is adjustable via the sidebar slider in real time.

**☢️ Module 2 — Tail Risk Radar (Kurtosis & Skewness)**

- Kurtosis trend area line (purple) + skewness dashed line + k_effective reference line
- Orange dashed line marks the Gaussian baseline (default 3.0; excess kurtosis shows 0.0)
- When kurtosis "pierces" the baseline it creates a strong visual signal, serving as a circuit-breaker for mean-reversion strategies

**🔬 Module 3 — Engine Zoom (Absorption Rate & Adaptive Memory)**

- Left: `half_life_sec` area chart — see directly how engine memory shortens as market accelerates
- Right: VWAP vs. absorption rate V/D (volume / price_levels) dual-axis divergence chart
  - VWAP making new highs while V/D declines → shallow buy-side, potential topping signal

### Data Pipeline

```
MainQuantEngine (each window settlement)
  └─ Persistence.write_window() / write_physics()
      └─ SnapshotExporter.maybe_export(conn)   ← inside write lock, reuses same connection
          ├─ COPY window_results TO snapshot_wr.parquet  (ZSTD)
          └─ COPY physics_stats  TO snapshot_phy.parquet (ZSTD)
                                                    │
                                         Streamlit st.rerun() every N s
                                                    │
                              duckdb.query("SELECT * FROM read_parquet(...)")
```

---

## Data Queries

### Interactive Menu

```bash
cd /path/to/project
conda run -p /path/to/envs/quant python -m intraday.query
```

Menu options: today's summary / latest records / fat-tail risk / hourly aggregation / decay trends / liquidity vacuum / custom SQL.

> **Queries work while the main engine is running**: the query script detects write-lock conflicts and automatically copies a database snapshot, without affecting the main engine's writes.

### CLI Arguments

All examples below assume the correct conda environment; prefix with `conda run -p /path/to/envs/quant` as needed (abbreviated below):

```bash
# Latest 30 window records (ES)
python -m intraday.query --symbol ES --tail 30

# Latest 20 decay statistics (all symbols)
python -m intraday.query --physics 20

# Today's per-symbol summary
python -m intraday.query --summary

# Hourly aggregation (NQ, today)
python -m intraday.query --symbol NQ --hourly

# Decay coefficient k trend (last 3 hours, GC)
python -m intraday.query --symbol GC --decay 3

# Fat-tail risk periods
python -m intraday.query --risk

# Liquidity vacuum periods
python -m intraday.query --vacuum --symbol ES

# Row count statistics
python -m intraday.query --count

# Custom SQL
python -m intraday.query --sql "SELECT symbol, count(*) FROM window_results GROUP BY 1"

# Export today's Parquet
python -m intraday.query --export 20260219
```

### Direct DuckDB CLI

```bash
duckdb ~/results/intraday.duckdb

-- Today's volume by symbol
SELECT symbol, sum(total_volume) AS vol
FROM window_results
WHERE strftime(dt, '%Y%m%d') = '20260219'
GROUP BY 1;

-- Decay k trend (ES, last 1 hour)
SELECT strftime(dt,'%H:%M:%S'), k_effective, coverage_sec, liquidity_ratio
FROM physics_stats
WHERE symbol='ES' AND dt >= now() - INTERVAL 1 HOUR
ORDER BY ts DESC;
```

---

## Session Parameters

`SessionAwareAdapter` checks the current session on every tick and automatically updates three parameters on session change:

| Session | window_sec | min_samples | history_size | Coverage |
|---------|-----------|-------------|-------------|----------|
| Maintenance | 5s | 1 | 60 | 300s |
| Asian Session | 5s | 2 | 60 | 300s |
| Euro-US Overlap | 5s | 10 | 60 | 300s |
| US Afternoon | 5s | 5 | 60 | 300s |

---

## Metrics Reference

### CME Liquidity Panel

| Metric | Description |
|--------|-------------|
| Price Levels | Number of distinct price ticks observed within the window |
| Impact bps | Estimated market impact cost (basis points) |
| Delta Ratio | (Buy vol − Sell vol) / (Buy vol + Sell vol); positive = buy-side dominant |
| VWAP | Volume-weighted average price |

### φ(ΔP) Probability Density Panel

| Metric | Description |
|--------|-------------|
| μ (CLT) | Decay-weighted mean; near 0 indicates random walk |
| σ (CLT) | Decay-weighted standard deviation; measures price diffusion speed |
| 95% CI | 95% confidence interval for price change in the next aggregation window |
| Excess Kurtosis | Gaussian baseline = 0; >5 fat-tail risk, <1 near-ideal diffusion |
| Skewness | Positive = right-skewed (thicker upper tail), negative = left-skewed |
| Decay k | Current effective decay coefficient (with corresponding half-life) |
| Eff. Coverage | Current statistical coverage window, dynamically scaled by liquidity |
| Liq. Ratio | Current window volume relative to the last 20-window average |
| Eff. Samples | Decay-weighted equivalent sample count Σwᵢ |

### Signal Types

| Signal | Severity | Trigger Condition |
|--------|----------|-------------------|
| `Impact Spike` | WARN / ALERT | `impact_bps` exceeds instrument threshold (`warn_bps` / `alert_bps`) |
| `Fat-Tail Escalation` | WARN / ALERT | Excess kurtosis exceeds `kurt_warn` / `kurt_alert` |
| `Order Imbalance` | WARN | `|delta_ratio|` exceeds `delta_imbal_warn` (default 0.65) |
| `Volume Anomaly` | WARN | Current window volume > 20-window average × `volume_surge_x` (default 3×) |
| `Liquidity Exhaustion` | ALERT | Volume = 0 or `tick_count` = 0 |

---

## FAQ

**Connection timeout / contract not found**
- Confirm TWS is logged in and running
- Verify the port number in API settings matches `TWS_PORT`
- Leave `last_trade_date` empty to let the engine auto-select the contract and avoid expired months

**`RuntimeError: There is no current event loop`**
- Fixed: `ibkr_feed.py` automatically creates an event loop at thread entry, compatible with Python 3.10+

**TUI display garbled**
- Terminal must support UTF-8 and 256 colors; iTerm2 / macOS Terminal (Solarized theme) recommended

**Some symbols fail to connect**
- The engine prints `❌ Some symbols failed to connect` at startup; check whether the contract month for the affected symbol has expired
- Each symbol occupies an independent IBKR clientId (auto-incremented from `base_client_id=10`); ensure TWS allows sufficient concurrent connections
