# intraday/analytics/signal_engine.py
"""
信号引擎
=========
输入: WindowResult + DeltaPStats (每窗口结算后调用)
输出: List[SignalEvent]，同时通过回调广播
"""
from __future__ import annotations
import logging
from collections import deque
from typing import Callable, Dict, List, Optional

from intraday.core.signals import SignalEvent, SignalType, Severity # type: ignore
from intraday.core.types import WindowResult # type: ignore
from intraday.config.products import ProductConfig # type: ignore

logger = logging.getLogger(__name__)


class SignalEngine:
    """
    用法:
        se = SignalEngine()
        se.register(GC_CONFIG)
        se.on_signal(lambda e: print(e))

        # 每次窗口结算后调用:
        events = se.evaluate(window_result, dist_stats)
    """

    def __init__(self, history_size: int = 200):
        self._configs: Dict[str, ProductConfig] = {}
        self._callbacks: List[Callable[[SignalEvent], None]] = []
        self._vol_history:  Dict[str, deque] = {}  # symbol → 最近20窗口成交量
        self.history: deque = deque(maxlen=history_size)

    # ── 注册 ──────────────────────────────────────────────────────

    def register(self, config: ProductConfig) -> None:
        self._configs[config.symbol] = config
        self._vol_history[config.symbol] = deque(maxlen=20)

    def on_signal(self, callback: Callable[[SignalEvent], None]) -> None:
        self._callbacks.append(callback)

    # ── 主评估入口 ─────────────────────────────────────────────────

    def evaluate(self, window: WindowResult,
                 dist=None) -> List[SignalEvent]:
        """
        Parameters
        ----------
        window : WindowResult  (需有 symbol 字段)
        dist   : DeltaPStats | None
        """
        sym = getattr(window, "symbol", None)
        if not sym or sym not in self._configs:
            return []

        cfg = self._configs[sym]
        events: List[SignalEvent] = []

        events += self._check_impact(window, cfg)
        events += self._check_volume_surge(window, cfg)
        events += self._check_delta_imbal(window, cfg)
        events += self._check_low_liquidity(window, cfg)
        if dist:
            events += self._check_thick_tail(window, cfg, dist)

        # 更新成交量历史
        self._vol_history[sym].append(window.total_volume)

        # 广播 & 记录
        for e in events:
            self.history.append(e)
            for cb in self._callbacks:
                try:
                    cb(e)
                except Exception as ex:
                    logger.error("[SignalEngine] 回调异常: %s", ex)

        return events

    def recent(self, n: int = 20) -> List[SignalEvent]:
        """返回最近 n 条信号"""
        items = list(self.history)
        return items[-n:]

    # ── 各项信号检测 ──────────────────────────────────────────────

    def _check_impact(self, w: WindowResult,
                      cfg: ProductConfig) -> List[SignalEvent]:
        bps = w.impact_bps
        alert = getattr(cfg, "impact_alert_bps", 8.0)
        warn  = getattr(cfg, "impact_warn_bps",  3.0)
        if bps >= alert:
            return [SignalEvent(
                symbol=w.symbol, sig_type=SignalType.IMPACT_SPIKE,
                severity=Severity.ALERT, value=bps, threshold=alert,
                message=f"冲击 {bps:.2f}bps ≥ 告警线 {alert}bps  "
                        f"离散度={w.price_levels}层",
            )]
        if bps >= warn:
            return [SignalEvent(
                symbol=w.symbol, sig_type=SignalType.IMPACT_SPIKE,
                severity=Severity.WARN, value=bps, threshold=warn,
                message=f"冲击 {bps:.2f}bps ≥ 警告线 {warn}bps",
            )]
        return []

    def _check_thick_tail(self, w: WindowResult,
                          cfg: ProductConfig, dist) -> List[SignalEvent]:
        kurt  = getattr(dist, "kurt", 0.0)
        alert = getattr(cfg, "kurt_alert", 5.0)
        warn  = getattr(cfg, "kurt_warn",  2.0)
        if kurt >= alert:
            return [SignalEvent(
                symbol=w.symbol, sig_type=SignalType.THICK_TAIL,
                severity=Severity.ALERT, value=kurt, threshold=alert,
                message=f"超额峰度 {kurt:.2f} ≥ {alert}，极度厚尾 — 建议避免市价单",
            )]
        if kurt >= warn:
            return [SignalEvent(
                symbol=w.symbol, sig_type=SignalType.THICK_TAIL,
                severity=Severity.WARN, value=kurt, threshold=warn,
                message=f"超额峰度 {kurt:.2f} ≥ {warn}，厚尾加剧",
            )]
        return []

    def _check_delta_imbal(self, w: WindowResult,
                           cfg: ProductConfig) -> List[SignalEvent]:
        r     = w.delta_ratio
        thr   = getattr(cfg, "delta_imbal_warn", 0.65)
        lo    = 1.0 - thr
        if r >= thr:
            return [SignalEvent(
                symbol=w.symbol, sig_type=SignalType.DELTA_IMBAL,
                severity=Severity.WARN, value=r, threshold=thr,
                message=f"买卖比 {r:.0%} > {thr:.0%}，强势偏买  "
                        f"Delta={w.delta:+d}",
            )]
        if r <= lo:
            return [SignalEvent(
                symbol=w.symbol, sig_type=SignalType.DELTA_IMBAL,
                severity=Severity.WARN, value=r, threshold=lo,
                message=f"买卖比 {r:.0%} < {lo:.0%}，强势偏卖  "
                        f"Delta={w.delta:+d}",
            )]
        return []

    def _check_volume_surge(self, w: WindowResult,
                            cfg: ProductConfig) -> List[SignalEvent]:
        hist = self._vol_history.get(w.symbol, deque())
        if len(hist) < 5:
            return []
        avg = sum(hist) / len(hist)
        if avg <= 0:
            return []
        surge_x = getattr(cfg, "volume_surge_x", 3.0)
        ratio = w.total_volume / avg
        if ratio >= surge_x:
            return [SignalEvent(
                symbol=w.symbol, sig_type=SignalType.VOLUME_SURGE,
                severity=Severity.ALERT, value=ratio, threshold=surge_x,
                message=f"成交量 {w.total_volume} 手 = 均值×{ratio:.1f}",
            )]
        return []

    def _check_low_liquidity(self, w: WindowResult,
                              cfg: ProductConfig) -> List[SignalEvent]:
        if w.tick_count <= 2 and w.price_levels <= 1:
            return [SignalEvent(
                symbol=w.symbol, sig_type=SignalType.LOW_LIQUIDITY,
                severity=Severity.INFO, value=float(w.tick_count),
                threshold=3.0,
                message=f"窗口仅 {w.tick_count} 笔成交，流动性极低",
            )]
        return []
