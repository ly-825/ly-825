# Amazon Review Pipeline 后续开发路线

## 当前能力基线

当前项目已经具备以下能力：

- **多站点 Amazon 支持**
  - 已支持 `us/uk/de/jp/fr/it/es/ca/in/au/mx/br/nl/se/pl`。
  - 已根据地区切换 Amazon 域名与评论源语言。

- **排行榜商品搜索**
  - 支持 `bestsellers`、`newreleases`、`moversandshakers`、`topreview`。
  - 可按关键词抓取候选 ASIN，并做基础去重。

- **评论抓取与翻译**
  - 使用 Chrome CDP 抓取 Amazon 评论。
  - 使用 MiniMax 将多语言评论翻译为中文。

- **AI 语义分析**
  - 单品维度提取用户场景、优点、痛点、代表评论。
  - 跨产品维度合并共性痛点并生成选品建议。

- **报告输出**
  - 生成 Word 图文报告。
  - 输出 `/tmp/asin_pool_*.json`、`/tmp/reviews_*.json`、`/tmp/report_*.docx`。
  - 支持飞书发送报告。

## 总体方向

后续不建议一次性改成“全域智能决策系统”。更稳妥的路线是：

```text
多站点竞品分析
-> 单次竞品分析
-> Amazon 数据资产化
-> 历史趋势对比
-> Google 长尾词扩展
-> RAG 历史复盘
-> 异常预警
-> TikTok / 社媒扩展
-> 跨平台机会判断
```

## Phase 0：多站点能力巩固

### 当前状态

多站点不是后续才做，当前代码已经具备基础能力。

当前 `scripts/pipeline.py` 已支持以下 Amazon 地区：

| 地区代码 | 站点 | 语言 |
|---|---|---|
| us | amazon.com | English |
| uk | amazon.co.uk | English |
| de | amazon.de | German |
| jp | amazon.co.jp | Japanese |
| fr | amazon.fr | French |
| it | amazon.it | Italian |
| es | amazon.es | Spanish |
| ca | amazon.ca | English |
| in | amazon.in | English |
| au | amazon.com.au | English |
| mx | amazon.com.mx | Spanish |
| br | amazon.com.br | Portuguese |
| nl | amazon.nl | Dutch |
| se | amazon.se | Swedish |
| pl | amazon.pl | Polish |

### 已实现能力

- **地区选择**
  - 交互模式会要求用户选择目标 Amazon 地区。
  - 参数模式支持 `--region us/de/jp/...`。

- **域名切换**
  - 根据地区自动切换 Amazon 域名。
  - 搜索页、评论页、商品链接和报告链接都会使用对应站点域名。

- **源语言切换**
  - 根据地区自动设置评论源语言。
  - MiniMax 翻译会按站点语言转成中文。

- **报告展示**
  - Word 报告中会展示地区对应的 Amazon 域名。
  - 商品链接会指向对应地区站点。

### 后续需要增强的多站点能力

- **价格标准化**
  - 当前价格保留页面原文。
  - 后续应拆分为 `currency`、`price_amount`、`price_text`。

- **销量 / 排名标准化**
  - 不同站点的 BSR、销量文案和类目名称不同。
  - 后续应统一保存原文和解析后的数值。

- **类目映射**
  - 各站点类目体系不完全一致。
  - 后续应建立跨站点类目映射表。

- **跨站点对比**
  - 支持同一关键词在多个站点同时分析。
  - 输出不同市场的价格带、评分、痛点、竞争强度差异。

- **多站点批量运行**
  - 当前一次运行只选一个地区。
  - 后续可增加 `--regions us,jp,de` 参数，一次生成多市场对比报告。

### 建议新增命令

```bash
python3 scripts/pipeline.py \
  --keyword "water bottle" \
  --regions us,jp,de \
  --sort bestsellers \
  --max-products 5 \
  --yes
```

### 多站点增强验收标准

- 可以一次输入多个地区。
- 每个地区独立采集、独立保存快照。
- 最终生成一个跨站点对比报告。
- 报告能回答：
  - 哪个市场价格带更高？
  - 哪个市场评分更低？
  - 哪个市场差评痛点更集中？
  - 哪个市场竞争更弱、更适合作为蓝海机会？

## Phase 1：数据资产化

### 目标

把当前“一次性报告”升级为“可沉淀、可复盘、可对比”的本地数据资产。

### 建议新增模块

```text
scripts/storage.py
scripts/snapshot.py
scripts/models.py
```

### 建议数据存储

MVP 阶段优先使用 SQLite，后续可迁移到 PostgreSQL。

### 核心数据表

#### product_snapshot

保存每次采集到的商品快照。

| 字段 | 说明 |
|---|---|
| id | 自增主键 |
| asin | 商品 ASIN |
| region | 地区代码 |
| domain | Amazon 域名 |
| keyword | 搜索关键词 |
| sort | 榜单类型 |
| title | 商品标题 |
| brand | 品牌 |
| price | 价格原文 |
| rating | 评分原文 |
| review_count | 本次抓到的评论数 |
| monthly_sales | 页面展示的月销量 |
| sales_rank | 页面展示的排名 |
| product_url | 商品链接 |
| product_image | 商品图片 |
| captured_at | 采集时间 |

#### review_snapshot

保存评论明细。

| 字段 | 说明 |
|---|---|
| id | 自增主键 |
| asin | 商品 ASIN |
| region | 地区代码 |
| rating | 评论星级 |
| author | 评论作者 |
| review_date | 评论日期原文 |
| verified | 是否 Verified |
| title | 原文标题 |
| body | 原文正文 |
| title_zh | 中文标题 |
| body_zh | 中文正文 |
| captured_at | 采集时间 |

#### analysis_report

保存 AI 分析结论。

| 字段 | 说明 |
|---|---|
| id | 自增主键 |
| report_type | product / cross_product / trend |
| keyword | 关键词 |
| region | 地区 |
| sort | 榜单类型 |
| asins | ASIN 列表 JSON |
| content_json | 结构化分析 JSON |
| docx_path | Word 报告路径 |
| created_at | 创建时间 |

### 需要改动的位置

- `scripts/pipeline.py`
  - 在 `step_search_products` 后保存 ASIN 候选快照。
  - 在 `step_scrape_reviews` 后保存商品和评论快照。
  - 在 `step_analyze_docx` 后保存分析结果索引。

### 新增命令建议

```bash
python3 scripts/pipeline.py \
  --keyword "water bottle" \
  --region us \
  --sort bestsellers \
  --max-products 5 \
  --save-db \
  --yes
```

### 验收标准

- 跑完一次 pipeline 后，本地数据库中能查到商品快照。
- 同一个关键词第二天再次运行后，能保留两天的历史数据。
- 不影响现有 Word 报告生成和飞书发送。

## Phase 2：连续对比分析

### 目标

在已有快照基础上，生成“今日 vs 昨日 vs 上周”的趋势报告。

### 建议新增模块

```text
scripts/trend_analyzer.py
```

### 首批规则

- **价格异常**
  - 当前价格较上次下降或上升超过 10%。

- **BSR / 排名异常**
  - 排名提升或下降超过 30%。

- **评论增长异常**
  - 评论数增长超过过去 7 日均值的 2 倍。

- **评分异常**
  - 评分下降超过 0.2。

- **差评异常**
  - 1-2 星评论占比明显上升。

### 输出形式

```bash
python3 scripts/trend_analyzer.py \
  --keyword "water bottle" \
  --region us \
  --days 7
```

输出：

```text
📈 Amazon 竞品趋势分析
关键词：water bottle | 地区：美国 | 时间窗口：7天

1. B0XXXX 排名显著提升
   - 上次：#125
   - 当前：#62
   - 变化：提升 50.4%
   - 可能原因：价格下降、评论增长、站内活动

2. B0YYYY 差评风险上升
   - 1-2星评论占比：8% -> 15%
   - 高频问题：漏水、盖子松动
```

### 验收标准

- 至少能对同一 ASIN 的两次快照做对比。
- 能输出规则触发原因。
- AI 只负责解释，不负责生成底层数值。

## Phase 3：Google 长尾词扩展

### 目标

补充 Amazon 站外搜索意图，辅助识别蓝海关键词。

### 建议新增模块

```text
scripts/google_keywords.py
```

### MVP 数据源

优先使用 Google Suggest：

```text
https://suggestqueries.google.com/complete/search?client=firefox&q=KEYWORD
```

### 输出字段

| 字段 | 说明 |
|---|---|
| seed_keyword | 种子词 |
| suggestion | 联想词 |
| source | google_suggest |
| locale | 地区语言 |
| captured_at | 采集时间 |

### 新增命令建议

```bash
python3 scripts/google_keywords.py \
  --keyword "water bottle" \
  --locale en-US
```

### 报告集成建议

在 Word 报告中新增一个独立章节：

```text
🔎 Google 长尾词机会
```

内容包括：

- 高潜力长尾词
- 用户搜索意图分类
- 可用于标题 / 五点描述 / 广告投放的关键词

### 风险

- Google 可能限制请求频率。
- 大规模抓取建议后续接 SerpAPI 或 DataForSEO。

## Phase 4：RAG 历史复盘

### 目标

让用户可以用自然语言查询历史分析结论。

### 推荐原则

不要把所有结构化指标塞进向量库。

- **结构化数据**
  - 商品快照、价格、BSR、评分、评论数放 SQLite/PostgreSQL。

- **非结构化内容**
  - AI 分析结论、选品建议、评论摘要、用户笔记放向量库。

### 建议新增模块

```text
scripts/knowledge_base.py
scripts/query_history.py
```

### MVP 向量库选择

- Chroma
- LanceDB
- Qdrant

本地优先推荐 Chroma 或 LanceDB。

### 查询示例

```bash
python3 scripts/query_history.py \
  --question "过去一个月水杯类目最大的差评痛点是什么？"
```

### 回答要求

- 必须引用历史报告或快照时间。
- 必须区分事实数据和 AI 推测。
- 不允许凭空编造没有采集过的数据。

## Phase 5：异常预警与主动推送

### 目标

从“用户主动查询”升级为“系统主动提醒”。

### 建议新增模块

```text
scripts/watchlist.py
scripts/alert.py
```

### 功能范围

- 关注关键词
- 关注 ASIN
- 每日自动采集
- 触发异常规则
- 生成日报 / 周报
- 飞书推送

### watchlist 示例

```json
{
  "items": [
    {
      "keyword": "water bottle",
      "region": "us",
      "sort": "bestsellers",
      "max_products": 10,
      "enabled": true
    }
  ]
}
```

### 验收标准

- 可以配置一个关键词每日跑一次。
- 异常时能生成简短报告。
- 能复用现有飞书发送能力。

## Phase 6：社媒情报扩展

### 目标

加入 TikTok / Instagram / Facebook 等站外趋势信号。

### 推荐顺序

1. TikTok hashtag 趋势
2. TikTok 达人/视频互动数据
3. Instagram 话题趋势
4. Facebook 广告素材或公开讨论

### 实现建议

优先接第三方 API，不建议一开始自研深度爬虫。

可评估的数据源：

- TikTok Creative Center
- Apify
- SerpAPI
- DataForSEO
- Meta Ads Library

### 风险

- API 权限限制。
- 反爬维护成本高。
- 平台条款和合规风险高。
- 社媒热度与 Amazon 销量之间只能先做相关性提示，不要直接做因果结论。

## 推荐实施顺序

### 第 1 周

- 新增 SQLite 存储层。
- 保存商品快照、评论快照、报告索引。
- 增加 `--save-db` 参数。

### 第 2 周

- 新增趋势对比脚本。
- 支持同 ASIN 多次快照对比。
- 输出文本版趋势报告。

### 第 3 周

- 新增 Google Suggest 关键词扩展。
- 把长尾词章节集成到 Word 报告。

### 第 4 周

- 新增 watchlist 配置。
- 支持定时任务和飞书异常提醒。

### 后续

- 引入 RAG 查询。
- 接入 TikTok 数据源。
- 做跨平台趋势相关性分析。

## 不建议优先做的事项

- **不建议一开始重构整个 pipeline**
  - 当前脚本已经能跑通，先旁路新增模块更稳。

- **不建议一开始接全量社媒平台**
  - 数据源和合规风险高。

- **不建议把结构化指标全部放进向量库**
  - 趋势、价格、排名更适合数据库查询。

- **不建议让 AI 直接判断异常数值**
  - 异常触发应由规则或统计逻辑完成，AI 只负责解释和总结。

## 下一步最小改造任务

如果开始实现，建议第一批代码任务如下：

1. 新建 `scripts/storage.py`
   - 初始化 SQLite。
   - 创建 `product_snapshot`、`review_snapshot`、`analysis_report` 三张表。
   - 提供 `save_pipeline_run()` 方法。

2. 修改 `scripts/pipeline.py`
   - 增加 `--save-db` 参数。
   - 在 `run()` 结束前调用存储层。
   - 保持默认行为不变。

3. 新建 `scripts/trend_analyzer.py`
   - 读取 SQLite。
   - 按 `asin + region` 对比最近两次快照。
   - 输出价格、评分、评论数、排名变化。

4. 更新 `SKILL.md`
   - 增加“历史数据沉淀”和“趋势对比”的使用说明。
   - 保留当前强制提问规则。

## 完成标志

第一阶段完成后，项目应从：

```text
输入关键词 -> 生成一次性 Word 报告
```

升级为：

```text
输入关键词 -> 生成 Word 报告 -> 保存历史快照 -> 可进行趋势对比
```
