# intraday/display/bridge.py
"""
DisplayBridge — 观察者模式，解耦引擎与显示层
引擎调用 bridge.emit()，Bridge 分发给所有注册的显示后端
"""
from __future__ import annotations

from typing import List, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.types import WindowResult


class DisplayBridge:
    """
    用法:
        bridge = DisplayBridge()
        bridge.add_handler(terminal.on_window)
        engine.set_bridge(bridge)
    """

    def __init__(self) -> None:
        self._handlers: List[Callable] = []

    def add_handler(self, fn: Callable) -> None:
        self._handlers.append(fn)

    def emit(self, result: "WindowResult") -> None:
        for fn in self._handlers:
            try:
                fn(result)
            except Exception as e:
                print(f"[Bridge] 显示异常: {e}")
