#!/usr/bin/env python3
"""
Amazon 选品数据查询引擎
支持自然语言风格提问，查询 SQLite 历史数据

用法：
  python3 query_engine.py --ask "水杯最大的三个痛点是什么"
  python3 query_engine.py --ask "比较 us 和 de 站的均价"
  python3 query_engine.py --ask "过去30天哪些品类机会最大"
  python3 query_engine.py --ask "列出所有调研过的品类"
  python3 query_engine.py --ask "uk laptop bag 的评分分布"
  python3 query_engine.py --dashboard  # 打印完整仪表盘
"""

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from storage import DEFAULT_DB_PATH, connect


def parse_price(p):
    if not p:
        return None
    m = re.search(r"[\d,.]+", str(p))
    if m:
        try:
            return float(m.group().replace(",", ""))
        except ValueError:
            pass
    return None


def parse_rating(r):
    if not r:
        return None
    m = re.search(r"(\d+\.?\d*)", str(r))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def query_db(conn, ask: str) -> str:
    """解析自然语言提问，执行 SQL 返回结果"""
    ask_lower = ask.lower().strip()
    days_match = re.search(r"(\d+)\s*天", ask)
    days = int(days_match.group(1)) if days_match else 30

    # 提取关键词
    keyword = None
    for kw in ["water bottle", "laptop bag", "dumbbell", "socks", "trinkflasche",
               "水杯", "袜子", "电脑包", "哑铃"]:
        if kw in ask_lower:
            keyword = kw
            break

    # 提取地区
    region = None
    for r in ["us", "uk", "de", "jp", "fr", "it", "es", "ca", "in", "au", "mx", "br", "nl"]:
        if r in ask_lower.split() or r in ask_lower:
            # 确保是独立的地区代码
            if re.search(rf"\b{r}\b", ask_lower):
                region = r
                break

    since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days * 86400))

    lines = []

    # ── 品类机会排名 ──
    if any(w in ask_lower for w in ["机会", "opportunity", "蓝海", "哪个品类", "哪些品类", "品类排名"]):
        rows = conn.execute(
            """SELECT keyword, region, avg_price, avg_rating, product_count,
                      top_3_pain_points, opportunity_score, captured_at
               FROM category_summary
               WHERE captured_at >= ?
               ORDER BY opportunity_score DESC LIMIT 10""",
            (since,),
        ).fetchall()
        if rows:
            lines.append(f"\n📊 近{days}天品类机会排名（机会分越高，市场空白越大）：\n")
            for i, r in enumerate(rows, 1):
                pains = json.loads(r["top_3_pain_points"] or "[]")
                pain_str = "、".join(f"{p[0]}({p[1]})" for p in pains[:2])
                lines.append(
                    f"  #{i} {r['keyword']} ({r['region']}) "
                    f"| 均价${r['avg_price']:.0f} | 评分{r['avg_rating']:.1f}★ "
                    f"| 机会分{r['opportunity_score']}"
                )
                if pain_str:
                    lines.append(f"     Top痛点: {pain_str}")
        else:
            lines.append("暂无品类数据，请先运行 pipeline 并保存数据。")

    # ── 痛点查询 ──
    elif any(w in ask_lower for w in ["痛点", "pain", "差评", "问题"]):
        where = ["captured_at >= ?"]
        params = [since]
        if keyword:
            where.append("keyword LIKE ?")
            params.append(f"%{keyword}%")
        if region:
            where.append("region = ?")
            params.append(region)

        rows = conn.execute(
            f"""SELECT keyword, region, top_3_pain_points, avg_rating, captured_at
                FROM category_summary
                WHERE {' AND '.join(where)}
                ORDER BY captured_at DESC LIMIT 5""",
            params,
        ).fetchall()

        if rows:
            label = f"{keyword or '全品类'} ({region or '全部地区'})"
            lines.append(f"\n🔍 {label} 近{days}天高频痛点：\n")
            for r in rows:
                pains = json.loads(r["top_3_pain_points"] or "[]")
                pain_str = " | ".join(f"{p[0]}({p[1]}条)" for p in pains)
                lines.append(f"  {r['keyword']}({r['region']}) {r['captured_at'][:10]} | {pain_str}")
        else:
            lines.append(f"未找到 {keyword or ''} 的痛点数据。")

    # ── 价格对比 ──
    elif any(w in ask_lower for w in ["价格", "price", "均价", "比较", "对比"]):
        if keyword:
            rows = conn.execute(
                """SELECT region, avg_price, min_price, max_price, product_count, captured_at
                   FROM category_summary
                   WHERE keyword LIKE ? AND captured_at >= ?
                   ORDER BY captured_at DESC""",
                (f"%{keyword}%", since),
            ).fetchall()
            if rows:
                lines.append(f"\n💰 {keyword} 各地区价格对比：\n")
                lines.append(f"  {'地区':<6} {'均价':>8} {'最低':>8} {'最高':>8} {'产品数':>6}")
                for r in rows:
                    lines.append(
                        f"  {r['region']:<6} ${r['avg_price']:>7.0f} "
                        f"${r['min_price']:>7.0f} ${r['max_price']:>7.0f} {r['product_count']:>6}"
                    )

    # ── 品类列表 ──
    elif any(w in ask_lower for w in ["列表", "所有", "调研过", "品类", "哪些"]):
        rows = conn.execute(
            """SELECT DISTINCT keyword, COUNT(*) as cnt, MAX(captured_at) as last_run
               FROM category_summary GROUP BY keyword ORDER BY last_run DESC"""
        ).fetchall()
        if rows:
            lines.append(f"\n📋 已调研品类（共 {len(rows)} 个）：\n")
            for r in rows:
                lines.append(f"  {r['keyword']:<25s} {r['cnt']}次  最近: {r['last_run'][:10]}")
        else:
            lines.append("暂无调研数据。")

    # ── 评分分析 ──
    elif any(w in ask_lower for w in ["评分", "rating", "几星", "口碑"]):
        where = ["captured_at >= ?"]
        params = [since]
        if keyword:
            where.append("keyword LIKE ?")
            params.append(f"%{keyword}%")
        rows = conn.execute(
            f"""SELECT keyword, region, avg_rating, product_count, top_3_pain_points, captured_at
                FROM category_summary
                WHERE {' AND '.join(where)}
                ORDER BY avg_rating ASC LIMIT 10""",
            params,
        ).fetchall()
        if rows:
            lines.append(f"\n⭐ {keyword or '全品类'} 评分排名（从低到高）：\n")
            for r in rows:
                icon = "🔴" if r["avg_rating"] < 4.0 else ("🟡" if r["avg_rating"] < 4.3 else "🟢")
                lines.append(
                    f"  {icon} {r['keyword']}({r['region']}) "
                    f"{r['avg_rating']:.1f}★ | {r['product_count']}产品"
                )

    # ── 仪表盘 ──
    elif any(w in ask_lower for w in ["仪表盘", "dashboard", "总览", "全貌"]):
        return build_dashboard(conn)

    # ── 默认：智能摘要 ──
    else:
        # 给出最相关的数据概览
        if keyword:
            rows = conn.execute(
                """SELECT * FROM category_summary
                   WHERE keyword LIKE ? ORDER BY captured_at DESC LIMIT 1""",
                (f"%{keyword}%",),
            ).fetchall()
            if rows:
                r = rows[0]
                pains = json.loads(r["top_3_pain_points"] or "[]")
                pros = json.loads(r["top_3_pros"] or "[]")
                lines.append(f"\n📦 {r['keyword']} ({r['region']}) 最新调研摘要：")
                lines.append(f"  均价: ${r['avg_price']:.0f} (${r['min_price']:.0f}-${r['max_price']:.0f})")
                lines.append(f"  评分: {r['avg_rating']:.1f}★ | 产品: {r['product_count']}个 | 评论: {r['total_reviews']}条")
                lines.append(f"  机会分: {r['opportunity_score']}/100")
                if pains:
                    lines.append(f"  Top痛点: " + " | ".join(f"{p[0]}({p[1]})" for p in pains))
                if pros:
                    lines.append(f"  用户最爱: " + "、".join(pros))
                lines.append(f"  时间: {r['captured_at'][:10]}")
            else:
                lines.append(f"未找到 {keyword} 的调研数据，请先运行 pipeline。")
        else:
            lines.append("请提供更具体的查询，例如：")
            lines.append("  python3 query_engine.py --ask \"水杯的痛点\"")
            lines.append("  python3 query_engine.py --ask \"哪些品类机会最大\"")
            lines.append("  python3 query_engine.py --ask \"比较 us 和 de 的均价\"")
            lines.append("  python3 query_engine.py --dashboard")

    return "\n".join(lines)


def build_dashboard(conn) -> str:
    """生成完整仪表盘"""
    lines = []
    lines.append("=" * 65)
    lines.append("  📊 Amazon 选品数据中心")
    lines.append(f"  {time.strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 65)

    # 总览
    cats = conn.execute("SELECT COUNT(DISTINCT keyword) FROM category_summary").fetchone()[0]
    runs = conn.execute("SELECT COUNT(DISTINCT run_id) FROM category_summary").fetchone()[0]
    prods = conn.execute("SELECT COUNT(*) FROM product_snapshot").fetchone()[0]
    revs = conn.execute("SELECT COUNT(*) FROM review_snapshot").fetchone()[0]
    lines.append(f"\n  总品类: {cats} | 总调研: {runs}次 | 总产品: {prods} | 总评论: {revs}条")

    # 机会排名 Top 5
    lines.append(f"\n  🔥 机会最大品类 Top 5：")
    rows = conn.execute(
        """SELECT keyword, region, avg_price, avg_rating, opportunity_score, captured_at
           FROM category_summary ORDER BY opportunity_score DESC LIMIT 5"""
    ).fetchall()
    for i, r in enumerate(rows, 1):
        lines.append(
            f"    {i}. {r['keyword']}({r['region']}) "
            f"均价${r['avg_price']:.0f} 评分{r['avg_rating']:.1f}★ "
            f"机会分{r['opportunity_score']} ({r['captured_at'][:10]})"
        )

    # 各品类痛点统计
    lines.append(f"\n  🔍 全品类痛点 TOP 10：")
    all_pains = defaultdict(int)
    rows = conn.execute(
        "SELECT top_3_pain_points FROM category_summary WHERE captured_at >= ?",
        (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 90 * 86400)),),
    ).fetchall()
    for r in rows:
        for p in json.loads(r["top_3_pain_points"] or "[]"):
            all_pains[p[0]] += p[1]
    for kw, cnt in sorted(all_pains.items(), key=lambda x: -x[1])[:10]:
        bar = "█" * min(cnt, 30)
        lines.append(f"    {kw:<20s} {bar} {cnt}")

    # 地区调研分布
    lines.append(f"\n  🌍 地区调研分布：")
    rows = conn.execute(
        "SELECT region, COUNT(*) as cnt FROM category_summary GROUP BY region ORDER BY cnt DESC"
    ).fetchall()
    for r in rows:
        lines.append(f"    {r['region']:<6s} {r['cnt']}次")

    # 调研时间线
    lines.append(f"\n  📅 最近调研：")
    rows = conn.execute(
        "SELECT keyword, region, captured_at FROM category_summary ORDER BY captured_at DESC LIMIT 10"
    ).fetchall()
    for r in rows:
        lines.append(f"    {r['captured_at'][:10]} | {r['keyword']:<20s} | {r['region']}")

    lines.append(f"\n{'=' * 65}")
    return "\n".join(lines)


def update_obsidian_refs(conn, run_id: str, report_path: str, vault_path: str):
    """回填 Obsidian 引用到 category_summary"""
    conn.execute(
        "UPDATE category_summary SET report_obsidian_path = ? WHERE run_id = ?",
        (report_path, run_id),
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Amazon 选品数据查询引擎")
    parser.add_argument("--ask", "-a", help="自然语言提问")
    parser.add_argument("--dashboard", "-d", action="store_true", help="显示完整仪表盘")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite 路径")
    parser.add_argument("--keywords", action="store_true", help="列出所有调研过的关键词")
    args = parser.parse_args()

    conn = connect(args.db)

    try:
        if args.keywords:
            rows = conn.execute(
                "SELECT DISTINCT keyword FROM category_summary ORDER BY keyword"
            ).fetchall()
            for r in rows:
                print(r["keyword"])
        elif args.dashboard:
            print(build_dashboard(conn))
        elif args.ask:
            print(query_db(conn, args.ask))
        else:
            print(build_dashboard(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
