from dataclasses import dataclass
from typing import Optional

@dataclass
class ProductConfig:
    name: str
    symbol: str
    tick_size: float
    ref_price: float
    contract_multiplier: int
    unit: str
    exchange: str
    currency: str = "USD"

    # ── 信号阈值 ─────────────────────────────────────────
    impact_warn_bps:   float = 3.0    # 冲击成本警告线 (bps)
    impact_alert_bps:  float = 8.0    # 冲击成本告警线 (bps)
    kurt_warn:         float = 2.0    # 超额峰度警告 (厉尾开始)
    kurt_alert:        float = 5.0    # 超额峰度告警 (极度厉尾)
    delta_imbal_warn:  float = 0.65   # 买卖比警告线 (>65%偏买 / <35%偏卖)
    volume_surge_x:    float = 3.0    # 成交量均值倍数触发异常

# 1. 微黄金 (1手 = 10盎司)
MGC_CONFIG = ProductConfig(
    name="Micro Gold", symbol="MGC",
    tick_size=0.10, ref_price=2900.0, contract_multiplier=10,
    unit="$/oz", exchange="COMEX",
    impact_warn_bps=3.0, impact_alert_bps=8.0,
    kurt_warn=2.0, kurt_alert=5.0,
    delta_imbal_warn=0.65, volume_surge_x=3.0,
)

# 2. 标准黄金 (1手 = 100盎司)
GC_CONFIG = ProductConfig(
    name="Gold", symbol="GC",
    tick_size=0.10, ref_price=2900.0, contract_multiplier=100,
    unit="$/oz", exchange="COMEX",
    impact_warn_bps=2.0, impact_alert_bps=6.0,
    kurt_warn=2.0, kurt_alert=5.0,
    delta_imbal_warn=0.65, volume_surge_x=3.0,
)

# 3. 微标普 (1手 = $5 x 指数)
MES_CONFIG = ProductConfig(
    name="Micro E-mini S&P 500", symbol="MES",
    tick_size=0.25, ref_price=6000.0, contract_multiplier=5,
    unit="pts", exchange="CME",
    impact_warn_bps=2.0, impact_alert_bps=5.0,
    kurt_warn=2.0, kurt_alert=5.0,
    delta_imbal_warn=0.65, volume_surge_x=3.0,
)

# 4. 微纳指 (1手 = $2 x 指数)
MNQ_CONFIG = ProductConfig(
    name="Micro E-mini Nasdaq 100", symbol="MNQ",
    tick_size=0.25, ref_price=21000.0, contract_multiplier=2,
    unit="pts", exchange="CME",
    impact_warn_bps=2.0, impact_alert_bps=5.0,
    kurt_warn=2.0, kurt_alert=5.0,
    delta_imbal_warn=0.65, volume_surge_x=3.0,
)

# 5. 标准标普 E-mini (1手 = $50 x 指数)
ES_CONFIG = ProductConfig(
    name="E-mini S&P 500", symbol="ES",
    tick_size=0.25, ref_price=6000.0, contract_multiplier=50,
    unit="pts", exchange="CME",
    impact_warn_bps=1.5, impact_alert_bps=4.0,
    kurt_warn=2.0, kurt_alert=5.0,
    delta_imbal_warn=0.65, volume_surge_x=3.0,
)

# 6. 标准纳指 E-mini (1手 = $20 x 指数)
NQ_CONFIG = ProductConfig(
    name="E-mini Nasdaq 100", symbol="NQ",
    tick_size=0.25, ref_price=21000.0, contract_multiplier=20,
    unit="pts", exchange="CME",
    impact_warn_bps=1.5, impact_alert_bps=4.0,
    kurt_warn=2.0, kurt_alert=5.0,
    delta_imbal_warn=0.65, volume_surge_x=3.0,
)