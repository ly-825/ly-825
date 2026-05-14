#!/usr/bin/env python3
"""
Agent Obsidian 笔记写入工具
Agent 分析完数据后调用此脚本，将洞察写入 Obsidian vault

用法：
  python3 obsidian_note.py --title "水杯市场机会分析" \
    --content "分析内容 markdown..." \
    --tags "amazon,water-bottle,机会分析" \
    --dir "01_市场洞察"
"""

import argparse, os, time
from pathlib import Path

VAULT = str(Path.home() / "Documents" / "Obsidian" / "Amazon选品")


def safe_name(text: str) -> str:
    import re
    text = text.strip()
    text = re.sub(r"[\\/:*?\"<>|#\[\]]+", "-", text)
    text = re.sub(r"\s+", "-", text)
    return text.strip("-._ ")[:80]


def main():
    parser = argparse.ArgumentParser(description="写入 Obsidian 笔记")
    parser.add_argument("--title", "-t", required=True, help="笔记标题")
    parser.add_argument("--content", "-c", default="", help="Markdown 内容")
    parser.add_argument("--tags", default="", help="标签，逗号分隔")
    parser.add_argument("--dir", "-d", default="01_类目复盘", help="目标目录")
    parser.add_argument("--vault", default=VAULT)
    args = parser.parse_args()

    vault = Path(args.vault).expanduser()
    target_dir = vault / args.dir
    target_dir.mkdir(parents=True, exist_ok=True)

    tag_list = [t.strip() for t in args.tags.split(",") if t.strip()]
    tag_yaml = "\n".join(f"  - {t}" for t in tag_list)

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    filename = safe_name(args.title) + ".md"
    filepath = target_dir / filename

    content = args.content
    # 支持从 stdin 读取内容
    if not content and not os.isatty(0):
        content = os.sys.stdin.read()

    note = f"""---
title: {args.title}
created: {now}
tags:
{tag_yaml}
---

# {args.title}

{content}

---
*由 Agent 于 {now} 自动生成*
"""
    filepath.write_text(note, encoding="utf-8")
    print(f"✅ 笔记已写入: {filepath}")


if __name__ == "__main__":
    main()
