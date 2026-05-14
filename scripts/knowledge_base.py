#!/usr/bin/env python3

import argparse
import json
import sqlite3
from storage import DEFAULT_DB_PATH, connect


def search_knowledge(query: str, region: str = "", keyword: str = "", limit: int = 10, db_path: str = DEFAULT_DB_PATH):
    conn = connect(db_path)
    try:
        terms = [t.strip() for t in query.replace("，", " ").replace(",", " ").split() if t.strip()]
        where = []
        params = []
        if region:
            where.append("region = ?")
            params.append(region)
        if keyword:
            where.append("keyword LIKE ?")
            params.append(f"%{keyword}%")
        for term in terms:
            where.append("(content LIKE ? OR title LIKE ? OR keyword LIKE ? OR asin LIKE ?)")
            like = f"%{term}%"
            params.extend([like, like, like, like])
        sql = "SELECT * FROM knowledge_entry"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def print_results(rows):
    if not rows:
        print("暂无匹配的知识条目。")
        return
    for i, row in enumerate(rows, 1):
        print("=" * 72)
        print(f"#{i} {row.get('entry_type', '')} | {row.get('created_at', '')}")
        print(f"关键词: {row.get('keyword', '')} | 地区: {row.get('region', '')} | ASIN: {row.get('asin', '')}")
        title = row.get("title") or ""
        if title:
            print(f"标题: {title[:120]}")
        metadata = row.get("metadata_json") or ""
        if metadata:
            try:
                meta = json.loads(metadata)
                brief = []
                for key in ("price", "rating", "review_count", "monthly_sales", "sales_rank"):
                    if meta.get(key) not in (None, ""):
                        brief.append(f"{key}={meta.get(key)}")
                if brief:
                    print("指标: " + " | ".join(brief))
            except Exception:
                pass
        print("-" * 72)
        print((row.get("content") or "")[:1200])
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(description="Amazon Review Pipeline 本地知识库检索")
    parser.add_argument("--query", "-q", required=True, help="检索问题或关键词")
    parser.add_argument("--region", "-r", default="", help="可选地区代码，如 us/jp/de")
    parser.add_argument("--keyword", "-k", default="", help="可选原始品类关键词")
    parser.add_argument("--limit", "-n", type=int, default=10, help="返回条数")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="SQLite 数据库路径")
    args = parser.parse_args()
    rows = search_knowledge(
        query=args.query,
        region=args.region,
        keyword=args.keyword,
        limit=args.limit,
        db_path=args.db_path,
    )
    print_results(rows)


if __name__ == "__main__":
    main()
