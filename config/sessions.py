import time
from datetime import datetime
import pytz
from enum import Enum
from dataclasses import dataclass

class MarketSession(Enum):
    ASIAN = "Asian_Session"           # 亚盘 (清淡)
    EURO_US = "Euro_US_Overlap"       # 欧美重叠 (最活跃)
    US_AFTERNOON = "US_Afternoon"     # 美盘下午 (中等)
    MAINTENANCE = "Maintenance"       # 停盘维护

@dataclass
class SessionConfig:
    session_name: MarketSession
    window_size_sec: int          # 动态时间窗口大小 (核心！如 60s 或 300s)
    vol_multiplier: float         # 波动率基准乘数 
    adv_multiplier: float         # 日均成交量(ADV)基准乘数 
    impact_factor: float          # 冲击校准因子

class TimeFunctionSwitch:
    """
    时间函数开关：根据美东时间(ET)动态切换 COMEX MGC 的微观结构参数。
    """
    def __init__(self):
        # 强制使用纽约时间作为判定基准 (处理夏令时/冬令时)
        self.tz_ny = pytz.timezone('America/New_York')
        
        # 定义自治参数字典
        self.profiles = {
            MarketSession.ASIAN: SessionConfig(
                session_name=MarketSession.ASIAN,
                window_size_sec=60,        # 亚盘 60s 窗口
                vol_multiplier=0.4,
                adv_multiplier=0.3,
                impact_factor=1.5
            ),
            MarketSession.EURO_US: SessionConfig(
                session_name=MarketSession.EURO_US,
                window_size_sec=5,         # 欧美高峰 5s 窗口
                vol_multiplier=1.8,
                adv_multiplier=2.5,
                impact_factor=0.8
            ),
            MarketSession.US_AFTERNOON: SessionConfig(
                session_name=MarketSession.US_AFTERNOON,
                window_size_sec=5,         # 美盘下午 5s 窗口
                vol_multiplier=1.0,
                adv_multiplier=1.0,
                impact_factor=1.0
            ),
            MarketSession.MAINTENANCE: SessionConfig(
                session_name=MarketSession.MAINTENANCE,
                window_size_sec=300,       # 维护期 5 分钟窗口
                vol_multiplier=0.0,
                adv_multiplier=0.0,
                impact_factor=0.0
            )
        }

    def get_current_session(self, timestamp: float = None) -> SessionConfig:
        """
        输入 Unix 时间戳 (如果为 None 则取系统当前时间)，返回对应的交易时段配置。

        COMEX MGC 时段 (ET):
          17:00 – 18:00   维护停盘
          18:00 – 08:20   亚盘 / 欧盘早盘 (清淡)
          08:20 – 13:30   欧美重叠高峰 (COMEX开盘铃到黄金期货收盘)
          13:30 – 17:00   美盘下午 (流动性回落)
        """
        if timestamp is None:
            timestamp = time.time()

        # 使用 fromtimestamp + tz 替代已弃用的 utcfromtimestamp (Python 3.12+)
        dt_ny = datetime.fromtimestamp(timestamp, tz=self.tz_ny)
        hour   = dt_ny.hour
        minute = dt_ny.minute
        # 换算为分钟数方便比较
        hhmm = hour * 60 + minute

        # COMEX 维护: 17:00 – 18:00 ET
        if 17 * 60 <= hhmm < 18 * 60:
            return self.profiles[MarketSession.MAINTENANCE]
        # 欧美重叠高峰: 08:20 – 13:30 ET
        elif 8 * 60 + 20 <= hhmm < 13 * 60 + 30:
            return self.profiles[MarketSession.EURO_US]
        # 美盘下午: 13:30 – 17:00 ET
        elif 13 * 60 + 30 <= hhmm < 17 * 60:
            return self.profiles[MarketSession.US_AFTERNOON]
        # 亚盘: 18:00 – 次日 08:20 ET
        else:
            return self.profiles[MarketSession.ASIAN]