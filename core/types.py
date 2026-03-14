from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Tick:
    """标准化的底层逐笔成交数据 (从 IBKR 传入)"""
    price: float
    volume: int
    timestamp: float          # Unix 时间戳 (秒, 支持小数)
    side: str                 # 'buy' 或 'sell'
    order_id: str = ""        # 可选: IBKR 订单 ID 追踪
    delta_p: Optional[float] = None   # ΔP = price - prev_price (由 PriceDistributionTracker 填充)


@dataclass
class WindowResult:
    """单个时间窗口的 CME 价格离散度计算结果"""
    window_start: float
    window_end: float
    time_label: str

    # 价格离散度指标
    price_levels: int           # 核心: 不同成交价格层级数
    price_range_ticks: int      # 价格范围 (以 tick 为单位)
    price_range_abs: float      # 价格范围 (绝对值)

    # 冲击成本
    impact_bps: float           # 基点冲击成本
    impact_dollar: float        # 美元冲击成本 (每手)

    # 成交数据
    total_volume: int
    tick_count: int
    vwap: float
    high_price: float
    low_price: float

    # 订单流
    buy_volume: int
    sell_volume: int
    delta: int                  # 买 - 卖
    delta_ratio: float          # delta / total_volume

    unique_prices: List[float] = field(default_factory=list)

    # 多标的支持字段 (单标的时可留空)
    symbol:  str = ""
    session: str = ""


@dataclass
class PhysicsStatsResult:
    """
    Econophysics 引擎的统计输出
    包含 ΔP 分布统计及 φ(ΔP) 概率密度 (基于 CLT)
    """
    window_end: float
    current_price: float
    delta_p: float              # 当前窗口与上一窗口的 VWAP 差值

    empirical_mean: float       # E[ΔP]
    empirical_std: float        # σ(ΔP)
    skewness: float             # 偏度 γ₁
    kurtosis: float             # 超额峰度 γ₂ (>0 = 厚尾)

    clt_sigma: float            # CLT 标准差 = σ / √n_agg
    ci_lo: float                # 95% 置信下界
    ci_hi: float                # 95% 置信上界

    # φ(ΔP) 概率密度 — 用 List[float] 替代 np.ndarray, 无需 numpy 依赖
    pdf_x: List[float] = field(default_factory=list)   # x 轴网格
    pdf_y: List[float] = field(default_factory=list)   # 经验密度
    pdf_normal: List[float] = field(default_factory=list)  # 拟合正态密度