#!/usr/bin/env python3

import json
import os
import sqlite3
import time
from pathlib import Path

DEFAULT_DB_PATH = os.environ.get(
    "AMAZON_REVIEW_PIPELINE_DB",
    str(Path.home() / ".amazon_review_pipeline" / "pipeline.db"),
)

CURRENCY_MAP = {
    "us": "USD", "uk": "GBP", "de": "EUR", "jp": "JPY", "fr": "EUR",
    "it": "EUR", "es": "EUR", "ca": "CAD", "in": "INR", "au": "AUD",
    "mx": "MXN", "br": "BRL", "nl": "EUR",
}

CURRENCY_SYMBOLS = {
    "USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥", "CAD": "C$",
    "INR": "₹", "AUD": "A$", "MXN": "MX$", "BRL": "R$",
}


def connect(db_path: str = DEFAULT_DB_PATH):
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS product_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            asin TEXT NOT NULL,
            region TEXT,
            domain TEXT,
            keyword TEXT,
            sort TEXT,
            title TEXT,
            brand TEXT,
            price TEXT,
            rating TEXT,
            review_count INTEGER,
            monthly_sales TEXT,
            sales_rank TEXT,
            product_url TEXT,
            product_image TEXT,
            captured_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            asin TEXT NOT NULL,
            region TEXT,
            rating TEXT,
            author TEXT,
            review_date TEXT,
            verified INTEGER,
            title TEXT,
            body TEXT,
            title_zh TEXT,
            body_zh TEXT,
            captured_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_report (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            report_type TEXT NOT NULL,
            keyword TEXT,
            region TEXT,
            sort TEXT,
            asins TEXT,
            content_json TEXT,
            docx_path TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_entry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            keyword TEXT,
            region TEXT,
            asin TEXT,
            title TEXT,
            content TEXT NOT NULL,
            metadata_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS category_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            keyword TEXT NOT NULL,
            region TEXT,
            category TEXT NOT NULL,
            avg_price REAL,
            min_price REAL,
            max_price REAL,
            avg_rating REAL,
            total_reviews INTEGER,
            product_count INTEGER,
            top_3_pain_points TEXT,
            top_3_pros TEXT,
            opportunity_score REAL,
            report_obsidian_path TEXT,
            docx_path TEXT,
            currency TEXT DEFAULT 'USD',
            captured_at TEXT NOT NULL
        )
        """
    )
    # 迁移：给旧表加 currency 列
    try:
        conn.execute("ALTER TABLE category_summary ADD COLUMN currency TEXT DEFAULT 'USD'")
    except Exception:
        pass
    # 回填已有数据的币种
    for code, curr in CURRENCY_MAP.items():
        conn.execute("UPDATE category_summary SET currency = ? WHERE region = ? AND currency IS NULL", (curr, code))
    conn.commit()


def save_pipeline_run(
    all_data: dict,
    keyword: str,
    sort: str,
    region: str,
    domain: str,
    docx_path: str = "",
    db_path: str = DEFAULT_DB_PATH,
    run_id: str = "",
    cross_data: dict = None,
):
    """一次事务写入所有数据：product/review/knowledge/analysis/summary"""
    captured_at = time.strftime("%Y-%m-%d %H:%M:%S")
    run_id = run_id or time.strftime("%Y%m%d_%H%M%S")
    cross_data = cross_data or {}
    conn = connect(db_path)
    try:
        for asin, info in all_data.items():
            reviews = info.get("reviews", []) or []
            conn.execute(
                """
                INSERT INTO product_snapshot (
                    run_id, asin, region, domain, keyword, sort, title, brand,
                    price, rating, review_count, monthly_sales, sales_rank,
                    product_url, product_image, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, asin, region, domain, keyword, sort,
                    info.get("title", ""), info.get("brand", ""),
                    info.get("price", ""), info.get("rating", ""),
                    len(reviews), info.get("monthly_sales", ""),
                    info.get("sales_rank", ""),
                    f"https://{domain}/dp/{asin}",
                    info.get("product_image", ""), captured_at,
                ),
            )
            for review in reviews:
                conn.execute(
                    """
                    INSERT INTO review_snapshot (
                        run_id, asin, region, rating, author, review_date,
                        verified, title, body, title_zh, body_zh, captured_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, asin, region, review.get("rating", ""),
                        review.get("author", ""), review.get("date", ""),
                        1 if review.get("verified") else 0,
                        review.get("title", ""), review.get("body", ""),
                        review.get("title_zh", ""), review.get("body_zh", ""),
                        captured_at,
                    ),
                )
            # knowledge_entry: 存储结构化 JSON
            ai = info.get("_ai", {}) or {}
            structured = {
                "scenarios": ai.get("scenarios", []),
                "pros": ai.get("pros", []),
                "pains": ai.get("pains", []),
                "pain_tags": ai.get("pain_tags", []),
                "best_review_index": ai.get("best_review_index", 0),
            }
            conn.execute(
                """
                INSERT INTO knowledge_entry (
                    run_id, entry_type, keyword, region, asin, title,
                    content, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, "product_analysis", keyword, region, asin,
                    info.get("title", ""),
                    json.dumps(structured, ensure_ascii=False),
                    json.dumps({
                        "sort": sort, "price": info.get("price", ""),
                        "rating": info.get("rating", ""),
                        "review_count": len(reviews),
                        "monthly_sales": info.get("monthly_sales", ""),
                    }, ensure_ascii=False),
                    captured_at,
                ),
            )

        # analysis_report: 存完整跨产品分析
        conn.execute(
            """
            INSERT INTO analysis_report (
                run_id, report_type, keyword, region, sort, asins,
                content_json, docx_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, "pipeline_run", keyword, region, sort,
                json.dumps(list(all_data.keys()), ensure_ascii=False),
                json.dumps(cross_data, ensure_ascii=False),
                docx_path, captured_at,
            ),
        )

        # category_summary: 直接从内存 all_data 聚合
        save_category_summary(
            conn, all_data=all_data, keyword=keyword, region=region,
            docx_path=docx_path, run_id=run_id, captured_at=captured_at,
        )

        conn.commit()
    finally:
        conn.close()
    return db_path


def save_category_summary(conn, all_data: dict, keyword: str, region: str, docx_path: str = "", run_id: str = "", captured_at: str = ""):
    """聚合品类高浓度结论：均价/评分/Top痛点/机会评分"""
    import re

    prices = []
    ratings = []
    pain_kw_count = {}
    pro_set = set()
    total_reviews = 0
    product_count = len(all_data)

    for info in all_data.values():
        # 价格
        price_text = info.get("price", "") or ""
        m = re.search(r"[\d,.]+", str(price_text))
        if m:
            try:
                prices.append(float(m.group().replace(",", "")))
            except ValueError:
                pass
        # 评分
        rating_text = info.get("rating", "") or ""
        m = re.search(r"(\d+\.?\d*)", str(rating_text))
        if m:
            try:
                ratings.append(float(m.group(1)))
            except ValueError:
                pass
        # 评论数
        total_reviews += len(info.get("reviews", []) or [])
        # 痛点聚合
        for pain in (info.get("_ai", {}) or {}).get("pains", []) or []:
            kw = pain.get("keyword", "")
            cnt = int(pain.get("count", 0) or 0)
            if kw:
                pain_kw_count[kw] = pain_kw_count.get(kw, 0) + cnt
        # 优点聚合
        for pro in (info.get("_ai", {}) or {}).get("pros", []) or []:
            if pro.strip():
                pro_set.add(pro.strip())

    top_pains = sorted(pain_kw_count.items(), key=lambda x: -x[1])[:3]
    top_pros = list(pro_set)[:3]

    avg_price = round(sum(prices) / len(prices), 2) if prices else 0
    min_price = round(min(prices), 2) if prices else 0
    max_price = round(max(prices), 2) if prices else 0
    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else 0

    # 机会评分：痛点越集中 + 评分越低 = 机会越大 (0-100)
    opportunity_score = 0
    if avg_rating and avg_rating < 4.5:
        opportunity_score += min(40, int((4.5 - avg_rating) * 80))
    if top_pains:
        opportunity_score += min(30, top_pains[0][1] * 3)
    if prices and len(prices) >= 2:
        price_range = max_price - min_price
        if price_range > 0 and avg_price > 0:
            opportunity_score += min(30, int((price_range / avg_price) * 30))
    opportunity_score = min(100, opportunity_score)

    currency = CURRENCY_MAP.get(region, "USD")
    conn.execute(
        """
        INSERT INTO category_summary (
            run_id, keyword, region, category, avg_price, min_price, max_price,
            avg_rating, total_reviews, product_count, top_3_pain_points,
            top_3_pros, opportunity_score, report_obsidian_path, docx_path,
            currency, captured_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            keyword, region, keyword,
            avg_price, min_price, max_price,
            avg_rating, total_reviews, product_count,
            json.dumps(top_pains, ensure_ascii=False),
            json.dumps(top_pros, ensure_ascii=False),
            opportunity_score,
            "",  # report_obsidian_path
            docx_path,
            currency,
            captured_at or time.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
