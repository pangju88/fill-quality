# intraday/analytics/session_adapter.py
"""
SessionAwareAdapter
===================
监听时段切换，动态调整:
  - window_sec    聚合窗口长度
  - min_samples   最小结算样本数 (大数定律保障)
  - history_size  φ(ΔP) 历史长度 (保持 ≥1h 覆盖)

覆盖时长目标: history_size × window_sec ≥ 3600s
CLT 要求:     min_samples ≥ 30 (高频) / ≥ 5 (低频稀疏)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SessionParams:
    """单个时段的动态参数"""
    session_name:  str
    window_sec:    float   # 聚合窗口 (秒)
    min_samples:   int     # 最小结算样本数
    history_size:  int     # φ(ΔP) 滚动历史长度
    description:   str = ""

    @property
    def coverage_minutes(self) -> float:
        """理论覆盖时长 (分钟)"""
        return self.history_size * self.window_sec / 60.0


# ── 各时段参数 ────────────────────────────────────────────────────
#
# | 时段              | window_sec | min_samples | history_size | 覆盖   |
# |-------------------|-----------|-------------|-------------|--------|
# | Maintenance       |     5s    |      1      |      60     |  5min  |
# | Asian_Session     |     5s    |      2      |      60     |  5min  |
# | Euro_US_Overlap   |     5s    |     10      |      60     |  5min  |
# | US_Afternoon      |     5s    |      5      |      60     |  5min  |
#
SESSION_PARAMS: dict[str, SessionParams] = {
    "Maintenance": SessionParams(
        session_name="Maintenance",
        window_sec=5.0,
        min_samples=1,
        history_size=60,        # 60×5s = 300s
        description="维护时段，极低流动性，5s 窗口",
    ),
    "Asian_Session": SessionParams(
        session_name="Asian_Session",
        window_sec=5.0,
        min_samples=2,          # 5s 内亚盘约 1-5 笔，门槛 2
        history_size=60,        # 60×5s = 300s
        description="亚盘，中低流动性，5s 窗口",
    ),
    "Euro_US_Overlap": SessionParams(
        session_name="Euro_US_Overlap",
        window_sec=5.0,
        min_samples=10,         # 5s 高峰期约 10-50 笔
        history_size=60,        # 60×5s = 300s
        description="欧美高峰，高频 5s 窗口",
    ),
    "US_Afternoon": SessionParams(
        session_name="US_Afternoon",
        window_sec=5.0,
        min_samples=5,
        history_size=60,        # 60×5s = 300s
        description="美盘下午，中频 5s 窗口",
    ),
}

_DEFAULT_PARAMS = SessionParams(
    session_name="Unknown",
    window_sec=5.0,
    min_samples=2,
    history_size=60,
)


class SessionAwareAdapter:
    """
    用法::

        adapter = SessionAwareAdapter()
        adapter.on_change(engine._on_session_params_change)

        # 每笔 tick 处理后调用:
        adapter.tick(current_session_name)
    """

    def __init__(self) -> None:
        self._current: Optional[str] = None
        self._callbacks: List[Callable[[SessionParams, SessionParams], None]] = []

    def on_change(
        self,
        callback: Callable[[SessionParams, SessionParams], None],
    ) -> None:
        """
        注册参数变更回调。
        签名: ``callback(old_params, new_params) -> None``
        """
        self._callbacks.append(callback)

    def tick(self, session_name: str) -> bool:
        """
        通知当前时段名称，检测是否切换。
        返回 True 表示发生了切换并已触发回调。
        """
        if session_name == self._current:
            return False

        old_name = self._current or "–"
        old_p = SESSION_PARAMS.get(old_name, _DEFAULT_PARAMS)
        new_p = SESSION_PARAMS.get(session_name, _DEFAULT_PARAMS)

        logger.info(
            "[SessionAdapter] %s → %s  window=%.0fs  "
            "min_samples=%d  history=%d  覆盖=%.0fmin",
            old_name, session_name,
            new_p.window_sec, new_p.min_samples,
            new_p.history_size, new_p.coverage_minutes,
        )

        self._current = session_name
        for cb in self._callbacks:
            try:
                cb(old_p, new_p)
            except Exception as exc:
                logger.error("[SessionAdapter] 回调异常: %s", exc)
        return True

    @property
    def current_params(self) -> SessionParams:
        return SESSION_PARAMS.get(self._current or "", _DEFAULT_PARAMS)

    @property
    def current_session(self) -> Optional[str]:
        return self._current
