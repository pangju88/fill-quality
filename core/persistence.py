# intraday/core/persistence.py
"""
持久化模块（DuckDB）
====================
两张表：
  window_results  — 每窗口基础行情 (WindowResult)
  physics_stats   — 每窗口统计快照 (PhysicsStatsResult + DecayStats)

写入策略：
  - 批量缓冲（batch_size 条）后触发一次 executemany，降低 I/O
  - 线程安全（lock 保护缓冲区）
  - 程序退出时调用 flush() 写入剩余数据
  - 可选：export_parquet() 将指定日期数据导出为 ZSTD Parquet
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import duckdb

from .types import WindowResult, PhysicsStatsResult
from .decay_tracker import DecayStats
from .snapshot_exporter import SnapshotExporter

logger = logging.getLogger(__name__)


# ── DDL ────────────────────────────────────────────────────────────────────────

_DDL_WINDOW = """
CREATE TABLE IF NOT EXISTS window_results (
    ts               DOUBLE      NOT NULL,
    dt               TIMESTAMPTZ,
    symbol           VARCHAR,
    session          VARCHAR,
    vwap             DOUBLE,
    high_price       DOUBLE,
    low_price        DOUBLE,
    total_volume     BIGINT,
    tick_count       INTEGER,
    price_levels     INTEGER,
    price_range_abs  DOUBLE,
    impact_bps       DOUBLE,
    impact_dollar    DOUBLE,
    buy_volume       BIGINT,
    sell_volume      BIGINT,
    delta            BIGINT,
    delta_ratio      DOUBLE
);
"""

_DDL_PHYSICS = """
CREATE TABLE IF NOT EXISTS physics_stats (
    ts               DOUBLE      NOT NULL,
    dt               TIMESTAMPTZ,
    symbol           VARCHAR,
    delta_p          DOUBLE,
    mean             DOUBLE,
    std              DOUBLE,
    skewness         DOUBLE,
    kurtosis         DOUBLE,
    ci_lo            DOUBLE,
    ci_hi            DOUBLE,
    k_effective      DOUBLE,
    half_life_sec    DOUBLE,
    coverage_sec     DOUBLE,
    liquidity_ratio  DOUBLE,
    eff_n            DOUBLE
);
"""

_DDL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_wr_sym_ts  ON window_results (symbol, ts);
CREATE INDEX IF NOT EXISTS idx_phy_sym_ts ON physics_stats  (symbol, ts);
"""

_INSERT_WINDOW = """
INSERT INTO window_results VALUES (
    $ts, $dt, $symbol, $session,
    $vwap, $high_price, $low_price,
    $total_volume, $tick_count, $price_levels, $price_range_abs,
    $impact_bps, $impact_dollar,
    $buy_volume, $sell_volume, $delta, $delta_ratio
)
"""

_INSERT_PHYSICS = """
INSERT INTO physics_stats VALUES (
    $ts, $dt, $symbol,
    $delta_p, $mean, $std, $skewness, $kurtosis,
    $ci_lo, $ci_hi,
    $k_effective, $half_life_sec, $coverage_sec, $liquidity_ratio, $eff_n
)
"""


# ── 持久化器 ──────────────────────────────────────────────────────────────────

class Persistence:
    """
    线程安全批量写入器

    Parameters
    ----------
    db_path    : DuckDB 文件路径，默认 ~/results/intraday.duckdb
    batch_size : 累积到此条数后批量 INSERT（默认 50）
    parquet_dir: Parquet 导出目录，默认 ~/results/parquet/
    """

    def __init__(
        self,
        db_path: str = None,
        batch_size: int = 10,
        parquet_dir: str = None,
        snapshot_dir: str = None,
        snapshot_interval_sec: float = 3.0,
        snapshot_tail_rows: int = 500,
        enable_snapshot: bool = True,
    ) -> None:
        default_dir = Path.home() / "results"
        default_dir.mkdir(parents=True, exist_ok=True)

        self._db_path     = db_path or str(default_dir / "intraday.duckdb")
        self._parquet_dir = Path(parquet_dir) if parquet_dir else default_dir / "parquet"
        self._batch_size  = batch_size
        self._lock        = threading.Lock()

        self._buf_window:  List[dict] = []
        self._buf_physics: List[dict] = []

        self._conn = duckdb.connect(self._db_path)
        self._conn.execute(_DDL_WINDOW)
        self._conn.execute(_DDL_PHYSICS)
        self._conn.execute(_DDL_INDEXES)
        self._conn.commit()

        # ── 快照导出器 ────────────────────────────────────────────────────────
        self._snapshot: SnapshotExporter | None = (
            SnapshotExporter(
                snapshot_dir=snapshot_dir,
                interval_sec=snapshot_interval_sec,
                tail_rows=snapshot_tail_rows,
            )
            if enable_snapshot else None
        )

        logger.info("Persistence 初始化: %s", self._db_path)
        print(f"[Persistence] 数据库: {self._db_path}")

    # ── 写入接口 ──────────────────────────────────────────────────────────────

    def write_window(
        self,
        result: WindowResult,
        decay: Optional[DecayStats] = None,
    ) -> None:
        """写入一条 WindowResult，批量缓冲，线程安全。"""
        ts = result.window_end
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)

        row = {
            "ts":              ts,
            "dt":              dt,
            "symbol":          getattr(result, "symbol",         ""),
            "session":         getattr(result, "session",        ""),
            "vwap":            result.vwap,
            "high_price":      result.high_price,
            "low_price":       result.low_price,
            "total_volume":    result.total_volume,
            "tick_count":      result.tick_count,
            "price_levels":    result.price_levels,
            "price_range_abs": result.price_range_abs,
            "impact_bps":      result.impact_bps,
            "impact_dollar":   result.impact_dollar,
            "buy_volume":      result.buy_volume,
            "sell_volume":     result.sell_volume,
            "delta":           result.delta,
            "delta_ratio":     result.delta_ratio,
        }

        with self._lock:
            self._buf_window.append(row)
            if len(self._buf_window) >= self._batch_size:
                self._flush_window()
            else:
                # 不足 batch 时也逐条写入，确保即时落盘
                try:
                    self._conn.execute(_INSERT_WINDOW, row)
                    self._conn.commit()
                    self._buf_window.clear()
                except Exception as e:
                    logger.error("window_results 即时写入失败: %s", e)
            # 每隔 interval_sec 触发快照（在锁内，复用同一连接）
            if self._snapshot:
                self._snapshot.maybe_export(self._conn)

    def write_physics(
        self,
        result: PhysicsStatsResult,
        decay: Optional[DecayStats] = None,
        symbol: str = "",
    ) -> None:
        """写入一条 PhysicsStatsResult（附带 DecayStats 扩展字段）。"""
        ts = result.window_end
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)

        row = {
            "ts":             ts,
            "dt":             dt,
            "symbol":         symbol,
            "delta_p":        result.delta_p,
            "mean":           result.empirical_mean,
            "std":            result.empirical_std,
            "skewness":       result.skewness,
            "kurtosis":       result.kurtosis,
            "ci_lo":          result.ci_lo,
            "ci_hi":          result.ci_hi,
            "k_effective":    decay.k_effective     if decay else None,
            "half_life_sec":  decay.half_life_sec   if decay else None,
            "coverage_sec":   decay.coverage_sec    if decay else None,
            "liquidity_ratio":decay.liquidity_ratio if decay else None,
            "eff_n":          decay.eff_n           if decay else None,
        }

        with self._lock:
            self._buf_physics.append(row)
            if len(self._buf_physics) >= self._batch_size:
                self._flush_physics()
            else:
                # 不足 batch 时也逐条写入，确保即时落盘
                try:
                    self._conn.execute(_INSERT_PHYSICS, row)
                    self._conn.commit()
                    self._buf_physics.clear()
                except Exception as e:
                    logger.error("physics_stats 即时写入失败: %s", e)
            # 每隔 interval_sec 触发快照（在锁内，复用同一连接）
            if self._snapshot:
                self._snapshot.maybe_export(self._conn)

    # ── flush & close ─────────────────────────────────────────────────────────

    def flush(self) -> None:
        """强制写入所有缓冲（程序退出时调用）。"""
        with self._lock:
            self._flush_window()
            self._flush_physics()

    def close(self) -> None:
        self.flush()
        self._conn.close()
        logger.info("Persistence 已关闭: %s", self._db_path)

    # ── Parquet 导出 ──────────────────────────────────────────────────────────

    def export_parquet(self, date_str: str = None) -> Dict[str, str]:
        """
        将指定日期（默认今天）的数据导出为 ZSTD Parquet。

        Returns
        -------
        {"window_results": path, "physics_stats": path}
        """
        self.flush()
        date_str = date_str or datetime.now().strftime("%Y%m%d")
        self._parquet_dir.mkdir(parents=True, exist_ok=True)

        paths: Dict[str, str] = {}
        for table, prefix in (("window_results", "wr"), ("physics_stats", "phy")):
            out = str(self._parquet_dir / f"{prefix}_{date_str}.parquet")
            self._conn.execute(f"""
                COPY (
                    SELECT * FROM {table}
                    WHERE strftime(dt, '%Y%m%d') = '{date_str}'
                ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """)
            paths[table] = out
            logger.info("导出 %s → %s", table, out)
            print(f"[Persistence] 导出 {table} → {out}")

        return paths

    # ── 便捷查询 ──────────────────────────────────────────────────────────────

    def tail(self, table: str = "window_results", n: int = 20,
             symbol: str = None):
        """查看最新 n 条记录（返回 list of tuples）。"""
        self.flush()
        where = f"WHERE symbol = '{symbol}'" if symbol else ""
        return self._conn.execute(
            f"SELECT * FROM {table} {where} ORDER BY ts DESC LIMIT {n}"
        ).fetchall()

    def row_count(self) -> Dict[str, int]:
        """返回两张表的当前行数。"""
        self.flush()
        wr  = self._conn.execute("SELECT count(*) FROM window_results").fetchone()[0]
        phy = self._conn.execute("SELECT count(*) FROM physics_stats").fetchone()[0]
        return {"window_results": wr, "physics_stats": phy}

    # ── 内部批量写入 ──────────────────────────────────────────────────────────

    def _flush_window(self) -> None:
        if not self._buf_window:
            return
        try:
            self._conn.executemany(_INSERT_WINDOW, self._buf_window)
            self._conn.commit()
            self._buf_window.clear()
        except Exception as e:
            logger.error("window_results 写入失败: %s", e)

    def _flush_physics(self) -> None:
        if not self._buf_physics:
            return
        try:
            self._conn.executemany(_INSERT_PHYSICS, self._buf_physics)
            self._conn.commit()
            self._buf_physics.clear()
        except Exception as e:
            logger.error("physics_stats 写入失败: %s", e)
