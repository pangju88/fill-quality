# intraday/core/decay_tracker.py
"""
DecayWeightedTracker
====================
指数衰减加权统计追踪器，取代 PriceDistributionTracker 的纯等权统计。

权重公式:
    w_i = exp(-k * (t_now - t_i))

衰减系数 k 动态调整（随流动性梯度缩放）:
    k = k_base × (1 + λ × liquidity_ratio)
    liquidity_ratio = vol_window / vol_avg_20  (当前窗口成交量 / 近20窗口均量)

覆盖时间目标（基础锚定 300s）:
    低流动性  → k 小 → 半衰期长 → 有效覆盖 ≈ 600s+
    标准       → k_base → 半衰期 ≈ 300s
    高流动性  → k 大 → 半衰期短 → 有效覆盖 ≈ 60s

纯 Python，无 numpy/scipy 依赖。
"""
from __future__ import annotations

import math
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── 衰减参数配置 ──────────────────────────────────────────────────────────────

@dataclass
class DecayConfig:
    """
    衰减函数参数配置。

    默认值：基础半衰期 300s，流动性梯度系数 λ=2.0。
    超短线激进建议：k_base=0.00462, lam=3.0, k_min=0.001, k_max=0.08
    """

    # 基础衰减系数 k_base
    # half_life = ln(2) / k
    # k=0.00231 → half_life ≈ 300s（默认基础锚）
    k_base: float = 0.00231

    # 流动性驱动放大系数 λ
    # k_eff = k_base × (1 + λ × liquidity_ratio)
    # λ=2.0，ratio=3 → k 放大 ×7 → half_life 压缩至 ~43s
    lam: float = 2.0

    # k 的硬边界（防止极端压缩或无限扩展）
    k_min: float = 0.00050    # 上限半衰期 ≈ 1386s
    k_max: float = 0.05000    # 下限半衰期 ≈ 14s

    # 样本最大保留时间（超出则丢弃，节省内存）
    max_age_sec: float = 1800.0

    # CLT 聚合批次大小（每 n_agg 个 ΔP 求一次批均值）
    n_agg: int = 30

    # 直方图 bin 数量
    n_bins: int = 40

    # 最少等效样本量（Σw_i < 此值则返回 None）
    min_eff_n: float = 5.0

    # stats 缓存 TTL（秒）：TUI 20fps → 50ms 内复用同一快照，不重算
    stats_ttl_sec: float = 0.05

    # 过期样本清理间隔（秒）：不必每帧执行 evict
    evict_interval_sec: float = 5.0


# ── 衰减加权统计快照 ──────────────────────────────────────────────────────────

@dataclass
class DecayStats:
    """衰减加权 ΔP 统计快照（含元信息）"""
    # 元信息
    eff_n: float            # 等效样本量 Σw_i
    k_effective: float      # 当前有效衰减系数
    half_life_sec: float    # 当前半衰期（秒）
    coverage_sec: float     # 有效覆盖时长（99% 权重对应时间）
    liquidity_ratio: float  # 当前流动性倍数

    # 加权矩
    mean: float             # 加权均值 E[ΔP]
    std: float              # 加权标准差 σ(ΔP)
    skew: float             # 加权偏度 γ₁
    kurt: float             # 加权超额峰度 γ₂（正态=0）
    min_val: float
    max_val: float

    # CLT 置信区间
    clt_sigma: float        # σ / √n_agg
    clt_n_agg: int
    ci_lo: float            # μ - 1.96 × clt_sigma
    ci_hi: float            # μ + 1.96 × clt_sigma

    # 概率密度直方图
    hist_bins: List[float] = field(default_factory=list)
    hist_density: List[float] = field(default_factory=list)
    normal_density: List[float] = field(default_factory=list)


# ── 核心追踪器 ────────────────────────────────────────────────────────────────

class DecayWeightedTracker:
    """
    指数衰减加权 ΔP 追踪器。

    基础用法（每窗口结算后调用一次）:
        tracker = DecayWeightedTracker(config=DecayConfig())
        tracker.update(vwap=2985.50, ts=time.time(), volume=42)
        stats = tracker.get_stats(now=time.time())

    动态梯度逻辑:
        - 流动性高（volume >> avg）→ k 大 → 权重衰减快 → 近期数据权重集中
        - 流动性低（volume << avg）→ k 小 → 权重衰减慢 → 保留更长历史平滑
    """

    def __init__(self, config: Optional[DecayConfig] = None) -> None:
        self._cfg = config or DecayConfig()

        # 样本存储: deque of (timestamp, delta_p)
        self._samples: deque[Tuple[float, float]] = deque()

        # 成交量历史（最近 20 窗口，用于计算流动性倍数）
        self._vol_history: deque[int] = deque(maxlen=20)
        self._current_liquidity_ratio: float = 1.0

        # 前一 VWAP（差分用）
        self._prev_vwap: Optional[float] = None

        # CLT 聚合
        self._clt_buf: List[float] = []
        self._clt_means: deque[float] = deque(maxlen=500)

        # 脏标记（每次 update 置脏，get_stats 清脏）
        self._dirty: bool = True
        self._cached_stats: Optional[DecayStats] = None
        self._cached_stats_ts: float = 0.0   # 上次计算快照的时间戳
        self._last_evict_ts: float = 0.0     # 上次 evict 的时间戳
        self._last_k: float = self._cfg.k_base

    # ── 公开接口：输入 ────────────────────────────────────────────────────────

    def update(self, vwap: float, ts: float, volume: int = 0) -> Optional[float]:
        """
        推入新窗口 VWAP，返回 ΔP（第一笔无前值，返回 None）。

        Parameters
        ----------
        vwap   : 本窗口成交量加权均价
        ts     : 本窗口结束时间戳（Unix 秒）
        volume : 本窗口总成交量（驱动流动性比率计算）
        """
        # 1. 更新成交量历史 → 流动性倍数
        if volume > 0:
            self._vol_history.append(volume)
        if len(self._vol_history) >= 3:
            avg_vol = sum(self._vol_history) / len(self._vol_history)
            self._current_liquidity_ratio = (volume / avg_vol) if avg_vol > 0 else 1.0
        else:
            self._current_liquidity_ratio = 1.0

        # 2. 第一笔初始化，无 ΔP
        if self._prev_vwap is None:
            self._prev_vwap = vwap
            return None

        dp = vwap - self._prev_vwap
        self._prev_vwap = vwap

        # 3. 只保存非零 ΔP（零差分无信息量）
        if dp != 0.0:
            self._samples.append((ts, dp))

            # CLT 批次聚合
            self._clt_buf.append(dp)
            if len(self._clt_buf) >= self._cfg.n_agg:
                self._clt_means.append(
                    sum(self._clt_buf) / len(self._clt_buf)
                )
                self._clt_buf.clear()

        self._dirty = True
        return dp

    # ── 公开接口：统计输出 ────────────────────────────────────────────────────

    def get_stats(self, now: float) -> Optional[DecayStats]:
        """
        计算当前衰减加权统计快照。

        TTL 缓存：stats_ttl_sec 内无新数据时直接返回上次快照。
        evict 节流：每 evict_interval_sec 才清理一次超龄样本。

        Parameters
        ----------
        now : 当前时间戳（Unix 秒），用于计算各样本权重 w_i = exp(-k*age_i)
        """
        # TTL 缓存命中：无新数据 且 未超过 TTL → 直接复用
        if (
            not self._dirty
            and self._cached_stats is not None
            and (now - self._cached_stats_ts) < self._cfg.stats_ttl_sec
        ):
            return self._cached_stats

        # 1. 动态 k（流动性梯度）
        k = self._compute_k()
        self._last_k = k

        # 2. 节流 evict：距上次清理超过 evict_interval_sec 才执行
        if (now - self._last_evict_ts) >= self._cfg.evict_interval_sec:
            self._evict(now)
            self._last_evict_ts = now

        if not self._samples:
            return None

        # 3. 衰减权重
        weights: List[float] = []
        values: List[float] = []
        for ts_i, dp_i in self._samples:
            age = max(0.0, now - ts_i)
            weights.append(math.exp(-k * age))
            values.append(dp_i)

        eff_n = sum(weights)
        if eff_n < self._cfg.min_eff_n:
            return None

        # 4. 加权矩（均值 / 方差 / 偏度 / 超额峰度）
        mean = sum(w * x for w, x in zip(weights, values)) / eff_n

        var = sum(w * (x - mean) ** 2 for w, x in zip(weights, values)) / eff_n
        std = math.sqrt(var) if var > 1e-18 else 1e-9

        if std > 1e-9:
            skew = sum(w * ((x - mean) / std) ** 3
                       for w, x in zip(weights, values)) / eff_n
            kurt = (sum(w * ((x - mean) / std) ** 4
                        for w, x in zip(weights, values)) / eff_n) - 3.0
        else:
            skew = kurt = 0.0

        # 5. CLT
        clt_sigma = std / math.sqrt(max(1, self._cfg.n_agg))
        ci_lo = mean - 1.96 * clt_sigma
        ci_hi = mean + 1.96 * clt_sigma

        # 6. 元信息
        half_life_sec = math.log(2.0) / k
        coverage_sec  = math.log(100.0) / k   # 99% 权重覆盖时间

        # 7. 加权直方图（仅用权重 >= 5% 的样本）
        threshold = 0.05
        sig_vals = [x for w, x in zip(weights, values) if w >= threshold]
        sig_wts  = [w for w in weights if w >= threshold]
        hist_bins, hist_density, normal_density = self._histogram(
            sig_vals, sig_wts, mean, std
        )

        stats = DecayStats(
            eff_n=round(eff_n, 2),
            k_effective=round(k, 6),
            half_life_sec=round(half_life_sec, 1),
            coverage_sec=round(coverage_sec, 1),
            liquidity_ratio=round(self._current_liquidity_ratio, 2),
            mean=round(mean, 6),
            std=round(std, 6),
            skew=round(skew, 4),
            kurt=round(kurt, 4),
            min_val=min(values),
            max_val=max(values),
            clt_sigma=round(clt_sigma, 6),
            clt_n_agg=self._cfg.n_agg,
            ci_lo=round(ci_lo, 6),
            ci_hi=round(ci_hi, 6),
            hist_bins=hist_bins,
            hist_density=hist_density,
            normal_density=normal_density,
        )

        self._cached_stats = stats
        self._dirty = False
        return stats

    # ── 辅助查询 ──────────────────────────────────────────────────────────────

    def prob_exceed(self, threshold: float, now: float) -> float:
        """P(|ΔP| > threshold)，基于衰减加权正态近似"""
        stats = self.get_stats(now)
        if not stats or stats.std < 1e-9:
            return 0.0
        z = (abs(threshold) - abs(stats.mean)) / stats.std
        return _erfc(z / math.sqrt(2))

    def prob_up(self, now: float) -> float:
        """P(ΔP > 0)，基于衰减加权方向计数"""
        if not self._samples:
            return 0.5
        k = self._last_k
        w_up    = sum(math.exp(-k * max(0.0, now - ts)) for ts, dp in self._samples if dp > 0)
        w_total = sum(math.exp(-k * max(0.0, now - ts)) for ts, dp in self._samples)
        return w_up / w_total if w_total > 0 else 0.5

    def get_clt_means(self) -> List[float]:
        return list(self._clt_means)

    def reset(self) -> None:
        self._samples.clear()
        self._vol_history.clear()
        self._clt_buf.clear()
        self._clt_means.clear()
        self._prev_vwap = None
        self._dirty = True
        self._cached_stats = None
        self._cached_stats_ts = 0.0
        self._last_evict_ts = 0.0

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _compute_k(self) -> float:
        """
        动态衰减系数（流动性梯度）:
            k = k_base × (1 + λ × liquidity_ratio)
            clip 到 [k_min, k_max]
        """
        ratio = max(0.0, self._current_liquidity_ratio)
        k = self._cfg.k_base * (1.0 + self._cfg.lam * ratio)
        return max(self._cfg.k_min, min(self._cfg.k_max, k))

    def _evict(self, now: float) -> None:
        """丢弃超过 max_age_sec 的样本"""
        cutoff = now - self._cfg.max_age_sec
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def _histogram(
        self,
        values: List[float],
        weights: List[float],
        mean: float,
        std: float,
    ) -> Tuple[List[float], List[float], List[float]]:
        """加权直方图 + 对应正态密度"""
        if not values or std < 1e-9:
            return [], [], []
        lo, hi = min(values), max(values)
        if lo == hi:
            return [round(lo, 6)], [1.0], [1.0]

        margin = std * 0.5
        lo -= margin
        hi += margin
        n = self._cfg.n_bins
        bw = (hi - lo) / n

        counts = [0.0] * n
        for x, w in zip(values, weights):
            idx = max(0, min(n - 1, int((x - lo) / bw)))
            counts[idx] += w

        total = sum(counts)
        norm_f = total * bw if total > 0 else 1.0
        centers = [lo + (i + 0.5) * bw for i in range(n)]
        density = [c / norm_f for c in counts]

        inv = 1.0 / (std * math.sqrt(2.0 * math.pi))
        normal = [
            inv * math.exp(-0.5 * ((x - mean) / std) ** 2)
            for x in centers
        ]

        return (
            [round(b, 6) for b in centers],
            [round(d, 6) for d in density],
            [round(v, 6) for v in normal],
        )


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _erfc(x: float) -> float:
    """Abramowitz & Stegun erfc 近似，精度 ≈ 1.5e-7"""
    if x < 0:
        return 2.0 - _erfc(-x)
    t = 1.0 / (1.0 + 0.3275911 * x)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (
        1.421413741 + t * (-1.453152027 + t * 1.061405429)
    )))
    return poly * math.exp(-x * x)
