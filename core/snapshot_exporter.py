# intraday/core/snapshot_exporter.py
"""
SnapshotExporter
================
复用 Persistence 的同一 DuckDB 连接，在写锁内执行
COPY ... TO ... (FORMAT PARQUET，COMPRESSION ZSTD)。
DuckDB 导出几百行 Parquet 通常 < 1 ms，对主引擎完全无感知。

用法（由 Persistence 内部调用）：
    exporter = SnapshotExporter(snapshot_dir="~/results/snapshots")
    # 每次 commit 后（仍在锁内）
    exporter.maybe_export(conn)
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_COPY_SQL = """
COPY (
    SELECT * FROM {table}
    ORDER BY ts DESC
    LIMIT {tail}
) TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD);
"""


class SnapshotExporter:
    """
    Parameters
    ----------
    snapshot_dir : Parquet 快照输出目录，默认 ~/results/snapshots/
    interval_sec : 最小导出间隔（秒），默认 3.0
    tail_rows    : 每次导出最新 N 行，默认 500
    """

    def __init__(
        self,
        snapshot_dir: str | Path | None = None,
        interval_sec: float = 3.0,
        tail_rows: int = 500,
    ) -> None:
        snap_dir = Path(snapshot_dir) if snapshot_dir else Path.home() / "results" / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)

        self._interval    = interval_sec
        self._tail        = tail_rows
        self._last_export = 0.0

        self.path_wr  = str(snap_dir / "snapshot_wr.parquet")
        self.path_phy = str(snap_dir / "snapshot_phy.parquet")

        logger.info(
            "SnapshotExporter 已初始化: dir=%s  interval=%.1fs  tail=%d",
            snap_dir, interval_sec, tail_rows,
        )
        print(f"[SnapshotExporter] 快照目录: {snap_dir}  间隔: {interval_sec}s")

    # ── 公开接口（在写锁内、commit 后调用）────────────────────────────────────

    def maybe_export(self, conn) -> bool:
        """
        若距上次导出已超过 interval_sec，则用传入的 DuckDB 连接执行导出。
        必须在写锁内、commit 之后调用，确保读到最新数据。

        Returns
        -------
        True  : 本次实际执行了导出
        False : 尚未到达导出间隔，跳过
        """
        if time.monotonic() - self._last_export < self._interval:
            return False
        self._do_export(conn)
        self._last_export = time.monotonic()
        return True

    # ── 内部实现 ──────────────────────────────────────────────────────────────

    def _do_export(self, conn) -> None:
        try:
            conn.execute(_COPY_SQL.format(
                table="window_results", tail=self._tail, path=self.path_wr))
            conn.execute(_COPY_SQL.format(
                table="physics_stats",  tail=self._tail, path=self.path_phy))
            logger.debug("快照已写出: %s / %s", self.path_wr, self.path_phy)
        except Exception as exc:
            logger.warning("快照导出失败（忽略）: %s", exc)
