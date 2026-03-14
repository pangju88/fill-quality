# intraday/core/signals.py
"""
信号数据结构
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum


class Severity(Enum):
    INFO  = "INFO"
    WARN  = "WARN"
    ALERT = "ALERT"


class SignalType(Enum):
    IMPACT_SPIKE  = "冲击突增"
    THICK_TAIL    = "厚尾加剧"
    DELTA_IMBAL   = "买卖失衡"
    VOLUME_SURGE  = "成交量异常"
    LOW_LIQUIDITY = "流动性枯竭"
    SPREAD_ARB    = "价差套利"


@dataclass
class SignalEvent:
    symbol:    str
    sig_type:  SignalType
    severity:  Severity
    value:     float        # 触发信号的指标实测值
    threshold: float        # 对应阈值
    message:   str
    timestamp: float = field(default_factory=time.time)

    @property
    def time_label(self) -> str:
        import datetime
        return datetime.datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")

    @property
    def icon(self) -> str:
        return {"INFO": "ℹ", "WARN": "⚠", "ALERT": "🔴"}[self.severity.value]
