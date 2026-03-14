# intraday/main.py
"""
入口：多标的 IBKR 实时数据 → MultiEngine → Rich TUI
运行: python -m intraday.main
"""
import sys
import os
import signal
import logging
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from intraday.config.products import GC_CONFIG, ES_CONFIG, NQ_CONFIG
from intraday.app.multi_engine import MultiEngine, SymbolSpec
from intraday.display.terminal_rich import RichTerminalDisplay

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# ── 用户配置 ─────────────────────────────────────────────────────
TWS_PORT    = 7497       # paper=7497 / live=7496
MIN_SAMPLES = 5          # 每窗口最少N笔即结算

# ── 持久化配置 ───────────────────────────────────────────────────
DB_PATH     = None       # None = ~/results/intraday.duckdb
PARQUET_DIR = None       # None = ~/results/parquet/
BATCH_SIZE  = 10         # 每累积 N 条批量写入一次（同时每条即时落盘）
# ── DuckDB Parquet 快照配置（供 Streamlit 实时看板读取）────────────
SNAPSHOT_DIR      = None   # None = ~/results/snapshots/
SNAPSHOT_INTERVAL = 3.0    # 导出间隔（秒）
SNAPSHOT_TAIL     = 500    # 每次导出最新 N 行
ENABLE_SNAPSHOT   = True   # 设为 False 可全局关闭快照
SYMBOLS = [
    SymbolSpec(GC_CONFIG, last_trade_date="202604"),
    SymbolSpec(ES_CONFIG, last_trade_date="202603"),
    SymbolSpec(NQ_CONFIG, last_trade_date="202603"),
]
# ─────────────────────────────────────────────────────────────────


def main() -> None:
    # ── 1. 多标的引擎 ──────────────────────────────────────────
    me = MultiEngine(
        port=TWS_PORT,
        min_samples=MIN_SAMPLES,
        base_client_id=10,
        db_path=DB_PATH,
        parquet_dir=PARQUET_DIR,
        batch_size=BATCH_SIZE,
        snapshot_dir=SNAPSHOT_DIR,
        snapshot_interval_sec=SNAPSHOT_INTERVAL,
        snapshot_tail_rows=SNAPSHOT_TAIL,
        enable_snapshot=ENABLE_SNAPSHOT,
    )
    for spec in SYMBOLS:
        me.add(spec)

    # ── 2. TUI ────────────────────────────────────────────────
    sym_list = me.symbols()

    # decay_stats_fns：每个品种绑定一个 lambda，调用时传入当前 now
    # 使用立即求值的默认参数捕获 eng，避免闭包陷阱
    decay_stats_fns = {
        s: (lambda eng: lambda: eng.physics_stats.get_decay_stats(time.time()))(
            me.get_slot(s).engine
        )
        for s in sym_list
    }

    display = RichTerminalDisplay(
        symbols=sym_list,
        session_fn=me.get_slot(sym_list[0]).engine.get_current_session,
        dist_fns={
            s: me.get_slot(s).engine.get_price_distribution
            for s in sym_list
        },
        decay_stats_fns=decay_stats_fns,
        history_size=10,
        refresh_per_second=20,
    )
    me.bridge.add_handler(display.on_window)

    # ── 3. 优雅退出 ──────────────────────────────────────────
    def on_exit(sig, frame):
        me.stop_all()   # 内部含 persistence.flush() + close()
        print("\n── 最终状态 ────────────────────────")
        for sym, info in me.status().items():
            print(f"  {sym:6s}  {info['local_symbol']:12s}  "
                  f"ticks={info['tick_count']}  errors={info['error_count']}")
        counts = me.db_stats()
        print(f"  📦 window_results={counts['window_results']}  "
              f"physics_stats={counts['physics_stats']}")
        sys.exit(0)

    signal.signal(signal.SIGINT,  on_exit)
    signal.signal(signal.SIGTERM, on_exit)

    # ── 4. 连接所有 Feed ──────────────────────────────────
    syms_str = " / ".join(sym_list)
    print(f"◈ 连接 TWS {TWS_PORT}  ({syms_str})...")

    if not me.connect_all(timeout=25.0):
        print("❌ 部分标的连接失败，请检查日志")
        me.stop_all()
        sys.exit(1)

    for sym, info in me.status().items():
        print(f"  ✅ {sym:6s} → {info['local_symbol']}")

    # ── 5. TUI 主线程阻塞（含时钟驱动强制结算）────────────────
    display.start(flush_fn=me.flush_all)


if __name__ == "__main__":
    main()
