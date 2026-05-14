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


def parse_price_value(price_text) -> float:
    import re
    if not price_text:
        return None
    m = re.search(r"[\d,.]+", str(price_text))
    if m:
        try:
            return float(m.group().replace(",", ""))
        except ValueError:
            pass
    return None


def parse_rating_value(rating_text) -> float:
    import re
    if not rating_text:
        return None
    m = re.search(r"(\d+\.?\d*)", str(rating_text))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def parse_monthly_sales(sales_text) -> int:
    import re
    if not sales_text:
        return None
    text = str(sales_text).strip().upper().replace(",", "")
    m = re.search(r"(\d+\.?\d*)\s*K?", text)
    if m:
        val = float(m.group(1))
        if "K" in text:
            val *= 1000
        return int(val)
    return None


def parse_sales_rank(rank_text) -> int:
    import re
    if not rank_text:
        return None
    m = re.search(r"([\d,]+)", str(rank_text))
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


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
    # ── 痛点/优点明细表（优先级5）──
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pain_observation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            keyword TEXT,
            region TEXT,
            asin TEXT NOT NULL,
            pain TEXT NOT NULL,
            count INTEGER DEFAULT 1,
            captured_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pro_observation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            keyword TEXT,
            region TEXT,
            asin TEXT NOT NULL,
            pro TEXT NOT NULL,
            captured_at TEXT NOT NULL
        )
        """
    )
    # ── FTS5 全文检索（优先级6）──
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS review_fts USING fts5(
            asin, run_id, title, body, title_zh, body_zh,
            content='review_snapshot',
            content_rowid='id'
        )
        """
    )
    # 重建触发器（每次 init 都尝试，幂等）
    for trigger in [
        "CREATE TRIGGER IF NOT EXISTS review_ai AFTER INSERT ON review_snapshot BEGIN INSERT INTO review_fts(rowid, asin, run_id, title, body, title_zh, body_zh) VALUES (new.id, new.asin, new.run_id, new.title, new.body, new.title_zh, new.body_zh); END",
        "CREATE TRIGGER IF NOT EXISTS review_ad AFTER DELETE ON review_snapshot BEGIN INSERT INTO review_fts(review_fts, rowid, asin, run_id, title, body, title_zh, body_zh) VALUES('delete', old.id, old.asin, old.run_id, old.title, old.body, old.title_zh, old.body_zh); END",
        "CREATE TRIGGER IF NOT EXISTS review_au AFTER UPDATE ON review_snapshot BEGIN INSERT INTO review_fts(review_fts, rowid, asin, run_id, title, body, title_zh, body_zh) VALUES('delete', old.id, old.asin, old.run_id, old.title, old.body, old.title_zh, old.body_zh); INSERT INTO review_fts(rowid, asin, run_id, title, body, title_zh, body_zh) VALUES (new.id, new.asin, new.run_id, new.title, new.body, new.title_zh, new.body_zh); END",
    ]:
        try:
            conn.execute(trigger)
        except Exception:
            pass

    # ── 迁移：旧表加缺少的列 ──
    for col, col_type in [
        ("currency", "TEXT DEFAULT 'USD'"),
        ("price_value", "REAL"),
        ("rating_value", "REAL"),
        ("monthly_sales_value", "INTEGER"),
        ("sales_rank_value", "INTEGER"),
    ]:
        try:
            conn.execute(f"ALTER TABLE product_snapshot ADD COLUMN {col} {col_type}")
        except Exception:
            pass
    try:
        conn.execute("ALTER TABLE category_summary ADD COLUMN currency TEXT DEFAULT 'USD'")
    except Exception:
        pass

    # ── 索引 ──
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_product_keyword_region ON product_snapshot(keyword, region, captured_at)",
        "CREATE INDEX IF NOT EXISTS idx_category_kwr_time ON category_summary(keyword, region, captured_at)",
        "CREATE INDEX IF NOT EXISTS idx_category_time_score ON category_summary(captured_at, opportunity_score)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_kwr_region ON knowledge_entry(keyword, region, entry_type)",
    ]
    for idx_sql in indexes:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass

    # ── 唯一约束（用 UNIQUE INDEX 避免 ALTER TABLE 兼容问题）──
    uniques = [
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_product_run_asin ON product_snapshot(run_id, asin)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_category_run_kw_region ON category_summary(run_id, keyword, region)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_run_type ON analysis_report(run_id, report_type)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_run_type_asin ON knowledge_entry(run_id, entry_type, asin)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_review_run_asin_body ON review_snapshot(run_id, asin, body)",
    ]
    for uq_sql in uniques:
        try:
            conn.execute(uq_sql)
        except Exception:
            pass

    # 汇总视图
    try:
        conn.execute(
            """
            CREATE VIEW IF NOT EXISTS category_dashboard AS
            SELECT keyword, region, currency,
                   ROUND(AVG(avg_price),0) as avg_price_all,
                   ROUND(AVG(avg_rating),1) as avg_rating_all,
                   SUM(product_count) as total_products,
                   SUM(total_reviews) as total_reviews,
                   MAX(opportunity_score) as best_opportunity_score,
                   COUNT(*) as run_count,
                   MAX(captured_at) as last_run
            FROM category_summary
            GROUP BY keyword, region
            """
        )
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
            # 解析数值字段
            pv = parse_price_value(info.get("price", ""))
            rv = parse_rating_value(info.get("rating", ""))
            ms = parse_monthly_sales(info.get("monthly_sales", ""))
            sr = parse_sales_rank(info.get("sales_rank", ""))
            conn.execute(
                """
                INSERT OR IGNORE INTO product_snapshot (
                    run_id, asin, region, domain, keyword, sort, title, brand,
                    price, rating, review_count, monthly_sales, sales_rank,
                    product_url, product_image, captured_at,
                    currency, price_value, rating_value, monthly_sales_value, sales_rank_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, asin, region, domain, keyword, sort,
                    info.get("title", ""), info.get("brand", ""),
                    info.get("price", ""), info.get("rating", ""),
                    len(reviews), info.get("monthly_sales", ""),
                    info.get("sales_rank", ""),
                    f"https://{domain}/dp/{asin}",
                    info.get("product_image", ""), captured_at,
                    CURRENCY_MAP.get(region, "USD"), pv, rv, ms, sr,
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

        # 痛点/优点明细表
        for asin, info in all_data.items():
            ai = info.get("_ai", {}) or {}
            for pain in ai.get("pains", []) or []:
                kw = pain.get("keyword", "")
                cnt = int(pain.get("count", 0) or 0)
                if kw:
                    conn.execute(
                        "INSERT OR IGNORE INTO pain_observation(run_id,keyword,region,asin,pain,count,captured_at) VALUES(?,?,?,?,?,?,?)",
                        (run_id, keyword, region, asin, kw, cnt, captured_at))
            for pro in ai.get("pros", []) or []:
                if pro.strip():
                    conn.execute(
                        "INSERT OR IGNORE INTO pro_observation(run_id,keyword,region,asin,pro,captured_at) VALUES(?,?,?,?,?,?)",
                        (run_id, keyword, region, asin, pro.strip(), captured_at))

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
