# intraday/app/main_engine.py
import time
from typing import Optional, List, TYPE_CHECKING

from ..core.types import Tick, WindowResult, PhysicsStatsResult
from ..core.liquidity_engine import LiquidityEngine
from ..core.physics_stats import EconophysicsStats
from ..config.sessions import TimeFunctionSwitch, MarketSession
from ..config.products import ProductConfig
from ..analytics.signal_engine import SignalEngine
from ..analytics.session_adapter import SessionAwareAdapter, SessionParams

if TYPE_CHECKING:
    from ..display.bridge import DisplayBridge
    from ..core.price_distribution import DeltaPStats
    from ..core.signals import SignalEvent
    from ..core.persistence import Persistence

class MainQuantEngine:
    """
    微观流动性与经济物理学主引擎 (Central Brain)
    负责调度时段切换、计算流动性损耗(CME模型)、以及物理学概率分布。
    """
    def __init__(self, product_config: ProductConfig,
                 history_size: int = 1000,
                 min_samples: int = 30,
                 clt_n_agg: int = 30,
                 persistence: Optional["Persistence"] = None):
        self.config = product_config
        self.time_switch = TimeFunctionSwitch()

        # 获取系统启动时的初始交易时段
        initial_session = self.time_switch.get_current_session()
        self.current_session_name = initial_session.session_name

        print(f"[{self.config.symbol}] 引擎启动。当前时段: {self.current_session_name.value}")
        print(f"[{self.config.symbol}] 初始聚合窗口: {initial_session.window_size_sec} 秒")

        # 初始化流动性引擎 (CME 离散度统计)
        self.liquidity_engine = LiquidityEngine(
            product_config=self.config,
            initial_window_sec=initial_session.window_size_sec
        )

        # 初始化物理学统计模块 (φ(ΔP) 概率密度与 CLT)
        self.physics_stats = EconophysicsStats(
            history_size=history_size,
            clt_n_agg=clt_n_agg,
            min_samples=min_samples,
        )

        # 显示桥接 (可选，不设置则退回 print 模式)
        self._bridge: Optional["DisplayBridge"] = None

        # 持久化（可选）
        self._persistence: Optional["Persistence"] = persistence

        # 信号层
        self.signal_engine = SignalEngine()
        self.signal_engine.register(product_config)

        # 动态时段参数适配器
        self._session_adapter = SessionAwareAdapter()
        self._session_adapter.on_change(self._on_session_params_change)

    # ── 显示桥接 & 辅助接口 ────────────────────────────────────

    def _on_session_params_change(
        self, old: SessionParams, new: SessionParams
    ) -> None:
        """时段切换时同步更新所有参数: window_sec / min_samples / history_size"""
        # 1. liquidity_engine 窗口（这是控制结算频率的关键）
        self.liquidity_engine.set_window_size(new.window_sec)
        # 2. 物理学模块
        self.physics_stats._tracker.resize(new.history_size)
        self.physics_stats.min_samples = new.min_samples
        print(
            f"[{self.config.symbol}] 时段 {old.session_name} → {new.session_name}  "
            f"window={new.window_sec:.0f}s  "
            f"min_samples={new.min_samples}  "
            f"history={new.history_size}  "
            f"(覆盖 {new.coverage_minutes:.0f} min)"
        )

    def set_bridge(self, bridge: "DisplayBridge") -> None:
        """注册显示桥接器 (注册后 _trigger_alert 不再 print)."""
        self._bridge = bridge

    def get_current_session(self) -> str:
        """返回当前时段名称字符串，供 TUI 显示"""
        return self.current_session_name.value

    def get_price_distribution(self) -> Optional["DeltaPStats"]:
        """返回当前 ΔP 统计快照，供 TUI 显示"""
        return self.physics_stats._tracker.get_stats()

    def get_recent_signals(self, n: int = 20) -> List["SignalEvent"]:
        """返回最近 n 条信号，供 TUI 显示"""
        return self.signal_engine.recent(n)

    def on_signal(self, callback) -> None:
        """注册信号回调: callback(SignalEvent) -> None"""
        self.signal_engine.on_signal(callback)

    def flush_window(self, now: float) -> None:
        """时钟驱动强制结算，供外部定时调用（如 TUI 刷新循环）"""
        result = self.liquidity_engine.flush(now)
        if result is not None:
            result.symbol  = self.config.symbol
            result.session = self.current_session_name.value
            if self._bridge:
                self._bridge.emit(result)
            active_config = self.time_switch.get_current_session(now)
            self._evaluate_market_state(result, active_config)

    # ── Tick 处理 ────────────────────────────────────────────────

    def on_tick_received(self, price: float, volume: int, side: str, timestamp: float = None):
        """
        这个接口暴露给你的 IBAPI 客户端 (比如重写了 tickByTickAllLast 的地方)。
        """
        if timestamp is None:
            timestamp = time.time()
            
        # 1. 查询时间开关：当前是什么时段？该用什么参数？
        active_config = self.time_switch.get_current_session(timestamp)
        
        # 停盘维护期间，忽略数据
        if active_config.session_name == MarketSession.MAINTENANCE:
            return
            
        # 2. 时段切换检测 — 统一交由 SessionAwareAdapter 驱动
        #    _on_session_params_change 回调会同步更新 liquidity_engine / physics_stats
        if self.current_session_name != active_config.session_name:
            self.current_session_name = active_config.session_name
        self._session_adapter.tick(self.current_session_name.value)
            
        # 3. 将标准化 Tick 喂入流动性引擎
        tick = Tick(price=price, volume=volume, timestamp=timestamp, side=side)
        
        # liquidity_engine 会暂存 tick，一旦跨越了 window_size_sec (例如60秒)，
        # 它就会结算出一个完整的 WindowResult 返回给我们。
        window_result = self.liquidity_engine.process_tick(tick)
        
        if window_result is not None:
            # 填充 symbol 和 session 标签
            window_result.symbol  = self.config.symbol
            window_result.session = self.current_session_name.value
            # 4. 推送到显示桥接
            if self._bridge:
                self._bridge.emit(window_result)
            # 5. 综合状态评估
            self._evaluate_market_state(window_result, active_config)

    def _evaluate_market_state(self, window_result: WindowResult, session_config):
        """
        综合评估：结合 CME 的摩擦力(冲击成本) 与 物理学的动能(ΔP分布)
        """
        # 1. 修正冲击成本：引入时段自治乘数
        # CME 平方根法则预估: 真实的微观摩擦力需要根据日均成交量和波动率修正
        adjusted_impact = session_config.impact_factor * (
            window_result.impact_bps / session_config.adv_multiplier
        )
        
        # 2. 物理学状态更新 (输入当前窗口的加权均价 VWAP + 成交量驱动衰减系数)
        physics_result = self.physics_stats.update(
            window_end=window_result.window_end,
            current_vwap=window_result.vwap,
            volume=window_result.total_volume,
        )

        # 3. 持久化写入（批量缓冲，不阻塞主流程）
        if self._persistence is not None:
            decay = self.physics_stats.get_decay_stats(window_result.window_end)
            self._persistence.write_window(window_result, decay)
            if physics_result is not None:
                self._persistence.write_physics(
                    physics_result,
                    decay=decay,
                    symbol=getattr(window_result, "symbol", ""),
                )
        
        if physics_result is None:
            # 数据积累阶段，仍运行无分布信号检测
            self.signal_engine.evaluate(window_result, None)
            return

        # 信号引擎评估 (含厚尾)
        self.signal_engine.evaluate(window_result, physics_result)
            
        # ==========================================================
        # 核心策略过滤逻辑：结合离散度与峰度
        # ==========================================================
        
        # 状态 A：极端尾部风险 (突发剧烈单边行情)
        # 特征：超额峰度极大 (正态基准=0，>5 表示严重厚尾) + 价格离散度极高
        if physics_result.kurtosis > 5.0 and window_result.price_levels >= 4:
            self._trigger_alert("TAIL_RISK", window_result, physics_result, adjusted_impact)
            # 你的处理建议：暂停市价单执行，切换为被动限价单，或完全离场观望。

        # 状态 B：典型的流动性真空 (Liquidity Vacuum)
        # 特征：成交量(total_volume)极低，但价格离散度(price_levels)依然很高
        elif window_result.total_volume < (10 * session_config.adv_multiplier) and window_result.price_levels >= 3:
            self._trigger_alert("VACUUM", window_result, physics_result, adjusted_impact)
            # 处理建议：此时的突破很可能是假的(洗盘)，切忌追单。

        # 状态 C：完美的扩散状态 (理想的 OU 均值回归交易环境)
        # 特征：超额峰度接近 0 (正态)，价格跨度小(1-2个tick)，流动性极好
        elif physics_result.kurtosis < 1.0 and window_result.price_levels <= 2:
            self._trigger_alert("GAUSSIAN_DIFFUSION", window_result, physics_result, adjusted_impact)
            # 处理建议：可以安全地执行你的 pairs trading 或均值回归算法，滑点可控。
            
        # 其他常态忽略...

    def _trigger_alert(self, state_type: str, w_res: WindowResult, p_res: PhysicsStatsResult, adj_impact: float):
        """状态告警 — 有 bridge 时静默 (由 TUI 显示)，否则 print"""
        if self._bridge:
            return  # TUI 模式: 告警由显示层负责
        time_str = time.strftime('%H:%M:%S', time.localtime(w_res.window_end))
        print(f"[{time_str}] 状态: {state_type}")
        print(f"  ├─ 物理学特征: ΔP={p_res.delta_p:.2f}, 峰度(Kurt)={p_res.kurtosis:.1f}")
        print(f"  └─ CME 流动性: 离散度={w_res.price_levels} 档, 修正冲击={adj_impact:.2f} bps, 成交={w_res.total_volume} 手")