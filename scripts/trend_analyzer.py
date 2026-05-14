#!/usr/bin/env python3

import argparse
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from storage import DEFAULT_DB_PATH, connect

DEFAULT_VAULT = str(Path.home() / "Documents" / "Obsidian" / "Amazon选品")


def safe_name(text: str, fallback: str = "trend") -> str:
    text = (text or fallback).strip()
    text = re.sub(r"[\\/:*?\"<>|#\[\]]+", "-", text)
    text = re.sub(r"\s+", "-", text)
    text = text.strip("-._ ")
    return text[:80] or fallback


def parse_number(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    multiplier = 1
    if re.search(r"k\+?$", text, re.I):
        multiplier = 1000
    elif re.search(r"m\+?$", text, re.I):
        multiplier = 1000000
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return float(match.group(0)) * multiplier


def parse_rank(value):
    if value is None:
        return None
    text = str(value).replace(",", "")
    match = re.search(r"#?\s*(\d+)", text)
    if not match:
        return None
    return float(match.group(1))


def change_line(label, old_raw, new_raw, parser=parse_number, lower_is_better=False):
    old = parser(old_raw)
    new = parser(new_raw)
    if old is None or new is None:
        return f"- {label}: {old_raw or 'N/A'} -> {new_raw or 'N/A'}"
    delta = new - old
    pct = ""
    if old != 0:
        pct = f" ({delta / old * 100:+.1f}%)"
    if delta == 0:
        direction = "持平"
    elif lower_is_better:
        direction = "改善" if delta < 0 else "变差"
    else:
        direction = "上升" if delta > 0 else "下降"
    return f"- {label}: {old_raw or old} -> {new_raw or new}，{direction} {delta:+.2f}{pct}"


def load_snapshots(keyword="", region="", asin="", days=0, db_path=DEFAULT_DB_PATH):
    conn = connect(db_path)
    try:
        where = []
        params = []
        if keyword:
            where.append("keyword LIKE ?")
            params.append(f"%{keyword}%")
        if region:
            where.append("region = ?")
            params.append(region)
        if asin:
            where.append("asin = ?")
            params.append(asin)
        if days and days > 0:
            since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            where.append("captured_at >= ?")
            params.append(since)
        sql = "SELECT * FROM product_snapshot"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY asin, captured_at ASC, id ASC"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def analyze_trends(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["asin"]].append(row)
    trends = []
    for asin, snapshots in grouped.items():
        if len(snapshots) < 2:
            continue
        first = snapshots[0]
        last = snapshots[-1]
        trends.append({"asin": asin, "first": first, "last": last, "count": len(snapshots)})
    return trends


def render_report(trends, total_rows, keyword="", region="", days=0):
    lines = [
        "---",
        "type: trend-report",
        f"keyword: {keyword}",
        f"region: {region}",
        f"days: {days}",
        f"created_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "tags:",
        "  - amazon",
        "  - trend-report",
        "---",
        "",
        "# Amazon 商品趋势对比",
        "",
        "## 概览",
        "",
        f"- 关键词: {keyword or '全部'}",
        f"- 地区: {region or '全部'}",
        f"- 周期: {'不限' if not days else str(days) + ' 天'}",
        f"- 快照记录数: {total_rows}",
        f"- 可对比 ASIN 数: {len(trends)}",
        "",
    ]
    if not trends:
        lines.extend([
            "暂无可对比趋势。",
            "",
            "需要同一个 ASIN 至少保存过 2 次快照。运行 pipeline 时请加 `--save-db`。",
        ])
        return "\n".join(lines)
    for item in trends:
        first = item["first"]
        last = item["last"]
        lines.extend([
            f"## {item['asin']} - {(last.get('title') or '')[:80]}",
            "",
            f"- 快照次数: {item['count']}",
            f"- 时间范围: {first.get('captured_at')} -> {last.get('captured_at')}",
            f"- 地区: {last.get('region') or ''}",
            f"- 关键词: {last.get('keyword') or ''}",
            f"- 商品链接: {last.get('product_url') or ''}",
            "",
            change_line("价格", first.get("price"), last.get("price")),
            change_line("评分", first.get("rating"), last.get("rating")),
            change_line("评论数", first.get("review_count"), last.get("review_count")),
            change_line("月销量", first.get("monthly_sales"), last.get("monthly_sales")),
            change_line("销售排名", first.get("sales_rank"), last.get("sales_rank"), parser=parse_rank, lower_is_better=True),
            "",
        ])
    return "\n".join(lines).strip()


def export_obsidian_report(content: str, keyword: str, region: str, days: int, vault_path: str = DEFAULT_VAULT) -> str:
    vault = Path(vault_path or DEFAULT_VAULT).expanduser()
    out_dir = vault / "05_趋势报告"
    out_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y%m%d_%H%M%S")
    keyword_slug = safe_name(keyword or "all-keywords")
    region_slug = safe_name(region or "all-regions")
    days_slug = f"{days}days" if days else "all-days"
    out_path = out_dir / f"{date}_{region_slug}_{keyword_slug}_{days_slug}.md"
    out_path.write_text(content + "\n", encoding="utf-8")
    return str(out_path)


def main():
    parser = argparse.ArgumentParser(description="Amazon Review Pipeline 历史趋势对比")
    parser.add_argument("--keyword", "-k", default="", help="按关键词过滤")
    parser.add_argument("--region", "-r", default="", help="按地区过滤，如 us/jp/de")
    parser.add_argument("--asin", default="", help="按 ASIN 过滤")
    parser.add_argument("--days", type=int, default=0, help="只分析最近 N 天，0 表示不限")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="SQLite 数据库路径")
    parser.add_argument("--export-obsidian", action="store_true", help="导出趋势报告到 Obsidian")
    parser.add_argument("--obsidian-vault", default=DEFAULT_VAULT, help="Obsidian vault 路径")
    args = parser.parse_args()
    rows = load_snapshots(
        keyword=args.keyword,
        region=args.region,
        asin=args.asin,
        days=args.days,
        db_path=args.db_path,
    )
    trends = analyze_trends(rows)
    report = render_report(trends, total_rows=len(rows), keyword=args.keyword, region=args.region, days=args.days)
    print(report)
    if args.export_obsidian:
        out_path = export_obsidian_report(
            report,
            keyword=args.keyword,
            region=args.region,
            days=args.days,
            vault_path=args.obsidian_vault,
        )
        print(f"\n✅ 趋势报告已导出: {out_path}")


if __name__ == "__main__":
    main()
