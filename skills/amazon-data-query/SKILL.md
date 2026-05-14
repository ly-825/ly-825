---
name: amazon-data-query
description: >
  查询本地 Amazon 选品历史数据库。当用户想了解已有数据的趋势、对比、
  排名、痛点、摘要时使用。Agent 根据用户意图自主判断是否调用，
  不依赖关键词匹配。
alwaysActive: true
---

# Amazon 数据查询

不要自己写 SQL。根据用户意图选择对应命令，输出 JSON 结果后解读给用户。

## 命令速查

```bash
SCRIPT=~/.openclaw/workspace/skills/amazon-review-pipeline/scripts/query_db.py

# 趋势（多次调研对比）
python3 $SCRIPT --trend "关键词" --days 30

# 跨地区对比
python3 $SCRIPT --compare "关键词"
python3 $SCRIPT --compare "关键词" --regions us,uk

# 品类机会排名
python3 $SCRIPT --ranking --days 30

# 单品类摘要
python3 $SCRIPT --summary "关键词"

# 痛点统计（全品类或指定）
python3 $SCRIPT --pains
python3 $SCRIPT --pains "关键词"

# 列出所有已调研品类
python3 $SCRIPT --categories
```

## 用户意图 → 命令映射

| 用户说 | 用什么命令 |
|--------|-----------|
| XX的趋势/变化/走向 | `--trend "XX"` |
| 比较/对比 XX 各地区 | `--compare "XX"` |
| 哪个品类机会最大/排名 | `--ranking` |
| XX 有什么痛点/问题 | `--pains "XX"` |
| 总结一下 XX / XX怎么样 | `--summary "XX"` |
| 有哪些品类 / 调研过什么 | `--categories` |

## 超出命令范围的查询

如果用户问题无法用以上 6 个命令覆盖（比如「评论里最常提到什么材质」「20-30美元区间有什么产品」），可以自己写 SQL 直查原始表。

数据库路径：`~/.amazon_review_pipeline/pipeline.db`

```bash
# 直查原始评论表（可分页、过滤、模糊搜索）
python3 -c "
import sqlite3, json
conn = sqlite3.connect('$HOME/.amazon_review_pipeline/pipeline.db')
conn.row_factory = sqlite3.Row
rows = conn.execute('''SELECT asin, region, body_zh, title_zh, rating, review_date
    FROM review_snapshot WHERE keyword LIKE ? AND body_zh LIKE ?
    ORDER BY captured_at DESC LIMIT 10''', ('%水杯%', '%漏水%')).fetchall()
for r in rows:
    print(f\"⭐{r['rating']} | {r['asin']} | {r['body_zh'][:100]}\")
"
```

```bash
# 直查品类总结表（可按条件过滤、排序）
python3 -c "
import sqlite3, json
conn = sqlite3.connect('$HOME/.amazon_review_pipeline/pipeline.db')
conn.row_factory = sqlite3.Row
rows = conn.execute('''SELECT * FROM category_summary
    WHERE avg_price BETWEEN ? AND ? ORDER BY opportunity_score DESC''', (20, 50)).fetchall()
for r in rows:
    print(f\"{r['keyword']}({r['region']}) 均价\${r['avg_price']} 机会分{r['opportunity_score']}\")
"
```

## 回答规则

1. **标注数据来源**：说明用了哪个命令或查询了哪张表
2. **不编造数据**：结果里没有的不要自己补充
3. **币种提醒**：不同地区价格币种不同，比较时提醒用户
4. **不加 pipeline 引导**：数据已有，不需要重新抓
