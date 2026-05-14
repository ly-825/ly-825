#!/usr/bin/env python3

import argparse
import json
import re
import time
from pathlib import Path

DEFAULT_VAULT = str(Path.home() / "Documents" / "Obsidian" / "Amazon选品")


def safe_name(text: str, fallback: str = "untitled") -> str:
    text = (text or fallback).strip()
    text = re.sub(r"[\\/:*?\"<>|#\[\]]+", "-", text)
    text = re.sub(r"\s+", "-", text)
    text = text.strip("-._ ")
    return text[:80] or fallback


def md_escape(text: str) -> str:
    return (text or "").replace("\r", "").strip()


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def render_product_card(asin: str, info: dict, keyword: str, region: str, sort: str, domain: str, captured_at: str, run_id: str = "", db_path: str = "") -> str:
    ai = info.get("_ai", {}) or {}
    reviews = info.get("reviews", []) or []
    lines = [
        "---",
        f"type: asin-card",
        f"batch_id: {run_id}",
        f"asin: {asin}",
        f"keyword: {keyword}",
        f"region: {region}",
        f"sort: {sort}",
        f"captured_at: {captured_at}",
        f"db_path: {db_path}",
        "tags:",
        "  - amazon",
        "  - asin-card",
        f"  - region/{region}",
        f"  - category/{safe_name(keyword)}",
        f"  - batch/{run_id}",
        "---",
        "",
        f"# {asin} - {md_escape(info.get('title', ''))[:100]}",
        "",
        "## 基本信息",
        "",
        f"- ASIN: `{asin}`",
        f"- 地区: `{region}`",
        f"- 品类: [[00_Dashboard|{keyword}]]",
        f"- 榜单: `{sort}`",
        f"- 链接: https://{domain}/dp/{asin}",
        f"- 价格: {md_escape(info.get('price', '')) or 'N/A'}",
        f"- 评分: {md_escape(info.get('rating', '')) or 'N/A'}",
        f"- 评论总数: {md_escape(info.get('review_count', '')) or 'N/A'}",
        f"- 评论数: {len(reviews)}",
        f"- 月销量: {md_escape(info.get('monthly_sales', '')) or 'N/A'}",
        f"- 排名: {md_escape(info.get('sales_rank', '')) or 'N/A'}",
        "",
    ]
    if info.get("product_image"):
        lines.extend(["## 商品图片", "", f"![]({info.get('product_image')})", ""])
    if ai.get("scenarios"):
        lines.extend(["## 用户场景", ""])
        lines.extend(f"- {md_escape(x)}" for x in ai.get("scenarios", []))
        lines.append("")
    if ai.get("pros"):
        lines.extend(["## 核心优点", ""])
        lines.extend(f"- {md_escape(x)}" for x in ai.get("pros", []))
        lines.append("")
    if ai.get("pains") or ai.get("pain_tags"):
        lines.extend(["## 痛点", ""])
        for tag in ai.get("pain_tags", []) or []:
            tag_slug = safe_name(tag)
            lines.append(f"- [[04_痛点库/{tag_slug}|{md_escape(tag)}]]")
        for pain in ai.get("pains", []) or []:
            kw = pain.get('keyword', '')
            kw_slug = safe_name(kw)
            lines.append(f"- [[04_痛点库/{kw_slug}|{md_escape(kw)}]]: {pain.get('count', 0)} 次")
        lines.append("")
    if reviews:
        lines.extend(["## 代表评论", ""])
        for i, review in enumerate(reviews[:5], 1):
            title = review.get("title_zh") or review.get("title") or ""
            body = review.get("body_zh") or review.get("body") or ""
            rating = review.get("rating", "")
            lines.extend([
                f"### 评论 {i}",
                "",
                f"- 评分: {md_escape(rating)}",
                f"- 标题: {md_escape(title)}",
                "",
                md_escape(body)[:800],
                "",
            ])
    return "\n".join(lines).strip() + "\n"


def render_report_note(all_data: dict, keyword: str, region: str, sort: str, domain: str, docx_path: str, captured_at: str) -> str:
    date = captured_at.split()[0] if captured_at else time.strftime("%Y-%m-%d")
    all_scenarios = {}
    all_pros = {}
    ai_errors = []
    lines = [
        "---",
        "type: research-report",
        f"keyword: {keyword}",
        f"region: {region}",
        f"sort: {sort}",
        f"captured_at: {captured_at}",
        "tags:",
        "  - amazon",
        "  - research-report",
        f"  - region-{region}",
        "---",
        "",
        f"# {keyword} - {region} - {sort} - {date}",
        "",
        "## 基本信息",
        "",
        f"- 关键词: {keyword}",
        f"- 地区: {region}",
        f"- 榜单: {sort}",
        f"- 产品数: {len(all_data)}",
        f"- Word 报告: {docx_path or 'N/A'}",
        "",
        "## 商品列表",
        "",
    ]
    all_pains = {}
    for asin, info in all_data.items():
        title = md_escape(info.get("title", ""))[:80]
        lines.append(f"- [[{asin}]] - {title}")
        ai = info.get("_ai", {}) or {}
        if ai.get("_ai_error"):
            ai_errors.append((asin, ai.get("_ai_error", "")))
        for scenario in ai.get("scenarios", []) or []:
            all_scenarios[scenario] = all_scenarios.get(scenario, 0) + 1
        for pro in ai.get("pros", []) or []:
            all_pros[pro] = all_pros.get(pro, 0) + 1
        for pain in ai.get("pains", []) or []:
            key = pain.get("keyword", "")
            if key:
                all_pains[key] = all_pains.get(key, 0) + int(pain.get("count", 0) or 0)
    lines.append("")
    lines.extend(["## 商品详情", ""])
    for asin, info in all_data.items():
        ai = info.get("_ai", {}) or {}
        reviews = info.get("reviews", []) or []
        lines.extend([
            f"### [[{asin}]]",
            "",
            f"- 标题: {md_escape(info.get('title', ''))}",
            f"- 品牌: {md_escape(info.get('brand', '')) or 'N/A'}",
            f"- 价格: {md_escape(info.get('price', '')) or 'N/A'}",
            f"- 评分: {md_escape(info.get('rating', '')) or 'N/A'}",
            f"- 评论总数: {md_escape(info.get('review_count', '')) or 'N/A'}",
            f"- 评论样本数: {len(reviews)}",
            f"- 月销量: {md_escape(info.get('monthly_sales', '')) or 'N/A'}",
            f"- 销售排名: {md_escape(info.get('sales_rank', '')) or 'N/A'}",
            f"- 链接: https://{domain}/dp/{asin}",
            "",
        ])
        if info.get("product_image"):
            lines.extend([f"![]({info.get('product_image')})", ""])
        if info.get("bullet_points"):
            lines.extend(["#### 商品要点", ""])
            lines.extend(f"- {md_escape(x)}" for x in info.get("bullet_points", [])[:8])
            lines.append("")
        if ai.get("_fallback"):
            lines.extend(["- 分析来源: AI 超时/失败，本段使用本地规则兜底摘要。", ""])
        if ai.get("pains"):
            lines.extend(["#### 痛点", ""])
            for pain in ai.get("pains", [])[:8]:
                lines.append(f"- {md_escape(pain.get('keyword', ''))}: {pain.get('count', 0)} 次")
            lines.append("")
        if ai.get("pros"):
            lines.extend(["#### 卖点", ""])
            lines.extend(f"- {md_escape(x)}" for x in ai.get("pros", [])[:8])
            lines.append("")
        if ai.get("scenarios"):
            lines.extend(["#### 使用场景", ""])
            lines.extend(f"- {md_escape(x)}" for x in ai.get("scenarios", [])[:8])
            lines.append("")
        if reviews:
            best_idx = ai.get("best_review_index", 0)
            if not isinstance(best_idx, int) or best_idx < 0 or best_idx >= len(reviews):
                best_idx = 0
            review = reviews[best_idx]
            title = review.get("title_zh") or review.get("title") or ""
            body = review.get("body_zh") or review.get("body") or ""
            lines.extend([
                "#### 代表评论",
                "",
                f"- 评分: {md_escape(review.get('rating', ''))}",
                f"- Verified: {'是' if review.get('verified') else '否'}",
                f"- 标题: {md_escape(title) or 'N/A'}",
                "",
                md_escape(body)[:800] or "N/A",
                "",
            ])
    lines.extend(["## 共性痛点", ""])
    if all_pains:
        for key, count in sorted(all_pains.items(), key=lambda x: -x[1])[:20]:
            lines.append(f"- {md_escape(key)}: {count} 次")
    else:
        lines.append("- 暂无明显共性痛点")
    lines.append("")
    lines.extend(["## AI 分析状态", ""])
    if ai_errors:
        lines.append("- 部分商品 AI 分析失败/超时，报告已降级继续生成。")
        for asin, error in ai_errors[:20]:
            lines.append(f"- [[{asin}]]: {md_escape(error[:120])}")
    else:
        lines.append("- AI 分析完成，未记录失败或超时。")
    lines.append("")
    lines.extend(["## 高频使用场景", ""])
    if all_scenarios:
        for key, count in sorted(all_scenarios.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"- {md_escape(key)}: 覆盖 {count} 个商品")
    else:
        lines.append("- 暂无明显场景")
    lines.append("")
    lines.extend(["## 用户认可卖点", ""])
    if all_pros:
        for key, count in sorted(all_pros.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"- {md_escape(key)}: 覆盖 {count} 个商品")
    else:
        lines.append("- 暂无明显卖点")
    lines.append("")
    lines.extend(["## 选品复盘", ""])
    if all_pains:
        top_pains = "、".join(key for key, _ in sorted(all_pains.items(), key=lambda x: -x[1])[:5])
        lines.append(f"- 优先验证痛点是否真实高频：{top_pains}")
        lines.append("- 不建议只根据本次少量样本直接定款，应扩大 ASIN 数量后再判断。")
    else:
        lines.append("- 本次样本未形成稳定共性痛点，建议扩大样本后再做选品判断。")
    if all_pros:
        top_pros = "、".join(key for key, _ in sorted(all_pros.items(), key=lambda x: -x[1])[:5])
        lines.append(f"- 可保留用户已认可卖点：{top_pros}")
    lines.append("- 如果搜索结果集中出现同品牌/同系列变体，需要提高产品数或调整关键词，避免把颜色/容量差异误判为多个竞品。")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def extract_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    meta = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.strip().startswith("-"):
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta


def collect_dashboard_stats(vault: Path, current_data: dict):
    reports = []
    report_dir = vault / "02_调研报告"
    if report_dir.exists():
        for path in sorted(report_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            meta = extract_frontmatter(path)
            reports.append({
                "path": path,
                "name": path.stem,
                "keyword": meta.get("keyword", ""),
                "region": meta.get("region", ""),
                "sort": meta.get("sort", ""),
                "captured_at": meta.get("captured_at", ""),
            })

    pain_counts = {}
    scenario_counts = {}
    pro_counts = {}
    for info in current_data.values():
        ai = info.get("_ai", {}) or {}
        for pain in ai.get("pains", []) or []:
            key = pain.get("keyword", "")
            if key:
                pain_counts[key] = pain_counts.get(key, 0) + int(pain.get("count", 0) or 0)
        for scenario in ai.get("scenarios", []) or []:
            scenario_counts[scenario] = scenario_counts.get(scenario, 0) + 1
        for pro in ai.get("pros", []) or []:
            pro_counts[pro] = pro_counts.get(pro, 0) + 1

    return reports, pain_counts, scenario_counts, pro_counts


def render_dashboard(vault: Path, current_data: dict, captured_at: str) -> str:
    """从 SQLite 动态生成仪表盘"""
    import json as _json, os as _os
    db_path = _os.environ.get("AMAZON_REVIEW_PIPELINE_DB",
              str(Path.home() / ".amazon_review_pipeline" / "pipeline.db"))

    # 尝试从 SQLite 拉数据
    db_stats = {"runs": 0, "products": 0, "reviews": 0, "cats": 0,
                "ranking": [], "pains": [], "recent": [], "regions": {}, "keywords": {}}

    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        db_stats["cats"] = conn.execute("SELECT COUNT(DISTINCT keyword) FROM category_summary").fetchone()[0]
        db_stats["runs"] = conn.execute("SELECT COUNT(DISTINCT run_id) FROM category_summary").fetchone()[0]
        db_stats["products"] = conn.execute("SELECT COUNT(*) FROM product_snapshot").fetchone()[0]
        db_stats["reviews"] = conn.execute("SELECT COUNT(*) FROM review_snapshot").fetchone()[0]

        # 机会排名
        rows = conn.execute("""SELECT keyword, region, avg_price, avg_rating, currency,
            opportunity_score, captured_at FROM category_summary
            ORDER BY opportunity_score DESC LIMIT 8""").fetchall()
        db_stats["ranking"] = [dict(r) for r in rows]

        # 全品类痛点 TOP 15
        pain_rows = conn.execute(
            "SELECT top_3_pain_points FROM category_summary"
        ).fetchall()
        pain_map = {}
        for r in pain_rows:
            for p in _json.loads(r["top_3_pain_points"] or "[]"):
                pain_map[p[0]] = pain_map.get(p[0], 0) + p[1]
        db_stats["pains"] = sorted(pain_map.items(), key=lambda x: -x[1])[:15]

        # 最近调研
        recents = conn.execute(
            "SELECT keyword, region, captured_at, report_obsidian_path FROM category_summary ORDER BY captured_at DESC LIMIT 10"
        ).fetchall()
        db_stats["recent"] = [dict(r) for r in recents]

        # 地区和关键词分布
        for r in conn.execute("SELECT region, COUNT(*) as cnt FROM category_summary GROUP BY region").fetchall():
            db_stats["regions"][r["region"]] = r["cnt"]
        for r in conn.execute("SELECT keyword, COUNT(*) as cnt FROM category_summary GROUP BY keyword").fetchall():
            db_stats["keywords"][r["keyword"]] = r["cnt"]

        conn.close()
    except Exception:
        pass

    lines = [
        "# Amazon 选品数据中心",
        "",
        f"> 自动更新：{captured_at} | {db_stats['cats']}个品类 | {db_stats['runs']}次调研 | {db_stats['products']}个产品 | {db_stats['reviews']}条评论",
        "",
        "## 🔥 品类机会排名",
        "",
        "| 品类 | 地区 | 均价 | 评分 | 机会分 | 日期 |",
        "|------|------|------|------|--------|------|",
    ]
    for r in db_stats["ranking"]:
        curr = r['currency'] or 'USD'
        sym = {'USD':'$','GBP':'£','EUR':'€','JPY':'¥','CAD':'C$','INR':'₹','AUD':'A$','MXN':'MX$','BRL':'R$'}.get(curr, '$')
        lines.append(f"| {r['keyword']} | {r['region']} | {sym}{r['avg_price']:.0f} | {r['avg_rating']:.1f}★ | {r['opportunity_score']:.0f} | {r['captured_at'][:10]} |")

    lines.extend(["", "## 🔍 全品类痛点 TOP 15", ""])
    for kw, cnt in db_stats["pains"]:
        lines.append(f"- {md_escape(kw)}: {cnt}次")

    lines.extend(["", "## 📅 最近调研", ""])
    for r in db_stats["recent"]:
        obsidian_ref = r.get("report_obsidian_path", "")
        if obsidian_ref:
            lines.append(f"- [[{obsidian_ref}|{r['captured_at'][:10]} {r['keyword']}({r['region']})]]")
        else:
            lines.append(f"- {r['captured_at'][:10]} | {r['keyword']} | {r['region']}")

    lines.extend(["", "## 🌍 地区分布", ""])
    for k, c in sorted(db_stats["regions"].items(), key=lambda x: -x[1]):
        lines.append(f"- `{k}`: {c}次")

    lines.extend(["", "## 📦 品类分布", ""])
    for k, c in sorted(db_stats["keywords"].items(), key=lambda x: -x[1]):
        lines.append(f"- `{md_escape(k)}`: {c}次")

    lines.extend([
        "",
        "## 快速入口",
        "",
        "- `02_调研报告/`",
        "- `03_ASIN卡片/`",
        "- `04_痛点库/`",
        "- `99_附件/`",
        "",
    ])
    return "\n".join(lines)


def render_pain_note(pain: str, entries: list[dict], captured_at: str) -> str:
    lines = [
        "---",
        "type: pain-point",
        f"pain: {pain}",
        f"updated_at: {captured_at}",
        "tags:",
        "  - amazon",
        "  - pain-point",
        "---",
        "",
        f"# 痛点：{md_escape(pain)}",
        "",
        f"> 自动更新时间：{captured_at}",
        "",
        "## 出现记录",
        "",
    ]
    for item in entries:
        lines.extend([
            f"- [[{item['asin']}]] | {item['region']} | {md_escape(item['keyword'])} | {item['count']} 次",
            f"  - 商品：{md_escape(item['title'])[:120]}",
            f"  - 调研：[[02_调研报告/{item['report_name']}]]",
        ])
    lines.append("")
    lines.extend(["## 选品提示", ""])
    total = sum(item["count"] for item in entries)
    affected = len({item["asin"] for item in entries})
    lines.append(f"- 累计提及：{total} 次")
    lines.append(f"- 影响商品数：{affected} 个")
    lines.append("- 建议结合原始评论判断该痛点是真实需求、使用误区、物流问题还是单品质量问题。")
    lines.append("")
    return "\n".join(lines)


def export_pain_library(vault: Path, all_data: dict, keyword: str, region: str, report_name: str, captured_at: str):
    pain_dir = vault / "04_痛点库"
    grouped = {}
    for asin, info in all_data.items():
        ai = info.get("_ai", {}) or {}
        for pain in ai.get("pains", []) or []:
            name = pain.get("keyword", "")
            if not name:
                continue
            grouped.setdefault(name, []).append({
                "asin": asin,
                "title": info.get("title", ""),
                "keyword": keyword,
                "region": region,
                "count": int(pain.get("count", 0) or 0),
                "report_name": report_name,
            })
    for pain, new_entries in grouped.items():
        path = pain_dir / f"{safe_name(pain)}.md"
        # 追加模式：合并历史记录，同一 ASIN+report 去重
        all_entries = list(new_entries)
        if path.exists():
            existing = extract_frontmatter(str(path))
            old_entries = existing.get("entries", []) or []
            seen = {(e.get("asin",""), e.get("report_name","")) for e in new_entries}
            for e in old_entries:
                if (e.get("asin",""), e.get("report_name","")) not in seen:
                    all_entries.append(e)
        write_file(path, render_pain_note(pain, all_entries, captured_at))


def export_obsidian(all_data: dict, keyword: str, region: str, sort: str, domain: str, docx_path: str = "", vault_path: str = DEFAULT_VAULT, run_id: str = "", db_path: str = ""):
    import sys, os
    captured_at = time.strftime("%Y-%m-%d %H:%M:%S")
    run_id = run_id or time.strftime("%Y%m%d_%H%M%S")
    db_path = db_path or os.environ.get("AMAZON_REVIEW_PIPELINE_DB",
                str(Path.home() / ".amazon_review_pipeline" / "pipeline.db"))
    vault_path = vault_path or DEFAULT_VAULT
    vault = Path(vault_path).expanduser()
    report_dir = vault / "02_调研报告"
    asin_dir = vault / "03_ASIN卡片"
    keyword_slug = safe_name(keyword)
    report_name = f"{run_id}_{region}_{keyword_slug}_{sort}.md"
    report_path = report_dir / report_name
    report_content = render_report_note(all_data, keyword, region, sort, domain, docx_path, captured_at)
    write_file(report_path, report_content)
    export_pain_library(vault, all_data, keyword, region, run_id, captured_at)
    asin_paths = []
    for asin, info in all_data.items():
        card_path = asin_dir / f"{asin}.md"
        card_content = render_product_card(asin, info, keyword, region, sort, domain, captured_at, run_id=run_id, db_path=db_path)
        write_file(card_path, card_content)
        asin_paths.append(str(card_path))
    dashboard = vault / "00_Dashboard.md"
    write_file(dashboard, render_dashboard(vault, all_data, captured_at))
    # 回填 Obsidian 引用到 SQLite
    try:
        from storage import connect as db_connect
        conn = db_connect(db_path)
        conn.execute(
            "UPDATE category_summary SET report_obsidian_path = ? WHERE run_id = ?",
            (str(report_path), run_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return {"report_path": str(report_path), "asin_paths": asin_paths, "vault_path": str(vault)}


def main():
    parser = argparse.ArgumentParser(description="导出 Amazon Review Pipeline 数据到 Obsidian Markdown")
    parser.add_argument("--input", "-i", required=True, help="reviews_*.json 路径")
    parser.add_argument("--keyword", "-k", required=True)
    parser.add_argument("--region", "-r", required=True)
    parser.add_argument("--sort", "-s", required=True)
    parser.add_argument("--domain", default="www.amazon.com")
    parser.add_argument("--docx-path", default="")
    parser.add_argument("--vault", default=DEFAULT_VAULT)
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()
    with open(args.input, "r", encoding="utf-8") as f:
        all_data = json.load(f)
    result = export_obsidian(
        all_data=all_data,
        keyword=args.keyword,
        region=args.region,
        sort=args.sort,
        domain=args.domain,
        docx_path=args.docx_path,
        vault_path=args.vault,
        run_id=args.run_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
