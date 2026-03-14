#!/usr/bin/env python3
# intraday/query.py
"""
DuckDB 数据查询脚本
===================
用法:
    python query.py                         # 交互菜单
    python query.py --tail 20               # 最新 20 条窗口记录
    python query.py --symbol ES --tail 50   # ES 最新 50 条
    python query.py --summary              # 今日各标的汇总
    python query.py --export 20260219      # 导出指定日期 Parquet
    python query.py --sql "SELECT ..."     # 自定义 SQL

数据库默认路径: ~/results/intraday.duckdb
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── 路径设置 ──────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

DEFAULT_DB = str(Path.home() / "results" / "intraday.duckdb")


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _connect(db_path: str):
    import duckdb
    if not Path(db_path).exists():
        print(f"❌ 数据库不存在: {db_path}")
        sys.exit(1)
    # 尝试只读连接；若主程序持有写锁则降级为复制内存方式读取
    try:
        return duckdb.connect(db_path, read_only=True)
    except duckdb.IOException:
        # 主程序运行中：将数据库复制到临时文件后只读连接，避免锁冲突
        import shutil, tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False)
        tmp.close()
        shutil.copy2(db_path, tmp.name)
        print(f"⚠️  主程序运行中，已复制快照: {tmp.name}")
        return duckdb.connect(tmp.name, read_only=True)


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M:%S")


def _print_table(rows, headers: list[str]) -> None:
    if not rows:
        print("  (无数据)")
        return
    # 计算列宽
    widths = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        sr = [str(v) if v is not None else "–" for v in row]
        str_rows.append(sr)
        for i, v in enumerate(sr):
            widths[i] = max(widths[i], len(v))

    sep   = "  ".join("-" * w for w in widths)
    hdr   = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(hdr)
    print(sep)
    for sr in str_rows:
        print("  ".join(v.ljust(widths[i]) for i, v in enumerate(sr)))


# ── 各查询函数 ────────────────────────────────────────────────────────────────

def cmd_summary(conn, date_str: str = None) -> None:
    """今日（或指定日期）各标的汇总统计"""
    date_str = date_str or datetime.now().strftime("%Y%m%d")
    print(f"\n═══ 日内汇总  {date_str} ════════════════════════════════════════")

    # 窗口汇总
    rows = conn.execute(f"""
        SELECT
            symbol,
            count(*)                          AS windows,
            sum(total_volume)                 AS total_vol,
            round(avg(vwap), 2)               AS avg_vwap,
            round(min(low_price), 2)          AS day_low,
            round(max(high_price), 2)         AS day_high,
            round(avg(impact_bps), 3)         AS avg_impact_bps,
            round(avg(price_levels), 1)       AS avg_levels
        FROM window_results
        WHERE strftime(dt, '%Y%m%d') = '{date_str}'
        GROUP BY symbol
        ORDER BY symbol
    """).fetchall()
    print("\n▸ 窗口汇总 (window_results)")
    _print_table(rows, ["symbol", "windows", "total_vol", "avg_vwap",
                        "day_low", "day_high", "avg_impact_bps", "avg_levels"])

    # 物理统计汇总
    rows2 = conn.execute(f"""
        SELECT
            symbol,
            count(*)                          AS rows,
            round(avg(mean), 6)               AS avg_mean,
            round(avg(std), 5)                AS avg_std,
            round(avg(kurtosis), 3)           AS avg_kurt,
            round(avg(k_effective), 5)        AS avg_k,
            round(avg(liquidity_ratio), 2)    AS avg_liq_ratio,
            round(avg(coverage_sec), 1)       AS avg_coverage_s
        FROM physics_stats
        WHERE strftime(dt, '%Y%m%d') = '{date_str}'
        GROUP BY symbol
        ORDER BY symbol
    """).fetchall()
    print("\n▸ 衰减统计汇总 (physics_stats)")
    _print_table(rows2, ["symbol", "rows", "avg_mean", "avg_std",
                         "avg_kurt", "avg_k", "avg_liq_ratio", "avg_coverage_s"])


def cmd_tail_window(conn, n: int = 20, symbol: str = None) -> None:
    """最新 N 条窗口记录"""
    where = f"WHERE symbol = '{symbol}'" if symbol else ""
    sym_label = symbol or "ALL"
    print(f"\n═══ 最新 {n} 条窗口记录  [{sym_label}] ═══════════════════════════")

    rows = conn.execute(f"""
        SELECT
            strftime(dt, '%H:%M:%S')  AS time,
            symbol,
            session,
            round(vwap, 2)            AS vwap,
            total_volume              AS vol,
            tick_count                AS ticks,
            price_levels              AS levels,
            round(impact_bps, 3)      AS imp_bps,
            delta,
            round(delta_ratio, 3)     AS d_ratio
        FROM window_results
        {where}
        ORDER BY ts DESC
        LIMIT {n}
    """).fetchall()
    _print_table(rows, ["time", "sym", "session", "vwap", "vol",
                        "ticks", "levels", "imp_bps", "delta", "d_ratio"])


def cmd_tail_physics(conn, n: int = 20, symbol: str = None) -> None:
    """最新 N 条物理统计记录"""
    where = f"WHERE symbol = '{symbol}'" if symbol else ""
    sym_label = symbol or "ALL"
    print(f"\n═══ 最新 {n} 条衰减统计  [{sym_label}] ══════════════════════════")

    rows = conn.execute(f"""
        SELECT
            strftime(dt, '%H:%M:%S') AS time,
            symbol,
            round(delta_p, 4)        AS delta_p,
            round(mean, 6)           AS mean,
            round(std, 5)            AS std,
            round(skewness, 3)       AS skew,
            round(kurtosis, 3)       AS kurt,
            round(k_effective, 5)    AS k_eff,
            round(half_life_sec, 1)  AS half_s,
            round(coverage_sec, 1)   AS cov_s,
            round(liquidity_ratio,2) AS liq_x,
            round(eff_n, 1)          AS eff_n
        FROM physics_stats
        {where}
        ORDER BY ts DESC
        LIMIT {n}
    """).fetchall()
    _print_table(rows, ["time", "sym", "delta_p", "mean", "std",
                        "skew", "kurt", "k_eff", "half_s", "cov_s", "liq_x", "eff_n"])


def cmd_tail_risk(conn, symbol: str = None) -> None:
    """厚尾风险时段：kurt > 3 的窗口"""
    where_sym = f"AND p.symbol = '{symbol}'" if symbol else ""
    print(f"\n═══ 厚尾风险时段 (kurt > 3) ════════════════════════════════════")

    rows = conn.execute(f"""
        SELECT
            strftime(p.dt, '%m-%d %H:%M:%S') AS time,
            p.symbol,
            round(p.kurtosis, 2)       AS kurt,
            round(p.skewness, 3)       AS skew,
            round(p.std, 5)            AS std,
            round(p.k_effective, 5)    AS k_eff,
            round(p.coverage_sec, 1)   AS cov_s,
            round(w.impact_bps, 3)     AS imp_bps,
            w.price_levels             AS levels,
            w.total_volume             AS vol
        FROM physics_stats p
        JOIN window_results w
          ON abs(p.ts - w.ts) < 1 AND p.symbol = w.symbol
        WHERE p.kurtosis > 3
        {where_sym}
        ORDER BY p.kurtosis DESC
        LIMIT 30
    """).fetchall()
    _print_table(rows, ["time", "sym", "kurt", "skew", "std",
                        "k_eff", "cov_s", "imp_bps", "levels", "vol"])


def cmd_hourly(conn, symbol: str = None, date_str: str = None) -> None:
    """按小时聚合：成交量 / 冲击成本 / 流动性均值"""
    date_str = date_str or datetime.now().strftime("%Y%m%d")
    where_sym = f"AND symbol = '{symbol}'" if symbol else ""
    sym_label = symbol or "ALL"
    print(f"\n═══ 小时聚合  {date_str}  [{sym_label}] ═══════════════════════")

    rows = conn.execute(f"""
        SELECT
            symbol,
            strftime(dt, '%H:00')           AS hour,
            count(*)                         AS windows,
            sum(total_volume)               AS vol,
            round(avg(vwap), 2)             AS avg_vwap,
            round(avg(impact_bps), 3)       AS avg_imp_bps,
            round(avg(price_levels), 1)     AS avg_levels,
            sum(delta)                      AS net_delta
        FROM window_results
        WHERE strftime(dt, '%Y%m%d') = '{date_str}'
        {where_sym}
        GROUP BY symbol, hour
        ORDER BY symbol, hour
    """).fetchall()
    _print_table(rows, ["symbol", "hour", "windows", "vol",
                        "avg_vwap", "avg_imp_bps", "avg_levels", "net_delta"])


def cmd_decay_trend(conn, symbol: str, hours: int = 2) -> None:
    """指定标的近 N 小时的衰减系数 k 趋势"""
    print(f"\n═══ {symbol}  衰减系数 k 趋势（最近 {hours} 小时）════════════")

    rows = conn.execute(f"""
        SELECT
            strftime(dt, '%H:%M:%S')  AS time,
            round(k_effective, 5)     AS k_eff,
            round(half_life_sec, 1)   AS half_s,
            round(coverage_sec, 1)    AS cov_s,
            round(liquidity_ratio, 2) AS liq_x,
            round(eff_n, 1)           AS eff_n,
            round(kurtosis, 3)        AS kurt
        FROM physics_stats
        WHERE symbol = '{symbol}'
          AND dt >= now() - INTERVAL {hours} HOUR
        ORDER BY ts DESC
        LIMIT 120
    """).fetchall()
    _print_table(rows, ["time", "k_eff", "half_s", "cov_s", "liq_x", "eff_n", "kurt"])


def cmd_vacuum(conn, symbol: str = None) -> None:
    """流动性真空：成交量极低但价格离散度高的窗口"""
    where_sym = f"AND symbol = '{symbol}'" if symbol else ""
    print(f"\n═══ 流动性真空时段 (vol < 5 & levels >= 3) ════════════════════")

    rows = conn.execute(f"""
        SELECT
            strftime(dt, '%m-%d %H:%M:%S') AS time,
            symbol,
            total_volume  AS vol,
            tick_count    AS ticks,
            price_levels  AS levels,
            round(impact_bps, 3)   AS imp_bps,
            round(vwap, 2)         AS vwap,
            delta
        FROM window_results
        WHERE total_volume < 5 AND price_levels >= 3
        {where_sym}
        ORDER BY ts DESC
        LIMIT 30
    """).fetchall()
    _print_table(rows, ["time", "sym", "vol", "ticks",
                        "levels", "imp_bps", "vwap", "delta"])


def cmd_row_count(conn) -> None:
    """数据库行数统计"""
    wr  = conn.execute("SELECT count(*) FROM window_results").fetchone()[0]
    phy = conn.execute("SELECT count(*) FROM physics_stats").fetchone()[0]
    db_range = conn.execute(
        "SELECT min(dt), max(dt) FROM window_results"
    ).fetchone()
    print(f"\n═══ 数据库统计 ══════════════════════════════════════════════════")
    print(f"  window_results : {wr:,} 行")
    print(f"  physics_stats  : {phy:,} 行")
    if db_range[0]:
        print(f"  时间范围       : {db_range[0]}  →  {db_range[1]}")


def cmd_export(conn_rw, date_str: str) -> None:
    """导出指定日期 Parquet（需要可写连接）"""
    import duckdb
    from pathlib import Path as P
    out_dir = P.home() / "results" / "parquet"
    out_dir.mkdir(parents=True, exist_ok=True)
    for table, prefix in (("window_results", "wr"), ("physics_stats", "phy")):
        out = str(out_dir / f"{prefix}_{date_str}.parquet")
        conn_rw.execute(f"""
            COPY (
                SELECT * FROM {table}
                WHERE strftime(dt, '%Y%m%d') = '{date_str}'
            ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        print(f"  ✅ {table} → {out}")


def cmd_custom_sql(conn, sql: str) -> None:
    """执行自定义 SQL"""
    print(f"\n═══ 自定义查询 ══════════════════════════════════════════════════")
    try:
        result = conn.execute(sql)
        rows = result.fetchall()
        if result.description:
            headers = [d[0] for d in result.description]
            _print_table(rows, headers)
        else:
            print(f"  影响行数: {len(rows)}")
    except Exception as e:
        print(f"  ❌ SQL 错误: {e}")


def interactive_menu(db_path: str) -> None:
    """交互式查询菜单"""
    conn = _connect(db_path)
    cmd_row_count(conn)

    while True:
        print("""
┌─ 查询菜单 ──────────────────────────────────┐
│  1  今日汇总（各标的）                        │
│  2  最新窗口记录                              │
│  3  最新衰减统计                              │
│  4  厚尾风险时段                              │
│  5  小时聚合                                  │
│  6  衰减系数 k 趋势                           │
│  7  流动性真空时段                            │
│  8  自定义 SQL                                │
│  0  退出                                      │
└─────────────────────────────────────────────┘""")
        choice = input("选择: ").strip()
        if choice == "0":
            break
        elif choice == "1":
            d = input("日期 (YYYYMMDD, 回车=今天): ").strip() or None
            cmd_summary(conn, d)
        elif choice == "2":
            sym = input("标的 (回车=全部): ").strip() or None
            n   = int(input("条数 (回车=20): ").strip() or "20")
            cmd_tail_window(conn, n, sym)
        elif choice == "3":
            sym = input("标的 (回车=全部): ").strip() or None
            n   = int(input("条数 (回车=20): ").strip() or "20")
            cmd_tail_physics(conn, n, sym)
        elif choice == "4":
            sym = input("标的 (回车=全部): ").strip() or None
            cmd_tail_risk(conn, sym)
        elif choice == "5":
            sym = input("标的 (回车=全部): ").strip() or None
            d   = input("日期 (YYYYMMDD, 回车=今天): ").strip() or None
            cmd_hourly(conn, sym, d)
        elif choice == "6":
            sym = input("标的 (必填): ").strip()
            h   = int(input("小时数 (回车=2): ").strip() or "2")
            if sym:
                cmd_decay_trend(conn, sym, h)
        elif choice == "7":
            sym = input("标的 (回车=全部): ").strip() or None
            cmd_vacuum(conn, sym)
        elif choice == "8":
            sql = input("SQL: ").strip()
            if sql:
                cmd_custom_sql(conn, sql)

    conn.close()


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Intraday DuckDB 查询工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db",     default=DEFAULT_DB,  help="DuckDB 文件路径")
    parser.add_argument("--symbol", default=None,        help="过滤标的 (GC/ES/NQ)")
    parser.add_argument("--tail",   type=int, default=0, help="最新 N 条窗口记录")
    parser.add_argument("--physics",type=int, default=0, help="最新 N 条物理统计")
    parser.add_argument("--summary",action="store_true", help="今日汇总")
    parser.add_argument("--hourly", action="store_true", help="小时聚合")
    parser.add_argument("--risk",   action="store_true", help="厚尾风险时段")
    parser.add_argument("--vacuum", action="store_true", help="流动性真空时段")
    parser.add_argument("--decay",  type=int, default=0, help="衰减 k 趋势（小时数）")
    parser.add_argument("--count",  action="store_true", help="行数统计")
    parser.add_argument("--export", default=None,        help="导出 Parquet (YYYYMMDD)")
    parser.add_argument("--sql",    default=None,        help="自定义 SQL")
    parser.add_argument("--date",   default=None,        help="指定日期 YYYYMMDD")

    args = parser.parse_args()

    # 无参数 → 进入交互菜单
    flags = [args.tail, args.physics, args.summary, args.hourly,
             args.risk, args.vacuum, args.decay, args.count,
             args.export, args.sql]
    if not any(flags):
        interactive_menu(args.db)
        return

    # 批量命令模式（导出需要可写连接）
    if args.export:
        import duckdb
        conn = duckdb.connect(args.db)
        print(f"\n═══ 导出 Parquet  {args.export} ═══════════════════════")
        cmd_export(conn, args.export)
        conn.close()
        return

    conn = _connect(args.db)

    if args.count:
        cmd_row_count(conn)
    if args.summary:
        cmd_summary(conn, args.date)
    if args.tail:
        cmd_tail_window(conn, args.tail, args.symbol)
    if args.physics:
        cmd_tail_physics(conn, args.physics, args.symbol)
    if args.risk:
        cmd_tail_risk(conn, args.symbol)
    if args.hourly:
        cmd_hourly(conn, args.symbol, args.date)
    if args.decay:
        sym = args.symbol or "ES"
        cmd_decay_trend(conn, sym, args.decay)
    if args.vacuum:
        cmd_vacuum(conn, args.symbol)
    if args.sql:
        cmd_custom_sql(conn, args.sql)

    conn.close()


if __name__ == "__main__":
    main()
