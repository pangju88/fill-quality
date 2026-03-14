"""
价格变动 ΔP 的概率密度估计
===============================
纯 Python 实现，无 numpy / scipy 依赖。

基于中心极限定理 (CLT):
  当 n 足够大时，(1/n)Σ ΔP_i → N(μ, σ²/n)

核心量:
  ΔP_i    = P_i - P_{i-1}         每笔/每窗口价格变动
  φ(ΔP)   = 经验概率密度 (直方图归一化)
  CLT窗口: 将 n_agg 个 ΔP 聚合为一个样本，重复采样后得到均值分布
"""

import math
import logging
from collections import deque

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class DeltaPStats:
    """ΔP 统计快照 (懒计算，由 PriceDistributionTracker.get_stats() 返回)"""
    n: int                              # 样本数
    mean: float                         # E[ΔP]
    std: float                          # σ(ΔP)
    skew: float                         # 偏度 γ₁
    kurt: float                         # 超额峰度 γ₂ (正态 = 0；>0 = 厚尾)
    min_val: float
    max_val: float

    # CLT 聚合分布
    clt_mu: float = 0.0                 # ≈ mean
    clt_sigma: float = 0.0             # σ / √n_agg
    clt_n_agg: int = 30                # 聚合批次大小

    # 95% 置信区间
    ci_lo: float = 0.0                  # μ - 1.96 × clt_sigma
    ci_hi: float = 0.0                  # μ + 1.96 × clt_sigma

    # 概率密度直方图
    hist_bins: List[float] = field(default_factory=list)      # bin 中心 x 轴
    hist_density: List[float] = field(default_factory=list)   # 经验密度
    normal_density: List[float] = field(default_factory=list) # 拟合正态密度


# ── 核心追踪器 ────────────────────────────────────────────────────────────────

class PriceDistributionTracker:
    """
    实时追踪 ΔP 概率密度

    用法 (接收逐笔成交价格):
        tracker = PriceDistributionTracker(tick_size=0.10, n_agg=30)
        tracker.update(price)           # 每笔 tick 调用，返回 ΔP 或 None
        stats = tracker.get_stats()     # 获取当前 DeltaPStats 快照
        p = tracker.prob_exceed(0.50)   # P(|ΔP| > 0.50)

    用法 (接收 VWAP 序列，即 EconophysicsStats 的用途):
        tracker = PriceDistributionTracker(tick_size=1.0, n_agg=30)
        tracker.update(vwap)            # 每窗口 VWAP 调用
    """

    def __init__(self, tick_size: float, n_agg: int = 30,
                 max_samples: int = 2000, n_bins: int = 40):
        """
        Parameters
        ----------
        tick_size  : float  最小价格单位 (用于参考；传 1.0 表示不归一化)
        n_agg      : int    CLT 聚合批次大小 (每 n_agg 个 ΔP 计算一次批均值)
        max_samples: int    滚动保留的最大 ΔP 样本数
        n_bins     : int    直方图 bin 数量
        """
        self.tick_size = tick_size
        self.n_agg = n_agg
        self.max_samples = max_samples
        self.n_bins = n_bins

        self._prev_price: Optional[float] = None
        self._delta_p_buffer: deque = deque(maxlen=max_samples)
        self._clt_agg_buffer: List[float] = []      # 当前聚合批次
        self._clt_means: deque = deque(maxlen=500)  # CLT 批均值序列

        self._last_stats: Optional[DeltaPStats] = None
        self._dirty = True

    # ── 数据输入 ──────────────────────────────────────────────────────────────

    def update(self, price: float) -> Optional[float]:
        """
        推入新价格，返回 ΔP；第一笔返回 None。

        Parameters
        ----------
        price : float  最新成交价或 VWAP
        """
        if self._prev_price is None:
            self._prev_price = price
            return None

        dp = price - self._prev_price
        self._prev_price = price

        if dp != 0.0:
            self._delta_p_buffer.append(dp)
            self._clt_agg_buffer.append(dp)

            # CLT 聚合：每满 n_agg 个 ΔP 记录一次批均值
            if len(self._clt_agg_buffer) >= self.n_agg:
                batch_mean = sum(self._clt_agg_buffer) / len(self._clt_agg_buffer)
                self._clt_means.append(batch_mean)
                self._clt_agg_buffer.clear()

        self._dirty = True
        return dp

    # ── 统计输出 ──────────────────────────────────────────────────────────────

    def get_stats(self) -> Optional[DeltaPStats]:
        """计算并返回当前 ΔP 统计快照（懒计算，dirty flag 控制）"""
        if len(self._delta_p_buffer) < 5:
            return None

        if not self._dirty and self._last_stats is not None:
            return self._last_stats

        samples = list(self._delta_p_buffer)
        n = len(samples)

        # 基础统计
        mean = sum(samples) / n
        var  = sum((x - mean) ** 2 for x in samples) / n
        std  = math.sqrt(var) if var > 0 else 1e-9

        # 偏度 γ₁
        skew = (
            (sum((x - mean) ** 3 for x in samples) / n) / (std ** 3)
            if std > 1e-9 else 0.0
        )

        # 超额峰度 γ₂ = raw_kurtosis - 3
        kurt = (
            (sum((x - mean) ** 4 for x in samples) / n) / (std ** 4) - 3.0
            if std > 1e-9 else 0.0
        )

        # CLT 参数
        clt_sigma = std / math.sqrt(self.n_agg)
        ci_lo = mean - 1.96 * clt_sigma
        ci_hi = mean + 1.96 * clt_sigma

        # 直方图
        hist_bins, hist_density, normal_density = self._compute_histogram(
            samples, mean, std
        )

        stats = DeltaPStats(
            n=n,
            mean=round(mean, 6),
            std=round(std, 6),
            skew=round(skew, 4),
            kurt=round(kurt, 4),
            min_val=min(samples),
            max_val=max(samples),
            clt_mu=round(mean, 6),
            clt_sigma=round(clt_sigma, 6),
            clt_n_agg=self.n_agg,
            ci_lo=round(ci_lo, 6),
            ci_hi=round(ci_hi, 6),
            hist_bins=hist_bins,
            hist_density=hist_density,
            normal_density=normal_density,
        )

        self._last_stats = stats
        self._dirty = False
        return stats

    # ── 概率查询 ──────────────────────────────────────────────────────────────

    def prob_exceed(self, threshold: float) -> float:
        """
        P(|ΔP| > threshold) — 价格变动超过阈值的概率（正态近似）

        Parameters
        ----------
        threshold : float  价格变动绝对值阈值
        """
        stats = self.get_stats()
        if not stats or stats.std < 1e-9:
            return 0.0
        z = (threshold - abs(stats.mean)) / stats.std
        return _erfc(z / math.sqrt(2))

    def prob_up(self) -> float:
        """P(ΔP > 0) — 基于历史 ΔP 的上涨概率"""
        samples = list(self._delta_p_buffer)
        if not samples:
            return 0.5
        return sum(1 for x in samples if x > 0) / len(samples)

    def next_price_range(self, confidence: float = 0.95) -> Tuple[float, float]:
        """
        基于 CLT 估计下一聚合窗口 (n_agg 笔) 后的价格期望区间

        Returns
        -------
        (下限, 上限) 绝对价格
        """
        stats = self.get_stats()
        if not stats or self._prev_price is None:
            p = self._prev_price or 0.0
            return (p, p)

        z = 1.96 if confidence >= 0.95 else 1.645
        half_width = z * stats.std * math.sqrt(self.n_agg)
        center = self._prev_price + stats.clt_mu * self.n_agg
        return (round(center - half_width, 6), round(center + half_width, 6))

    def get_clt_means(self) -> List[float]:
        """返回 CLT 批均值序列，可外部绘图验证正态收敛"""
        return list(self._clt_means)

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    def _compute_histogram(
        self, samples: List[float], mean: float, std: float
    ) -> Tuple[List[float], List[float], List[float]]:
        """计算归一化直方图 + 拟合正态密度"""
        if not samples or std < 1e-9:
            return [], [], []

        lo, hi = min(samples), max(samples)
        if lo == hi:
            return [round(lo, 6)], [1.0], [1.0]

        # 两侧留出半个标准差的余量，避免边界截断效应
        margin = std * 0.5
        lo -= margin
        hi += margin
        bin_width = (hi - lo) / self.n_bins

        counts = [0] * self.n_bins
        for x in samples:
            idx = int((x - lo) / bin_width)
            idx = max(0, min(self.n_bins - 1, idx))
            counts[idx] += 1

        norm_factor = len(samples) * bin_width
        bin_centers = [lo + (i + 0.5) * bin_width for i in range(self.n_bins)]
        density     = [c / norm_factor for c in counts]

        # 拟合正态密度
        inv_std_sqrt2pi = 1.0 / (std * math.sqrt(2.0 * math.pi))
        normal = [
            inv_std_sqrt2pi * math.exp(-0.5 * ((x - mean) / std) ** 2)
            for x in bin_centers
        ]

        return (
            [round(b, 6) for b in bin_centers],
            [round(d, 6) for d in density],
            [round(v, 6) for v in normal],
        )

    def resize(self, new_size: int) -> None:
        """
        动态调整滚动历史长度。
        扩容: 保留全部现有样本，仅放大容量上限。
        缩容: 保留最新的 new_size 条，丢弃最旧的。
        """
        if new_size == self.max_samples:
            return
        old_size = self.max_samples
        current = list(self._delta_p_buffer)
        self.max_samples = new_size
        self._delta_p_buffer = deque(
            current[-new_size:] if len(current) > new_size else current,
            maxlen=new_size,
        )
        self._dirty = True
        logger.debug(
            "[PriceDist] resize %d → %d  保留样本: %d",
            old_size, new_size, len(self._delta_p_buffer),
        )

    def reset(self) -> None:
        """清空所有历史数据"""
        self._prev_price = None
        self._delta_p_buffer.clear()
        self._clt_agg_buffer.clear()
        self._clt_means.clear()
        self._last_stats = None
        self._dirty = True


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _erfc(x: float) -> float:
    """
    互补误差函数近似 (Abramowitz & Stegun 7.1.26)
    精度 ≈ 1.5e-7，避免引入 scipy/math.erfc (Python 3.2+ 已内置 math.erfc)
    """
    # Python 3.2+ math.erfc 可直接用；这里保留纯算法版本以示原理
    if x < 0:
        return 2.0 - _erfc(-x)
    t = 1.0 / (1.0 + 0.3275911 * x)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (
        1.421413741 + t * (-1.453152027 + t * 1.061405429)
    )))
    return poly * math.exp(-x * x)
