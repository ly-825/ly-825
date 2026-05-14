#!/usr/bin/env python3
"""
Amazon 选品数据查询工具
供 Agent 调用，支持趋势/对比/排名/摘要/痛点等查询模式

用法：
  python3 query_db.py --trend "water bottle" --days 30
  python3 query_db.py --compare "water bottle" --regions us,uk
  python3 query_db.py --ranking --days 30
  python3 query_db.py --summary "laptop bag"
  python3 query_db.py --pains "water bottle"
  python3 query_db.py --categories
"""

import argparse, json, os, re, sys, time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from storage import DEFAULT_DB_PATH, connect


def query_trend(conn, keyword: str, days: int = 30) -> str:
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    rows = conn.execute(
        """SELECT * FROM category_summary
           WHERE keyword LIKE ? AND captured_at >= ?
           ORDER BY captured_at ASC""",
        (f"%{keyword}%", since),
    ).fetchall()

    if len(rows) < 2:
        return json.dumps({
            "type": "trend",
            "keyword": keyword,
            "data_points": len(rows),
            "message": f"数据不足（仅{len(rows)}次调研），需要至少2次才能分析趋势",
            "data": [{"date": r["captured_at"][:10], "region": r["region"],
                       "avg_price": r["avg_price"], "avg_rating": r["avg_rating"],
                       "opportunity_score": r["opportunity_score"]} for r in rows]
        }, ensure_ascii=False)

    first, last = rows[0], rows[-1]
    result = {
        "type": "trend",
        "keyword": keyword,
        "data_points": len(rows),
        "timespan": f"{first['captured_at'][:10]} → {last['captured_at'][:10]}",
        "price_change": {"from": first["avg_price"], "to": last["avg_price"]},
        "rating_change": {"from": first["avg_rating"], "to": last["avg_rating"]},
        "opportunity_change": {"from": first["opportunity_score"], "to": last["opportunity_score"]},
        "points": [{"date": r["captured_at"][:10], "region": r["region"],
                     "avg_price": r["avg_price"], "avg_rating": r["avg_rating"],
                     "opportunity_score": r["opportunity_score"]} for r in rows]
    }
    # 新增/消失的痛点
    first_pains = {p[0] for p in json.loads(first["top_3_pain_points"] or "[]")}
    last_pains = {p[0] for p in json.loads(last["top_3_pain_points"] or "[]")}
    result["new_pains"] = list(last_pains - first_pains)
    result["gone_pains"] = list(first_pains - last_pains)
    return json.dumps(result, ensure_ascii=False)


def query_compare(conn, keyword: str, regions: list = None) -> str:
    rows = conn.execute(
        """SELECT region, avg_price, min_price, max_price, avg_rating,
                  product_count, total_reviews, opportunity_score,
                  top_3_pain_points, top_3_pros, captured_at
           FROM category_summary
           WHERE keyword LIKE ?
           GROUP BY region
           ORDER BY captured_at DESC""",
        (f"%{keyword}%",),
    ).fetchall()

    if not rows:
        return json.dumps({"type": "compare", "keyword": keyword, "message": "无数据"}, ensure_ascii=False)

    regions_data = {}
    for r in rows:
        if regions and r["region"] not in regions:
            continue
        regions_data[r["region"]] = {
            "avg_price": r["avg_price"], "min_price": r["min_price"], "max_price": r["max_price"],
            "avg_rating": r["avg_rating"], "product_count": r["product_count"],
            "total_reviews": r["total_reviews"], "opportunity_score": r["opportunity_score"],
            "top_pains": json.loads(r["top_3_pain_points"] or "[]"),
            "top_pros": json.loads(r["top_3_pros"] or "[]"),
            "last_updated": r["captured_at"][:10],
        }
    return json.dumps({"type": "compare", "keyword": keyword, "regions": regions_data}, ensure_ascii=False)


def query_ranking(conn, days: int = 30) -> str:
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    rows = conn.execute(
        """SELECT keyword, region, avg_price, avg_rating, product_count,
                  opportunity_score, top_3_pain_points, captured_at
           FROM category_summary
           WHERE captured_at >= ?
           ORDER BY opportunity_score DESC""",
        (since,),
    ).fetchall()
    ranking = []
    for r in rows:
        ranking.append({
            "keyword": r["keyword"], "region": r["region"],
            "avg_price": r["avg_price"], "avg_rating": r["avg_rating"],
            "product_count": r["product_count"],
            "opportunity_score": r["opportunity_score"],
            "top_pains": json.loads(r["top_3_pain_points"] or "[]")[:2],
            "date": r["captured_at"][:10],
        })
    return json.dumps({"type": "ranking", "days": days, "count": len(ranking), "ranking": ranking}, ensure_ascii=False)


def query_summary(conn, keyword: str) -> str:
    row = conn.execute(
        """SELECT * FROM category_summary
           WHERE keyword LIKE ? ORDER BY captured_at DESC LIMIT 1""",
        (f"%{keyword}%",),
    ).fetchone()

    if not row:
        return json.dumps({"type": "summary", "keyword": keyword, "message": "无数据"}, ensure_ascii=False)

    return json.dumps({
        "type": "summary",
        "keyword": row["keyword"],
        "region": row["region"],
        "avg_price": row["avg_price"],
        "min_price": row["min_price"],
        "max_price": row["max_price"],
        "avg_rating": row["avg_rating"],
        "product_count": row["product_count"],
        "total_reviews": row["total_reviews"],
        "opportunity_score": row["opportunity_score"],
        "top_pains": json.loads(row["top_3_pain_points"] or "[]"),
        "top_pros": json.loads(row["top_3_pros"] or "[]"),
        "date": row["captured_at"][:10],
    }, ensure_ascii=False)


def query_pains(conn, keyword: str = None, days: int = 90) -> str:
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    if keyword:
        rows = conn.execute(
            """SELECT top_3_pain_points FROM category_summary
               WHERE keyword LIKE ? AND captured_at >= ?""",
            (f"%{keyword}%", since),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT top_3_pain_points FROM category_summary WHERE captured_at >= ?",
            (since,),
        ).fetchall()

    all_pains = defaultdict(int)
    for r in rows:
        for p in json.loads(r["top_3_pain_points"] or "[]"):
            all_pains[p[0]] += p[1]

    ranked = sorted(all_pains.items(), key=lambda x: -x[1])
    return json.dumps({
        "type": "pains",
        "keyword": keyword or "全品类",
        "days": days,
        "pains": [{"keyword": kw, "count": cnt} for kw, cnt in ranked],
    }, ensure_ascii=False)


def query_categories(conn) -> str:
    rows = conn.execute(
        """SELECT keyword, COUNT(*) as runs, MAX(captured_at) as last,
                  GROUP_CONCAT(DISTINCT region) as regions
           FROM category_summary
           GROUP BY keyword ORDER BY last DESC"""
    ).fetchall()
    cats = [{"keyword": r["keyword"], "runs": r["runs"],
             "last": r["last"][:10], "regions": r["regions"].split(",")} for r in rows]
    return json.dumps({"type": "categories", "count": len(cats), "categories": cats}, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Amazon 数据查询")
    parser.add_argument("--trend", "-t", help="品类趋势（关键词）")
    parser.add_argument("--compare", "-c", help="跨地区对比（关键词）")
    parser.add_argument("--ranking", "-r", action="store_true", help="品类机会排名")
    parser.add_argument("--summary", "-s", help="品类摘要（关键词）")
    parser.add_argument("--pains", "-p", nargs="?", const="__ALL__", help="痛点分析（可选关键词）")
    parser.add_argument("--categories", action="store_true", help="列出所有品类")
    parser.add_argument("--regions", default="", help="指定地区，逗号分隔")
    parser.add_argument("--days", "-d", type=int, default=30, help="天数范围")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        if args.trend:
            print(query_trend(conn, args.trend, args.days))
        elif args.compare:
            regions = [r.strip() for r in args.regions.split(",") if r.strip()] if args.regions else None
            print(query_compare(conn, args.compare, regions))
        elif args.ranking:
            print(query_ranking(conn, args.days))
        elif args.summary:
            print(query_summary(conn, args.summary))
        elif args.pains is not None:
            kw = None if args.pains == "__ALL__" else args.pains
            print(query_pains(conn, kw, args.days))
        elif args.categories:
            print(query_categories(conn))
        else:
            print(query_ranking(conn, args.days))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
