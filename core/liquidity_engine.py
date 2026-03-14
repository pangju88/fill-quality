from __future__ import annotations

from typing import List, Optional
from datetime import datetime

from .types import Tick, WindowResult
from ..config.products import ProductConfig


class LiquidityEngine:
    """
    流式微观流动性引擎。
    基于 CME 报告：统计时间窗口内的价格离散度与成交特征。
    """
    def __init__(self, product_config: ProductConfig, initial_window_sec: int):
        self.config = product_config
        self.window_sec = initial_window_sec

        # 内部状态缓存桶 (用于累加当前窗口内的 Ticks)
        self.current_window_start: float = 0.0
        self.ticks_bucket: List[Tick] = []

    def set_window_size(self, new_window_sec: int) -> None:
        """支持在时段切换时(如亚盘切美盘)，动态调整聚合窗口大小。
        重置窗口起点，避免旧窗口长度段耠导致边界错位。
        """
        self.window_sec = new_window_sec
        # 保留已入桶的 tick，但从最后一笔 tick 的时间重新划分窗口起点
        if self.ticks_bucket:
            self.current_window_start = self.ticks_bucket[-1].timestamp
        else:
            self.current_window_start = 0.0

    def process_tick(self, tick: Tick) -> Optional[WindowResult]:
        """
        处理逐笔成交。如果该笔成交触发了窗口结算，则返回 WindowResult，否则返回 None。
        """
        if self.current_window_start == 0.0:
            self.current_window_start = tick.timestamp

        # 判断是否跨越了时间窗口边界 (比如已经过了 60 秒)
        if tick.timestamp >= self.current_window_start + self.window_sec:
            # 结算上一个窗口
            result = self._close_window(end_time=self.current_window_start + self.window_sec)
            
            # 开启新窗口 (处理跨度可能大于1个窗口的情况，这在实盘数据中断时很常见)
            while self.current_window_start + self.window_sec <= tick.timestamp:
                self.current_window_start += self.window_sec
                
            self.ticks_bucket = [tick] # 将当前 tick 放入新桶
            return result
        else:
            self.ticks_bucket.append(tick)
            return None

    def flush(self, now: float) -> Optional[WindowResult]:
        """
        时钟驱动强制结算：当前时刻已超过窗口截止时间，且桶内有数据时，
        主动关闭当前窗口。供 TUI 刷新循环调用，解决无 tick 导致窗口不结算的问题。
        """
        if self.current_window_start == 0.0 or not self.ticks_bucket:
            return None
        deadline = self.current_window_start + self.window_sec
        if now < deadline:
            return None
        result = self._close_window(end_time=deadline)
        while self.current_window_start + self.window_sec <= now:
            self.current_window_start += self.window_sec
        self.ticks_bucket = []
        return result

    def _close_window(self, end_time: float) -> Optional[WindowResult]:
        """执行 CME 离散度核心算法"""
        if not self.ticks_bucket:
            return None  # 空窗口不处理

        prices  = [t.price for t in self.ticks_bucket]
        volumes = [t.volume for t in self.ticks_bucket]

        # 1. 离散度核心: 计算唯一成交价格的层级数
        sorted_prices  = sorted(set(prices))
        price_levels   = len(sorted_prices)
        price_high     = sorted_prices[-1]
        price_low      = sorted_prices[0]
        price_range_abs   = round(price_high - price_low, 6)
        price_range_ticks = round(price_range_abs / self.config.tick_size) if self.config.tick_size > 0 else 0

        # 2. 冲击成本
        impact_bps    = (price_levels * self.config.tick_size) / self.config.ref_price * 10_000
        impact_dollar = price_levels * self.config.tick_size * self.config.contract_multiplier

        # 3. VWAP
        total_volume = sum(volumes)
        vwap = sum(p * v for p, v in zip(prices, volumes)) / total_volume

        # 4. 订单流失衡
        buy_vol  = sum(t.volume for t in self.ticks_bucket if t.side == 'buy')
        sell_vol = total_volume - buy_vol
        delta    = buy_vol - sell_vol

        # 5. 时间标签
        time_label = datetime.fromtimestamp(end_time).strftime("%H:%M:%S")

        return WindowResult(
            window_start=self.current_window_start,
            window_end=end_time,
            time_label=time_label,
            price_levels=price_levels,
            price_range_ticks=price_range_ticks,
            price_range_abs=price_range_abs,
            impact_bps=round(impact_bps, 4),
            impact_dollar=round(impact_dollar, 2),
            total_volume=total_volume,
            tick_count=len(self.ticks_bucket),
            vwap=round(vwap, 4),
            high_price=price_high,
            low_price=price_low,
            buy_volume=buy_vol,
            sell_volume=sell_vol,
            delta=delta,
            delta_ratio=round(delta / total_volume, 4) if total_volume > 0 else 0.0,
            unique_prices=sorted_prices,
        )