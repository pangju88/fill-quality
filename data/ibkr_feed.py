# intraday/data/ibkr_feed.py
"""
IBKR 实时 Tick 数据接口 (ib_insync)
tickByTick Last — 每笔成交推送，side 通过 tick rule 近似
"""
import copy
import time
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


# ── 合约配置 ─────────────────────────────────────────────────────

@dataclass
class IBKRConfig:
    host: str            = "127.0.0.1"
    port: int            = 7497          # paper=7497 / live=7496
    client_id: int       = 1
    timeout: float       = 15.0
    symbol: str          = "MGC"
    sec_type: str        = "FUT"
    exchange: str        = "COMEX"
    currency: str        = "USD"
    last_trade_date: str = ""            # 如 "202506"，空=自动最近月
    multiplier: str      = "10"


_PRESETS: dict = {
    "MGC": IBKRConfig(symbol="MGC", exchange="COMEX", multiplier="10"),
    "GC":  IBKRConfig(symbol="GC",  exchange="COMEX", multiplier="100"),
    "MES": IBKRConfig(symbol="MES", exchange="CME",   multiplier="5"),
    "MNQ": IBKRConfig(symbol="MNQ", exchange="CME",   multiplier="2"),
    "ES":  IBKRConfig(symbol="ES",  exchange="CME",   multiplier="50"),
    "NQ":  IBKRConfig(symbol="NQ",  exchange="CME",   multiplier="20"),
    "CL":  IBKRConfig(symbol="CL",  exchange="NYMEX", multiplier="1000"),
    "SI":  IBKRConfig(symbol="SI",  exchange="COMEX", multiplier="5000"),
    "ZB":  IBKRConfig(symbol="ZB",  exchange="CBOT",  multiplier="1000"),
}


# ── 主类 ─────────────────────────────────────────────────────────

class IBKRTickFeed:
    """
    IBKR tickByTick 逐笔成交订阅

    典型用法:
        feed = IBKRTickFeed.from_preset("MGC", last_trade_date="202506")
        feed.subscribe(engine.on_tick_received)
        feed.start_async()
        ...
        feed.stop()
    """

    def __init__(self, config: IBKRConfig) -> None:
        self._cfg = config
        self._callbacks: List[Callable] = []
        self._ib = None
        self._contract = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._connected = threading.Event()
        self._last_price: Optional[float] = None
        self._last_tick_time: float = time.time()
        self.tick_count: int = 0
        self.error_count: int = 0
        self.resubscribe_count: int = 0
        self._consecutive_resubscribes: int = 0  # 连续重订阅次数（收到真实 tick 后清零）

    # watchdog：无 tick 超过此秒数则重新订阅
    WATCHDOG_TIMEOUT: int = 600   # 10 分钟
    # 连续重订阅超过此次数则全量断线重连
    MAX_RESUBSCRIBES: int = 3

    # ── 工厂方法 ──────────────────────────────────────────────────

    @classmethod
    def from_preset(
        cls,
        symbol: str,
        last_trade_date: str = "",
        port: int = 7497,
        client_id: int = 1,
    ) -> "IBKRTickFeed":
        if symbol not in _PRESETS:
            raise ValueError(
                f"未知品种 '{symbol}'，可用: {list(_PRESETS)}"
            )
        cfg = copy.copy(_PRESETS[symbol])
        cfg.port = port
        cfg.client_id = client_id
        cfg.last_trade_date = last_trade_date
        return cls(cfg)

    # ── 公共接口 ──────────────────────────────────────────────────

    def subscribe(self, callback: Callable) -> None:
        """
        注册回调，签名:
            callback(price, volume, timestamp, side) -> None
        与 MainQuantEngine.on_tick_received 签名一致，可直接传入。
        """
        self._callbacks.append(callback)

    def start_async(self) -> threading.Thread:
        """非阻塞：IBKR 在后台守护线程运行"""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"ibkr-{self._cfg.symbol}",
        )
        self._thread.start()
        return self._thread

    def wait_connected(self, timeout: float = 20.0) -> bool:
        """阻塞至连接并订阅成功，超时返回 False"""
        return self._connected.wait(timeout=timeout)

    def stop(self) -> None:
        self._stop_event.set()
        if self._ib:
            try:
                self._ib.disconnect()
            except Exception:
                pass
        logger.info("[IBKR] 已断开，共收 %d ticks", self.tick_count)

    @property
    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    @property
    def local_symbol(self) -> str:
        if self._contract:
            return self._contract.localSymbol
        return self._cfg.symbol

    # ── 内部：连接 & 订阅 ──────────────────────────────────────────

    def _run(self) -> None:
        # Python 3.10+ 在非主线程中没有默认事件循环，必须手动创建
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            from ib_insync import IB, Future
        except ImportError:
            raise RuntimeError("请先安装: pip install ib_insync")

        # ── 外层重连循环：watchdog 全量重连或异常退出后自动重试 ──
        reconnect_delay = 30  # 重连等待秒数
        while not self._stop_event.is_set():
            self._run_once(IB, Future)
            if self._stop_event.is_set():
                break
            logger.warning(
                "[IBKR][%s] 连接断开，%d 秒后重连...",
                self._cfg.symbol, reconnect_delay,
            )
            self._stop_event.wait(timeout=reconnect_delay)

        loop.close()

    def _run_once(self, IB, Future) -> None:
        """单次连接+订阅+事件循环；退出后由 _run 决定是否重连"""
        cfg = self._cfg
        ib = IB()

        # clientId 冲突时自动递增重试（上次进程未完全释放时常见）
        connected = False
        client_id = cfg.client_id
        for attempt in range(5):
            try:
                logger.info(
                    "[IBKR] 连接 %s:%d  clientId=%d (第%d次尝试)",
                    cfg.host, cfg.port, client_id, attempt + 1,
                )
                ib.connect(
                    cfg.host, cfg.port,
                    clientId=client_id,
                    timeout=cfg.timeout,
                    readonly=False,
                )
                connected = True
                break
            except Exception as e:
                err = str(e)
                if "already in use" in err or "326" in err or isinstance(e, TimeoutError):
                    logger.warning(
                        "[IBKR] clientId=%d 被占用，尝试 clientId=%d",
                        client_id, client_id + 1,
                    )
                    client_id += 1
                    ib = IB()   # 重建实例
                else:
                    logger.error("[IBKR] 连接失败: %s", e)
                    self.error_count += 1
                    return

        if not connected:
            logger.error("[IBKR] 5 次重试均失败，请在 TWS 中手动断开旧连接")
            self.error_count += 1
            return

        self._ib = ib

        # ── 解析合约（自动选最近未到期主力月）────────────────────
        import datetime as _dt
        today_str = _dt.date.today().strftime("%Y%m%d")  # e.g. "20260218"

        raw = Future(
            symbol=cfg.symbol,
            exchange=cfg.exchange,
            currency=cfg.currency,
            lastTradeDateOrContractMonth=cfg.last_trade_date,
            multiplier=cfg.multiplier,
        )
        try:
            details = ib.reqContractDetails(raw)
        except Exception as e:
            logger.error("[IBKR] reqContractDetails 失败: %s", e)
            ib.disconnect()
            return

        if not details:
            logger.error("[IBKR] 找不到合约 %s %s", cfg.symbol, cfg.last_trade_date)
            ib.disconnect()
            return

        # 过滤已到期合约，再选最近一个
        # lastTradeDateOrContractMonth 格式可能是 "20260425" 或 "202604"
        def _expiry_key(d) -> str:
            s = d.contract.lastTradeDateOrContractMonth
            return s if len(s) == 8 else s + "01"   # 补全到 YYYYMMDD 方便比较

        active = [d for d in details if _expiry_key(d) >= today_str]
        if not active:
            # 万一全部过滤掉（罕见），回退到全列表
            active = details
            logger.warning("[IBKR] 所有合约均已到期，使用最新到期合约")

        active.sort(key=_expiry_key)
        self._contract = active[0].contract
        logger.info(
            "[IBKR] 合约: %s  到期: %s",
            self._contract.localSymbol,
            self._contract.lastTradeDateOrContractMonth,
        )

        # ── 订阅 tickByTick Last ──────────────────────────────────
        ib.reqTickByTickData(
            self._contract,
            tickType="Last",
            numberOfTicks=0,       # 0 = 持续推送
            ignoreSize=False,
        )
        ib.pendingTickersEvent += self._on_pending_tickers

        self._connected.set()
        logger.info("[IBKR] 订阅成功，等待行情...")
        self._last_tick_time = time.time()  # 重置计时器
        self._consecutive_resubscribes = 0

        # ── 事件循环 ─────────────────────────────────────────────
        try:
            while not self._stop_event.is_set():
                ib.waitOnUpdate(timeout=1.0)

                # ── Watchdog：静默超时则重新订阅，多次无效则全量重连 ──
                elapsed = time.time() - self._last_tick_time
                if elapsed > self.WATCHDOG_TIMEOUT:
                    logger.warning(
                        "[IBKR][%s] %.0f 秒无新 tick，重新订阅行情（连续第 %d 次）...",
                        self._cfg.symbol, elapsed, self._consecutive_resubscribes + 1,
                    )
                    try:
                        ib.cancelTickByTickData(self._contract, "Last")
                        time.sleep(2)
                        ib.reqTickByTickData(
                            self._contract,
                            tickType="Last",
                            numberOfTicks=0,
                            ignoreSize=False,
                        )
                        # 注意：不在此处重置 _last_tick_time
                        # 计时器仅在 _dispatch 收到真实 tick 后才清零
                        self._consecutive_resubscribes += 1
                        self.resubscribe_count += 1
                        logger.info(
                            "[IBKR][%s] 重新订阅完成（第 %d 次）",
                            self._cfg.symbol, self.resubscribe_count,
                        )
                        # 超过阈值 → 退出内层循环触发全量重连
                        if self._consecutive_resubscribes >= self.MAX_RESUBSCRIBES:
                            logger.warning(
                                "[IBKR][%s] 连续 %d 次重订阅后仍无数据，触发全量重连",
                                self._cfg.symbol, self._consecutive_resubscribes,
                            )
                            break
                    except Exception as re_err:
                        logger.error("[IBKR][%s] 重新订阅失败: %s", self._cfg.symbol, re_err)
                        self.error_count += 1
        except Exception as e:
            logger.error("[IBKR] 事件循环异常: %s", e)
            self.error_count += 1
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass
            self._ib = None

    # ── 内部：数据解析 ────────────────────────────────────────────

    def _on_pending_tickers(self, tickers) -> None:
        for ticker in tickers:
            for t in ticker.tickByTicks:
                try:
                    price = float(t.price)
                    volume = int(t.size)
                    ts = t.time.timestamp() if hasattr(t.time, "timestamp") else time.time()
                    self._dispatch(price, volume, ts)
                except Exception as e:
                    logger.warning("[IBKR] 解析 tick 异常: %s", e)

    def _dispatch(self, price: float, volume: int, ts: float) -> None:
        """tick rule 推断 aggressor side，然后广播到所有回调"""
        if price <= 0 or volume <= 0:
            return

        if self._last_price is None or price > self._last_price:
            side = "buy"
        elif price < self._last_price:
            side = "sell"
        else:
            side = "buy"          # 价格不变：维持上次方向

        self._last_price = price
        self._last_tick_time = time.time()
        self._consecutive_resubscribes = 0   # 收到真实 tick，重置连续重订阅计数
        self.tick_count += 1

        for cb in self._callbacks:
            try:
                cb(price=price, volume=volume, timestamp=ts, side=side)
            except Exception as e:
                logger.error("[IBKR] 回调异常: %s", e)
                self.error_count += 1

    def __repr__(self) -> str:
        return (
            f"IBKRTickFeed({self._cfg.symbol} "
            f"port={self._cfg.port} "
            f"connected={self.is_connected} "
            f"ticks={self.tick_count})"
        )
