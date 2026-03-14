# intraday/dashboard/app.py
"""
Intraday 实时看板（Streamlit + DuckDB 读 Parquet 快照）
======================================================
启动方式:
    streamlit run intraday/dashboard/app.py
远程服务器:
    streamlit run intraday/dashboard/app.py \\
        --server.port 8501 --server.address 0.0.0.0 --server.headless true
"""
from __future__ import annotations

import datetime
import math
from pathlib import Path
import time

import duckdb
import pandas as pd
import streamlit as st

# 自动读取服务器系统时区
LOCAL_TZ = datetime.datetime.now().astimezone().tzinfo

# ═══════════════════════════════════════════════════════════════════════════════
# 页面配置
# ═══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Intraday Quant Monitor",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stMetric"] { background:#111827; border-radius:8px; padding:10px 14px; }
[data-testid="stMetricValue"] { font-size:1.4rem; font-weight:700; }
[data-testid="stMetricLabel"] { font-size:.75rem; color:#9ca3af; }
div[data-testid="stTabs"] button { font-size:.92rem; font-weight:600; }
.block-container { padding-top:1rem; }
table { width:100%; font-size:.85rem; }
td, th { padding: 3px 8px !important; }
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# 侧边栏配置
# ═══════════════════════════════════════════════════════════════════════════════
SNAP_DIR_DEFAULT = Path.home() / "results" / "snapshots"

with st.sidebar:
    st.markdown("## ⚙️ 控制面板")
    st.divider()

    snap_dir_input = st.text_input("📁 快照目录", str(SNAP_DIR_DEFAULT))
    SNAP_DIR = Path(snap_dir_input)
    PATH_WR  = SNAP_DIR / "snapshot_wr.parquet"
    PATH_PHY = SNAP_DIR / "snapshot_phy.parquet"

    st.divider()
    refresh_sec = st.slider("🔄 刷新间隔（秒）", 1, 30, 3)
    tail_rows   = st.slider("📋 加载最新 N 行", 50, 1000, 200, step=50)

    st.divider()
    wr_exists  = PATH_WR.exists()
    phy_exists = PATH_PHY.exists()
    st.markdown(
        f"{'🟢' if wr_exists  else '🔴'} window_results  \n"
        f"{'🟢' if phy_exists else '🔴'} physics_stats"
    )
    if wr_exists:
        age = time.time() - PATH_WR.stat().st_mtime
        st.caption(f"快照更新于 {age:.0f}s 前")

# ═══════════════════════════════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=3, show_spinner=False)
def load_wr(path: str, n: int) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame()
    df = duckdb.query(
        f"SELECT * FROM read_parquet('{path}') ORDER BY ts ASC LIMIT {n}"
    ).df()
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(LOCAL_TZ)
    return df

@st.cache_data(ttl=3, show_spinner=False)
def load_phy(path: str, n: int) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame()
    df = duckdb.query(
        f"SELECT * FROM read_parquet('{path}') ORDER BY ts ASC LIMIT {n}"
    ).df()
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(LOCAL_TZ)
    return df

wr  = load_wr(str(PATH_WR),  tail_rows)
phy = load_phy(str(PATH_PHY), tail_rows)

# ═══════════════════════════════════════════════════════════════════════════════
# 等待数据
# ═══════════════════════════════════════════════════════════════════════════════
if wr.empty:
    st.markdown("## 📈 Intraday Quant Monitor")
    st.warning(
        f"⏳ **等待快照文件生成**，确认主引擎正在运行。\n\n"
        f"预期路径：`{PATH_WR}`"
    )
    time.sleep(refresh_sec)
    st.rerun()

symbols = sorted(wr["symbol"].unique().tolist())

# ═══════════════════════════════════════════════════════════════════════════════
# 工具函数（对应 terminal_rich 的颜色逻辑）
# ═══════════════════════════════════════════════════════════════════════════════
def _erfc_approx(x: float) -> float:
    if x < 0:
        return 2.0 - _erfc_approx(-x)
    t = 1.0 / (1.0 + 0.3275911 * x)
    p = t * (0.254829592 + t * (-0.284496736 + t * (
        1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    return p * math.exp(-x * x)

def p_up(mean: float, std: float) -> float:
    if std <= 0:
        return 0.5
    z = -mean / std
    return 0.5 * _erfc_approx(z / math.sqrt(2))

def impact_color(v: float) -> str:
    return "🟢" if v < 2 else ("🟡" if v < 5 else "🔴")

def levels_color(v: int) -> str:
    return "🟢" if v <= 2 else ("🟡" if v <= 5 else "🔴")

def delta_color(v: int) -> str:
    return "🟢 +" if v >= 0 else "🔴 "

def kurt_label(v: float) -> str:
    if v > 2.0:   return f"{v:+.4f}  ⚠ 厚尾"
    if v > 0.5:   return f"{v:+.4f}  轻厚尾"
    if v < -0.5:  return f"{v:+.4f}  薄尾"
    return f"{v:+.4f}  ≈正态"

def skew_label(v: float) -> str:
    if v > 0.5:   return f"{v:+.4f}  右偏"
    if v < -0.5:  return f"{v:+.4f}  左偏"
    return f"{v:+.4f}  对称"

def lr_label(v: float) -> str:
    if v >= 2.0:  return f"{v:.2f}x  ▲ 高"
    if v >= 1.2:  return f"{v:.2f}x  正常"
    return f"{v:.2f}x  低"

# ═══════════════════════════════════════════════════════════════════════════════
# 标题
# ═══════════════════════════════════════════════════════════════════════════════
ts_latest = wr["ts"].iloc[-1]
lag = time.time() - ts_latest
dt_str = pd.to_datetime(ts_latest, unit="s", utc=True).tz_convert(LOCAL_TZ).strftime("%H:%M:%S %Z")

hcol1, hcol2 = st.columns([5, 1])
with hcol1:
    st.markdown("## 📈 Intraday Quant Monitor")
with hcol2:
    st.metric("数据延迟", f"{lag:.1f}s",
              delta="正常" if lag < 10 else "滞后",
              delta_color="normal" if lag < 10 else "inverse")

st.caption(f"最新快照时间: {dt_str}  ·  品种: {' / '.join(symbols)}")
st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# 两大 Tab
# ═══════════════════════════════════════════════════════════════════════════════
tab_live, tab_raw = st.tabs(["📊 实时监控", "🗄️ 原始数据 & 下载"])

# ───────────────────────────────────────────────────────────────────────────────
# TAB 1：实时监控（对应 terminal_rich 布局）
# ───────────────────────────────────────────────────────────────────────────────
with tab_live:

    # ── 每标的一个子 Tab，充分利用横向空间 ───────────────────────────────────
    sym_tabs = st.tabs([f"🔹 {sym}" for sym in symbols])

    for sym_tab, sym in zip(sym_tabs, symbols):
        with sym_tab:
            latest_wr  = wr[wr["symbol"] == sym].iloc[-1]  if not wr[wr["symbol"] == sym].empty  else None
            latest_phy = phy[phy["symbol"] == sym].iloc[-1] if not phy.empty and not phy[phy["symbol"] == sym].empty else None

            # ── 左右两列：实时指标 | φ(ΔP) ────────────────────────────────
            mc, dc = st.columns(2)

            # ── 实时指标面板 ────────────────────────────────────────────────
            with mc:
                st.markdown(f"#### 📊 {sym} 实时指标")
                if latest_wr is not None:
                    w = latest_wr
                    time_label = pd.to_datetime(w["ts"], unit="s", utc=True).tz_convert(LOCAL_TZ).strftime("%H:%M:%S")
                    pl  = int(w["price_levels"])
                    buy = int(w["buy_volume"])
                    sel = int(w["sell_volume"])
                    tot = int(w["total_volume"])
                    dlt = int(w["delta"])

                    rows = [
                        ("时间",       f"`{time_label}`",                  ""),
                        ("VWAP",       f"`{w['vwap']:.2f}`",               ""),
                        ("区间",       f"`{w['low_price']:.2f} ~ {w['high_price']:.2f}`", ""),
                        ("价格离散度", f"{levels_color(pl)} `{pl} 层`",    ""),
                        ("冲击成本",   f"{impact_color(w['impact_bps'])} `{w['impact_bps']:.2f} bps`", ""),
                        ("每手冲击",   f"`${w['impact_dollar']:.2f}`",      ""),
                        ("成交量",     f"`{tot} 手`",                       ""),
                        ("Tick 数",    f"`{int(w['tick_count'])}`",          ""),
                        ("Delta",      f"{delta_color(dlt)}`{abs(dlt)}`",   ""),
                        ("买 / 卖",    f"`{buy} / {sel}`",                  ""),
                    ]
                    md = "| 指标 | 值 |\n|---|---|\n"
                    for label, val, _ in rows:
                        md += f"| {label} | {val} |\n"
                    st.markdown(md)
                else:
                    st.info(f"等待 {sym} 第一个窗口…")

            # ── φ(ΔP) 物理统计面板 ──────────────────────────────────────────
            with dc:
                st.markdown(f"#### ⚛️ {sym} φ(ΔP) 密度")
                if latest_phy is not None:
                    p = latest_phy
                    pu = p_up(float(p["mean"]), float(p["std"]))

                    rows_phy = [
                        ("均值 μ",      f"`{p['mean']:+.5f}`",              "E[ΔP]"),
                        ("标准差 σ",    f"`{p['std']:.5f}`",                "波动幅度"),
                        ("偏度 γ₁",    f"`{skew_label(float(p['skewness']))}`", ""),
                        ("超额峰度 γ₂",f"`{kurt_label(float(p['kurtosis']))}`", ""),
                        ("95% CI",     f"`[{p['ci_lo']:+.5f}, {p['ci_hi']:+.5f}]`", ""),
                    ]

                    if pd.notna(p.get("k_effective")):
                        rows_phy += [
                            ("─────", "─────", ""),
                            ("衰减 k",   f"`{p['k_effective']:.5f}`",  f"半衰={p['half_life_sec']:.0f}s"),
                            ("有效覆盖", f"`{p['coverage_sec']:.0f}s`", "基准300s"),
                            ("流动性倍", f"`{lr_label(float(p['liquidity_ratio']))}`", "动态梯度"),
                            ("等效样本", f"`{p['eff_n']:.1f}`",         "Σwᵢ"),
                        ]

                    rows_phy += [
                        ("─────", "─────", ""),
                        ("P(ΔP>0)", f"`{pu:.1%}`", "上涨概率"),
                    ]

                    md = "| 参数 | 值 | 说明 |\n|---|---|---|\n"
                    for label, val, note in rows_phy:
                        md += f"| {label} | {val} | {note} |\n"
                    st.markdown(md)
                else:
                    st.info(f"{sym} ΔP 样本积累中…")

    st.divider()

    # ── 底部：窗口历史表（混排，时间倒序）────────────────────────────────────
    st.markdown("#### 🕐 窗口历史（最新在上）")

    hist = wr.sort_values("ts", ascending=False).copy()
    hist["时间"]    = pd.to_datetime(hist["ts"], unit="s", utc=True).dt.tz_convert(LOCAL_TZ).dt.strftime("%H:%M:%S")
    hist["标的"]    = hist["symbol"]
    hist["VWAP"]    = hist["vwap"].map("{:.2f}".format)
    hist["离散度"]  = hist["price_levels"].astype(int)
    hist["冲击bps"] = hist["impact_bps"].map("{:.2f}".format)
    hist["成交量"]  = hist["total_volume"].astype(int)
    hist["Ticks"]   = hist["tick_count"].astype(int)
    hist["Delta"]   = hist["delta"].map("{:+d}".format)
    hist["买/卖"]   = hist["buy_volume"].astype(str) + "/" + hist["sell_volume"].astype(str)
    hist["区间"]    = hist["low_price"].map("{:.2f}".format) + "~" + hist["high_price"].map("{:.2f}".format)

    disp_hist = hist[["时间", "标的", "VWAP", "离散度", "冲击bps",
                       "成交量", "Ticks", "Delta", "买/卖", "区间"]]
    st.dataframe(disp_hist, use_container_width=True, height=380, hide_index=True)

# ───────────────────────────────────────────────────────────────────────────────
# TAB 2：原始数据 & 下载
# ───────────────────────────────────────────────────────────────────────────────
with tab_raw:
    raw_left, raw_right = st.columns(2)

    _WR_COLS  = ["dt", "symbol", "session", "vwap", "high_price", "low_price",
                 "total_volume", "tick_count", "price_levels", "price_range_abs",
                 "impact_bps", "impact_dollar", "delta_ratio",
                 "buy_volume", "sell_volume", "delta"]
    _PHY_COLS = ["dt", "symbol", "delta_p", "mean", "std",
                 "skewness", "kurtosis", "k_effective",
                 "half_life_sec", "coverage_sec", "liquidity_ratio", "eff_n"]

    with raw_left:
        st.markdown("### 🪟 window_results")
        wr_disp = wr[[c for c in _WR_COLS if c in wr.columns]].sort_values("dt", ascending=False)
        st.dataframe(wr_disp, use_container_width=True, height=520)
        st.download_button(
            "⬇️ 下载 window_results CSV",
            data=wr_disp.to_csv(index=False).encode(),
            file_name=f"window_results_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )

    with raw_right:
        st.markdown("### ⚛️ physics_stats")
        if not phy.empty:
            phy_disp = phy[[c for c in _PHY_COLS if c in phy.columns]].sort_values("dt", ascending=False)
            st.dataframe(phy_disp, use_container_width=True, height=520)
            st.download_button(
                "⬇️ 下载 physics_stats CSV",
                data=phy_disp.to_csv(index=False).encode(),
                file_name=f"physics_stats_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
            )
        else:
            st.info("physics_stats 尚无数据")

# ═══════════════════════════════════════════════════════════════════════════════
# 底部 + 自动刷新
# ═══════════════════════════════════════════════════════════════════════════════
st.divider()
st.caption(
    f"🕐 刷新时间: **{pd.Timestamp.now().strftime('%H:%M:%S')}**  "
    f"· 每 {refresh_sec}s 自动更新  "
    f"· 快照: `{PATH_WR}`"
)
time.sleep(refresh_sec)
st.rerun()
