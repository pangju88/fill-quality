# intraday/app/multi_engine.py
"""
多标的引擎管理器
每个标的独立: IBKRTickFeed + MainQuantEngine + 信号引擎
共享: DisplayBridge → RichTerminalDisplay
"""
from __future__ import annotations
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from intraday.config.products import ProductConfig # type: ignore
from intraday.app.main_engine import MainQuantEngine # type: ignore
from intraday.display.bridge import DisplayBridge # type: ignore
from intraday.core.persistence import Persistence # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class SymbolSpec:
    """单个标的的订阅配置"""
    config: ProductConfig
    last_trade_date: str = ""   # 留空 = 自动选最近未到期主力月
    client_id: int = 0          # 0 = 由 MultiEngine 自动分配


class SymbolSlot:
    """单个标的的运行时状态容器"""

    def __init__(self, spec: SymbolSpec, client_id: int,
                 bridge: DisplayBridge, min_samples: int,
                 persistence: "Persistence" = None):
        from intraday.data.ibkr_feed import IBKRTickFeed # type: ignore

        self.symbol = spec.config.symbol
        self.spec   = spec

        self.engine = MainQuantEngine(
            product_config=spec.config,
            min_samples=min_samples,
            persistence=persistence,
        )
        self.engine.set_bridge(bridge)

        self.feed = IBKRTickFeed.from_preset(
            symbol=self.symbol,
            last_trade_date=spec.last_trade_date,
            client_id=client_id,
        )
        self.feed.subscribe(self.engine.on_tick_received)
        self._client_id = client_id

    def set_port(self, port: int) -> None:
        self.feed._cfg.port = port

    def stop(self) -> None:
        self.feed.stop()


class MultiEngine:
    """
    多标的引擎管理器

    用法:
        me = MultiEngine(port=7497, min_samples=5)
        me.add(SymbolSpec(GC_CONFIG,  last_trade_date="202604"))
        me.add(SymbolSpec(MGC_CONFIG, last_trade_date="202604"))
        me.add(SymbolSpec(MES_CONFIG, last_trade_date="202506"))

        bridge = me.bridge
        bridge.add_handler(display.on_window)

        if me.connect_all(timeout=20):
            me.start()        # 阻塞（无 TUI 时）
    """

    def __init__(self, port: int = 7497,
                 min_samples: int = 5,
                 base_client_id: int = 10,
                 db_path: str = None,
                 parquet_dir: str = None,
                 batch_size: int = 50,
                 snapshot_dir: str = None,
                 snapshot_interval_sec: float = 3.0,
                 snapshot_tail_rows: int = 500,
                 enable_snapshot: bool = True):
        self.port            = port
        self.min_samples     = min_samples
        self.bridge          = DisplayBridge()
        self._slots:         Dict[str, SymbolSlot] = {}
        self._next_cid       = base_client_id
        self._stop_event     = threading.Event()

        # 所有品种共享同一个 DuckDB 连接
        self._persistence = Persistence(
            db_path=db_path,
            batch_size=batch_size,
            parquet_dir=parquet_dir,
            snapshot_dir=snapshot_dir,
            snapshot_interval_sec=snapshot_interval_sec,
            snapshot_tail_rows=snapshot_tail_rows,
            enable_snapshot=enable_snapshot,
        )

    # ── 配置 ──────────────────────────────────────────────────────

    def add(self, spec: SymbolSpec) -> "MultiEngine":
        """添加标的，支持链式调用"""
        cid = spec.client_id if spec.client_id > 0 else self._next_cid
        self._next_cid += 1

        slot = SymbolSlot(
            spec=spec,
            client_id=cid,
            bridge=self.bridge,
            min_samples=self.min_samples,
            persistence=self._persistence,
        )
        slot.set_port(self.port)
        self._slots[spec.config.symbol] = slot
        logger.info("[MultiEngine] 注册 %s  clientId=%d", spec.config.symbol, cid)
        return self

    # ── 连接 ──────────────────────────────────────────────────────

    def connect_all(self, timeout: float = 25.0) -> bool:
        """
        并行启动所有 Feed，等待全部连接成功。
        返回 True=全部成功，False=至少一个失败。
        """
        for slot in self._slots.values():
            slot.feed.start_async()

        deadline = time.time() + timeout
        while time.time() < deadline:
            n_ok   = sum(1 for s in self._slots.values() if s.feed.is_connected)
            n_fail = sum(1 for s in self._slots.values() if s.feed.error_count > 0
                         and not s.feed.is_connected)
            if n_ok == len(self._slots):
                return True
            if n_fail > 0:
                for sym, s in self._slots.items():
                    if s.feed.error_count > 0 and not s.feed.is_connected:
                        logger.error("[MultiEngine] %s 连接失败", sym)
                return False
            time.sleep(0.4)

        # 超时：打印哪些超时
        for sym, s in self._slots.items():
            if not s.feed.is_connected:
                logger.error("[MultiEngine] %s 连接超时", sym)
        return False

    # ── 查询 ──────────────────────────────────────────────────────

    def symbols(self) -> List[str]:
        return list(self._slots.keys())

    def get_slot(self, symbol: str) -> Optional[SymbolSlot]:
        return self._slots.get(symbol)

    def status(self) -> Dict[str, dict]:
        return {
            sym: {
                "connected":    slot.feed.is_connected,
                "tick_count":   slot.feed.tick_count,
                "local_symbol": slot.feed.local_symbol,
                "error_count":  slot.feed.error_count,
            }
            for sym, slot in self._slots.items()
        }

    # ── 注册信号回调 ──────────────────────────────────────────────

    def on_signal(self, callback) -> None:
        """对所有标的注册同一个信号回调"""
        for slot in self._slots.values():
            slot.engine.on_signal(callback)

    def get_all_recent_signals(self, n: int = 30):
        """合并所有标的最近信号，按时间倒序"""
        all_events = []
        for slot in self._slots.values():
            all_events.extend(slot.engine.get_recent_signals(n))
        all_events.sort(key=lambda e: e.timestamp)
        return all_events[-n:]

    # ── 停止 ──────────────────────────────────────────────────────

    def flush_all(self, now: float = None) -> None:
        """对所有标的执行时钟驱动强制结算，供 TUI 刷新循环调用"""
        import time as _time
        t = now if now is not None else _time.time()
        for slot in self._slots.values():
            slot.engine.flush_window(t)

    def stop_all(self) -> None:
        self._stop_event.set()
        for slot in self._slots.values():
            slot.stop()
        self._persistence.close()   # flush 剩余缓冲并关闭数据库
        logger.info("[MultiEngine] 全部停止")

    def export_parquet(self, date_str: str = None) -> dict:
        """收盘后手动导出指定日期数据为 Parquet（默认今天）"""
        return self._persistence.export_parquet(date_str)

    def db_stats(self) -> Dict[str, int]:
        """返回数据库当前行数（调试用）"""
        return self._persistence.row_count()
