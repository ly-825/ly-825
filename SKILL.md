---
name: amazon-review-pipeline
description: 全自动 Amazon 选品 pipeline。当用户明确要求去 Amazon 抓取/分析/调研某品类时触发，如"帮我去 Amazon 抓一下XX"、"去 Amazon 分析一下XX"、"跑个选品调研"、"帮我跑这个分析"、"去抓数据"、"开始选品分析"、"跑一下 amazon"、"run the pipeline"、"抓取评论分析"。不匹配用户泛泛询问品类市场情况的问题——那些由 Agent 用知识库直接回答。
---

# amazon-review-pipeline

全自动 Amazon 选品评论分析 pipeline：**仅在 Agent 引导用户确认后**，用户明确说"要/好的/去抓/跑一下"时才执行**。执行链路：提取品类+翻译关键词 → 问两个参数 → 执行 pipeline.py → 输出报告 → 完成。

---

## ⛔ 执行时机规则（CRITICAL）

**此 Skill 不是选品问题的入口，是漏斗末端。触发条件：Agent 已引导用户，用户明确回复"要/好的/去抓/帮我分析"。**

**Agent 在引导之前不得触发此 Skill。**

---

## ⛔ 必问两个确认项（禁止跳过）

**触发后，先提取品类和关键词，然后必须问以下两个问题，等用户回复后再执行 pipeline。**

### 必问问题（两条必须都问，合并在一条消息里）

```
好的，我来帮你分析「{品类}」（搜索词：{英文关键词}）在 Amazon 美国站的情况。

先确认两个参数：
1. 排行榜类型：
   1️⃣ 销量榜（Best Sellers）—— 最畅销、存量市场
   2️⃣ 新品榜（New Releases）—— 近期新品机会
   3️⃣ 飙升榜（Movers & Shakers）—— 增速最快的品
   4️⃣ 评论榜（Top Rated）—— 口碑最好的竞品
2. 分析几个产品？（建议 3-10，越多越慢）
```

### 执行时机

- ❌ **禁止**：识别到意图后直接跑 pipeline
- ✅ **正确**：识别意图 → 提取关键词 → 问两个问题 → 等用户回复 → 执行

### 命令格式

收到用户回复后，按以下格式拼接命令（三个参数都是**必填**）：

**重要：飞书报告发送到触发者本人。** 你必须从 conversation 上下文（inbound_meta）中找到当前用户的飞书 open_id（格式为 `ou_xxx`），加上 `--feishu-target "ou_xxx"` 参数。这样 Word 报告会直接发到触发者的飞书窗口，而不是固定发到你的 open_id。

```bash
python3 ~/.openclaw/workspace/skills/amazon-review-pipeline/scripts/pipeline.py \
  --keyword "英文关键词" \
  --sort 排行榜英文 \
  --max-products 数量 \
  --feishu-target "触发者的飞书open_id" \
  --yes
```

排行榜英文映射：销量榜=bestsellers，新品榜=newreleases，飙升榜=moversandshakers，评论榜=topreview

---

## 前置条件

Chrome 已登录 Amazon，开启调试端口：

```bash
nohup "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=18800 \
  --user-data-dir="$HOME/Library/Application Support/Google/Chrome" \
  --remote-allow-origins='*' > /tmp/chrome.log 2>&1 &

curl -s http://localhost:18800/json/version
```

---

## 执行命令

```bash
# 交互模式（推荐）
python3 ~/.openclaw/workspace/skills/amazon-review-pipeline/scripts/pipeline.py

# 参数模式
python3 pipeline.py --keyword "laptop bag" --sort bestsellers --max-products 3 --feishu-target "ou_xxx" --yes
```

---

## 输出文件

| 文件 | 路径 | 说明 |
|------|------|------|
| ASIN 候选池 | `/tmp/asin_pool_YYYYMMDD_HHMMSS.json` | 搜索到的产品列表 |
| 评论数据（含翻译） | `/tmp/reviews_YYYYMMDD_HHMMSS.json` | 抓取的评论+中文翻译 |
| **Word 分析报告** | `/tmp/report_YYYYMMDD_HHMMSS.docx` | **主报告，内嵌产品图片+评论详情+AI 分析（MiniMax 生成）** |

**⚠️ Pipeline 完成后：**
1. 生成 `/tmp/report_*.docx`（Word 报告，含产品信息+评论详情+图片，**AI 分析已写入——基于 MiniMax 阅读真实评论生成**）
2. 自动 **发送 .docx 到飞书**（手机可直接下载打开）
3. 自动 **打开 Word 文件**（桌面端查看）

---

## Pipeline 完成后 Agent 的执行流程

Pipeline 自动完成以下步骤：
- ✅ 生成 `.docx` 报告
- ✅ 生成 **PDF** 版本（手机飞书可直接打开预览）
- ✅ 发送报告到飞书
- ✅ 打开 Word 文件

**Agent 只需要做一件事**：在聊天窗口输出完整报告摘要（格式见下方"报告格式"章节）。

### 可选：如果 Agent 觉得分析不够好，可以覆盖

```bash
# 覆盖自动分析（替换 Word 中的 [AgentOverride:ANALYSIS] 标记）
# 然后重新生成 PDF 并发送
# 命令见下方函数签名
```

如果 Agent 读了评论后觉得自己能写出更高质量的分析：

1. 读取 `/tmp/reviews_*.json`，撰写深度分析
2. 调用 `append_analysis_to_docx()` 覆盖自动分析

```bash
# 1. 写入分析到临时文件
cat > /tmp/agent_analysis.md << 'ANALYSIS_EOF'
## 🔍 共性痛点分析

### {痛点名称}（{X}条，占比{Y}%）
{深度解读，不是简单罗列关键词}

| 痛点 | 频次 | 解读 |
|------|------|------|
| {痛点1} | {X}条 | {行业解读} |
| {痛点2} | {X}条 | {行业解读} |

## 💡 选品建议

1. **{建议1}**：{数据支撑的具体建议}
2. **{建议2}**：{数据支撑的具体建议}
ANALYSIS_EOF

# 2. 覆盖 Word 中的自动分析
python3 -c "
import sys; sys.path.insert(0, '$HOME/.openclaw/workspace/skills/amazon-review-pipeline/scripts')
import pipeline

with open('/tmp/agent_analysis.md', 'r') as f:
    analysis_md = f.read()

pipeline.append_analysis_to_docx('/tmp/report_XXXXXX.docx',
    per_product_insights={},    # 不覆盖产品洞察（已经有了）
    analysis_md=analysis_md
)
"

# 3. 打开 + 飞书
open /tmp/report_XXXXXX.docx
python3 -c "
import sys; sys.path.insert(0, '$HOME/.openclaw/workspace/skills/amazon-review-pipeline/scripts')
import pipeline, asyncio
asyncio.run(pipeline.send_report_to_feishu('/tmp/report_XXXXXX.docx', keyword='关键词'))
"
```

---

## 报告格式（已固定，禁止改动）

每次 pipeline 完成后，Agent 必须将 `/tmp/report_YYYYMMDD_HHMMSS.docx` 路径告知用户，并在聊天窗口输出以下格式的文字摘要，**不得遗漏任何产品**：

```
📦 Amazon {品类关键词} 选品分析报告
关键词：{品类} | 排行榜：{榜单类型} | 产品数：{N} | 日期：{YYYY-MM-DD}

[每个产品单独一段：]

📦 #{排名} {ASIN}
产品标题：{完整标题}
链接：https://www.amazon.com/dp/{ASIN}
价格：{价格} | 评分：{评分} | 月销量：{真实月销量}
🎯 用户场景：{场景1}、{场景2}
✅ 核心优点：{优点1}、{优点2}、{优点3}
⚠ 痛点标签：{标签1}、{标签2}、{标签3}
💬 典型评论
⭐ {星级} | {Verified Purchase 或其他}
{评论正文，完整摘录，最多300字}
MEDIA: {product_image URL}

────────────────────────────────────────────────────────────
```

**格式规则：**
- 每个产品必须有：ASIN、价格、评分、月销量（仅真实数据，不估算）、链接、产品图片（MEDIA: 标签，紧跟典型评论之后）、用户场景、核心优点、痛点标签、典型评论（含星级+是否Verified）
- 🎯用户场景、✅核心优点、⚠痛点标签 由 Agent 根据评论内容自行分析得出，无品类限制
- **MEDIA: 标签必须放在每个产品段落末尾、分隔线之前**
- **必须使用 `product_image` 字段的 URL，不得省略**
- 共性痛点必须用表格呈现，包含出现次数和解读
- **必须包含全部产品，不得只列部分**
- **不得省略链接**
- 共性痛点分析+选品建议由 Agent 通过 `append_analysis_to_docx()` **替换** Word 中的自动生成内容，确保痛点表格有深度解读、选品建议有数据支撑

---

## Word 报告生成（主交付物）

**位置：** `/tmp/report_YYYYMMDD_HHMMSS.docx`

**标题：** `📦 Amazon {关键词} 选品分析报告`

**基本信息行：**
```
关键词：{品类} | 排行榜：{销量榜/新品榜/飙升榜/评论榜} | 产品数：{N} | 日期：{YYYY-MM-DD}
```

**每个商品结构（按顺序，不可省略）：**

1. **标题**：`📦 #{排名} {ASIN}`（Heading 1）
2. **内容段落：**
   ```
   产品标题：{完整标题}
   链接：https://www.amazon.com/dp/{ASIN}
   价格：{价格} | 评分：{评分} | 月销量：{真实月销量（仅真实数据，不估算）}
   ```
3. **用户洞察（🎯/✅/⚠ 由 MiniMax AI 基于评论自动分析）**：
   ```
   🎯 用户场景：{场景1}、{场景2}
   ✅ 核心优点：{优点1}、{优点2}
   ⚠ 痛点标签：{痛点1}、{痛点2}
   ```
   （AI 分析失败时留白色隐藏占位符 `[INSIGHT_PLACEHOLDER:{ASIN}]`，Agent 可填充）
4. **商品图片（必须插入）：**
   ```python
   doc.add_picture(img_path, width=Inches(3.0))
   ```
   在用户洞察之后插入产品图片
5. **典型评论**
6. **全部评论详情**
7. **分隔线：**
   ```
   ────────────────────────────────────────────────────────────
   ```

**共性痛点分析 & 选品建议（由 MiniMax AI 自动生成，Agent 可覆盖）：**

Pipeline 使用 MiniMax API 基于真实评论数据生成分析：
- 产品洞察（🎯/✅/⚠）— 逐产品场景/优点/痛点
- 共性痛点分析 — 跨产品痛点表格 + 行业解读
- 选品建议 — 数据驱动的市场机会

**Agent 覆盖方式**：调用 `append_analysis_to_docx()` 并传入 `analysis_md` 参数，会自动找到 `[AgentOverride:ANALYSIS]` 标记替换为 Agent 的分析。

**固定参数：**
- 评论页数：3页（固定，不询问）
- 商品数量：默认10个（用户可调整）
- 图片宽度：3.0英寸

**禁止事项：**
- ❌ 不得省略任何段落
- ❌ 不得自行添加非模板规定的段落
- ❌ 不得改变标题、基本信息行的格式
- ❌ 不得跳过图片插入
- ❌ 不得使用与模板不符的样式

- **引擎**：MiniMax M2.7 API（英→中）
- **字段**：`title_zh` / `body_zh`
- **失败策略**：翻译失败时 `title_zh`/`body_zh` 留空，报告显示原文；**不**把原文 fallback 填入翻译字段（避免污染中文痛点匹配）
- **并发控制**：每批 3 条，间隔 0.5s，避免 529 超限
- **超时**：120s / 批

---

## Pipeline 六步骤

```
① ASIN 搜索        → Amazon 排行榜滚动抓取 ASIN
② 产品去重         → title[:60] + brand + price 相同视为同一产品变体
③ 评论抓取        → 滚页加载，每产品独立 Chrome 实例
④ AI 翻译         → MiniMax 批量翻译（英→中）
⑤ AI 分析+报告    → MiniMax 分析评论→生成产品洞察+痛点分析+选品建议→写入 Word
⑥ 卖家联系方式     → Seller ID → 天眼查公司详情 → CSV（可选步骤）
```

---

## 第六步：卖家联系方式（可选）

输入：`reviews_*.json`（步骤③的输出）  
输出：`/tmp/sellers_contact_YYYYMMDD_HHMMSS.csv`

```bash
python3 ~/.openclaw/workspace/skills/amazon-review-pipeline/scripts/seller_contact.py \
  --input /tmp/reviews_YYYYMMDD_HHMMSS.json \
  --port 9222
```

**前置条件：** Chrome 已登录天眼查 + Amazon，开启调试端口 9222

**流程：** ASIN → CDP抓 Seller ID → CDP抓 Seller Profile（业务名）→ 天眼查搜索 → 进详情页 → 提取电话/邮箱/法人/注册资本

**输出字段：** asin, brand, seller_id, business_name, company_name, phone, email, legal_rep, reg_capital, address, tianyancha_url

---

---

## 品类分析说明

**Pipeline 适用于任何品类，无硬编码关键词限制。** Agent 根据实际评论内容自行提取：
- 🎯 用户场景：从评论中识别用户在什么场景下使用该产品
- ✅ 核心优点：从好评中提取用户反复提及的产品优势
- ⚠ 痛点标签：从差评/中性评中提取用户反馈的问题
- 🔍 共性痛点：跨产品的共性问题分析（含频次和解读）
- 💡 选品建议：基于数据的市场机会和差异化建议

---

## 评论 JSON 结构

```json
{
  "ASIN": {
    "asin": "B0C96NNJ86",
    "title": "MOSISO 360 Protective Laptop Shoulder Bag...",
    "brand": "MOSISO",
    "price": "$28.99",
    "rating": "4.7 out of 5",
    "reviews": [
      {
        "rating": "5.0 out of 5 stars",
        "author": "John D.",
        "date": "March 15, 2026",
        "verified": true,
        "title": "Best Laptop Case!",
        "body": "The MOSISO 360 is an excellent choice...",
        "title_zh": "最佳笔记本电脑保护套！",
        "body_zh": "MOSISO 360 是一款兼顾保护性和便捷性的绝佳选择..."
      }
    ]
  }
}
```

---

## 已知问题

- MiniMax API 高并发时可能返回 529，翻译字段留空，报告显示英文原文，属正常降级
- Chrome 端口默认为 18800（WorkBuddy 已配置），若连接失败请检查 Chrome 是否在 18800 端口运行

---

## 示例对话

> **用户**：帮我分析一下鼠标垫
> **Agent**：好的！先确认两个参数：
> 1. 排行榜类型：
>    1. 销量榜（Best Sellers）
>    2. 新品榜（New Releases）
>    3. 飙升榜（Movers & Shakers）
>    4. 评论榜（Top Rated）
> 2. 分析几个产品？（建议 3-10）
> **用户**：2，3个
> **Agent**：✅ 新品榜 × 3 个产品，开始跑...（约 10-20 分钟）

> **用户**：跑一下水杯
> **Agent**：好的！先确认两个参数：
> 1. 排行榜类型：
>    1. 销量榜（Best Sellers）
>    2. 新品榜（New Releases）
>    3. 飙升榜（Movers & Shakers）
>    4. 评论榜（Top Rated）
> 2. 分析几个产品？（建议 3-10）
> **用户**：1，5
> **Agent**：✅ 销量榜 × 5 个产品，开始跑...

### Pipeline 跑完后：

> **Pipeline 输出**：📄 Word报告: /tmp/report_20260506_143000.docx 🤖 AI 分析已写入 Word
>
> **Agent 流程 A（推荐）**：直接打开 + 发飞书（Word 已含 AI 分析）
> ```bash
> open /tmp/report_20260506_143000.docx
> python3 -c "import sys; sys.path.insert(0, '$HOME/.openclaw/workspace/skills/amazon-review-pipeline/scripts'); import pipeline, asyncio; asyncio.run(pipeline.send_report_to_feishu('/tmp/report_20260506_143000.docx', keyword='water bottle'))"
> ```
>
> **Agent 流程 B（需要更深分析时）**：覆盖自动分析再打开
> 1. 读取评论，写深度分析
> 2. `append_analysis_to_docx()` 覆盖
> 3. 打开 + 飞书
