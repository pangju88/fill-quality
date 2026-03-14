import time as _time
from collections import deque
from typing import Optional, List

from .types import PhysicsStatsResult
from .price_distribution import PriceDistributionTracker
from .decay_tracker import DecayWeightedTracker, DecayConfig, DecayStats


class EconophysicsStats:
    """
    价格变动 ΔP 的概率密度与尾部风险监控器（指数衰减加权版）
    =========================================================
    - 主统计：DecayWeightedTracker — w_i = exp(-k*(t_now-t_i))
      k 随流动性动态调整（高流动性快衰减，低流动性慢衰减）
    - 备统计：PriceDistributionTracker — 等权，兼容旧接口
    - 峰度定义为超额峰度 (excess kurtosis = raw - 3)，正态分布 = 0
    """

    def __init__(self, history_size: int = 500, clt_n_agg: int = 30,
                 min_samples: int = 30, n_bins: int = 60,
                 decay_config: Optional[DecayConfig] = None):
        """
        Parameters
        ----------
        history_size  : 等权追踪器滚动历史数（兜底用）
        clt_n_agg     : CLT 聚合批次大小
        min_samples   : 开始输出统计所需的最少等效样本数
        n_bins        : 概率密度直方图 bin 数量
        decay_config  : 衰减参数，None 则使用默认 DecayConfig()
        """
        self.history_size = history_size
        self.min_samples = min_samples

        self.vwap_history: deque = deque(maxlen=history_size)

        # 主：指数衰减加权追踪器
        self._decay_tracker = DecayWeightedTracker(
            config=decay_config or DecayConfig(
                n_agg=clt_n_agg,
                n_bins=n_bins,
            )
        )

        # 备：等权追踪器（兼容 get_price_distribution / next_price_range）
        self._tracker = PriceDistributionTracker(
            tick_size=1.0,
            n_agg=clt_n_agg,
            max_samples=history_size,
            n_bins=n_bins,
        )

    def update(self, window_end: float, current_vwap: float,
               volume: int = 0) -> Optional[PhysicsStatsResult]:
        """
        每次 LiquidityEngine 吐出一个窗口就调用。

        Parameters
        ----------
        window_end   : 窗口结束时间戳
        current_vwap : 本窗口 VWAP
        volume       : 本窗口总成交量（驱动流动性动态衰减系数）
        """
        self.vwap_history.append(current_vwap)
        if len(self.vwap_history) < 2:
            return None

        # 同步推入两个追踪器
        dp_decay = self._decay_tracker.update(current_vwap, window_end, volume)
        dp_equal = self._tracker.update(current_vwap)

        if dp_decay is None:
            return None

        # 优先使用衰减加权统计
        decay_stats: Optional[DecayStats] = self._decay_tracker.get_stats(window_end)
        equal_stats = self._tracker.get_stats()

        eff_n = decay_stats.eff_n if decay_stats else 0.0
        if decay_stats is None or eff_n < self.min_samples:
            return None

        mean      = decay_stats.mean
        std       = decay_stats.std
        skew      = decay_stats.skew
        kurt      = decay_stats.kurt
        clt_sigma = decay_stats.clt_sigma
        ci_lo     = decay_stats.ci_lo
        ci_hi     = decay_stats.ci_hi
        pdf_x     = decay_stats.hist_bins
        pdf_y     = decay_stats.hist_density
        pdf_normal= decay_stats.normal_density

        # 若衰减追踪器直方图为空，回退到等权直方图
        if not pdf_y and equal_stats:
            pdf_x      = equal_stats.hist_bins
            pdf_y      = equal_stats.hist_density
            pdf_normal = equal_stats.normal_density

        if not pdf_y:
            pdf_x      = [round(mean, 6)]
            pdf_y      = [1.0]
            pdf_normal = [1.0]

        return PhysicsStatsResult(
            window_end=window_end,
            current_price=current_vwap,
            delta_p=round(dp_decay, 6),
            empirical_mean=mean,
            empirical_std=std,
            skewness=round(skew, 4),
            kurtosis=round(kurt, 4),
            clt_sigma=round(clt_sigma, 6),
            ci_lo=round(ci_lo, 6),
            ci_hi=round(ci_hi, 6),
            pdf_x=pdf_x,
            pdf_y=pdf_y,
            pdf_normal=pdf_normal,
        )

    # ── 辅助查询 ──────────────────────────────────────────────────────────────

    def prob_exceed(self, threshold: float) -> float:
        """P(|ΔP| > threshold) — 衰减加权正态近似"""
        return self._decay_tracker.prob_exceed(threshold, _time.time())

    def prob_up(self) -> float:
        """P(ΔP > 0) — 衰减加权方向计数"""
        return self._decay_tracker.prob_up(_time.time())

    def next_price_range(self, confidence: float = 0.95):
        """基于 CLT 估计下一聚合窗口的价格期望区间 → (下限, 上限)"""
        return self._tracker.next_price_range(confidence)

    def get_clt_means(self) -> List[float]:
        """返回 CLT 批均值序列（衰减加权版）"""
        return self._decay_tracker.get_clt_means()

    def get_decay_stats(self, now: Optional[float] = None) -> Optional[DecayStats]:
        """直接获取衰减统计快照（含半衰期、覆盖秒数、流动性倍数等调试字段）"""
        return self._decay_tracker.get_stats(now if now is not None else _time.time())

    def reset(self) -> None:
        self.vwap_history.clear()
        self._decay_tracker.reset()
        self._tracker.reset()