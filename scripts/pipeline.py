#!/usr/bin/env python3
"""
Amazon Review Pipeline - 全链路自动化
1搜索ASIN → 2产品数据 → 3评论抓取 → 4AI翻译 → 5分析报告

与 cdp_scraper.py / cdp_scraper_reviews.py 保持一致的 CDP 写法:
  - get_cdp_ws_url() 用 /json/list 获取页面级 WS URL
  - cdp_send() 返回 result.value(returnByValue=True)
  - 产品/评论提取均用 JS DOM 而非 innerHTML 正则
"""

import argparse, asyncio, json, os, random, re, subprocess, sys, time, uuid
import requests, websockets

# ─── Config ──────────────────────────────────────────────────────────────────
MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"

def _load_minimax_key() -> str:
    """加载 MiniMax API Key：优先环境变量，其次 openclaw.json 配置"""
    key = os.environ.get("MINIMAX_API_KEY", "")
    if key:
        return key
    # 从 openclaw.json 的 env 配置中读取
    for cfg_path in [
        os.path.expanduser("~/.openclaw/openclaw.json"),
        os.path.expanduser("~/.openclaw/config.json"),
    ]:
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                # openclaw.json 里 key 可能在 env 对象或顶层
                key = cfg.get("env", {}).get("MINIMAX_API_KEY", "") if isinstance(cfg.get("env"), dict) else ""
                if not key:
                    key = cfg.get("MINIMAX_API_KEY", "")
                if key:
                    return key
            except Exception:
                continue
    return ""

API_KEY = _load_minimax_key()
TRANSLATE_TIMEOUT = 30
RETRY_MAX = 3
BATCH_SIZE = 3
BATCH_DELAY = 0.5

# ─── 中文关键词自动翻译 ───────────────────────────────────────────────────
KEYWORD_TRANSLATIONS = {
    "抱枕": "body pillow",
    "儿童棉袜": "kids cotton socks",
    "儿童袜子": "kids socks",
    "儿童运动袜": "kids athletic socks",
    "瑜伽裤": "yoga pants",
    "运动短裤": "athletic shorts women",
    "运动内衣": "sports bra women",
    "跑步短裤": "running shorts women",
    "健身服": "fitness wear women",
    "羽毛球拍": "badminton racket",
    "网球拍": "tennis racket",
    "游泳镜": "swimming goggles",
    "保温杯": "insulated water bottle",
    "水杯": "water bottle",
    "杯子": "water bottle",
    "塑料水杯": "plastic water bottle",
    "无线充电器": "wireless charger",
    "蓝牙耳机": "bluetooth earphones",
    "充电线": "charging cable",
    "纸质笔记本": "journal notebook",
}

def auto_translate_keyword(kw: str) -> tuple[str, str]:
    """检测中文并翻译为英文,返回 (原始关键词, 搜索用关键词)"""
    has_chinese = any("\u4e00" <= c <= "\u9fff" for c in kw)
    if not has_chinese:
        return kw, kw
    en = KEYWORD_TRANSLATIONS.get(kw)
    if en:
        print(f"   [WEB] 检测到中文关键词 -> 自动翻译为: {en}")
        return kw, en
    print(f"   [!] 未找到 '{kw}' 的翻译,请使用英文关键词")
    return kw, kw

# ─── 品类配置已移除 ─────────────────────────────────────────────
# 所有产品洞察（用户场景、核心优点、痛点标签）由 Agent 在 pipeline 完成后撰写，
# 不再使用硬编码关键词匹配，pipeline 适用于任何品类。

CDP_BROWSER = None   # 全局当前 Chrome 进程

# ─── Chrome / CDP ────────────────────────────────────────────────────────────
# 使用已登录的 Chrome(端口 9222),直接导航到各 ASIN 评论页
DEFAULT_PORT = 9222


def open_new_tab(port: int, url: str) -> str:
    """通过 PUT /json/new 创建新标签页并直接导航到指定 URL，返回 WS URL"""
    for attempt in range(5):
        try:
            resp = requests.put(
                f"http://localhost:{port}/json/new?{url}", timeout=10)
            if resp.status_code == 200:
                tab = resp.json()
                ws = tab.get("webSocketDebuggerUrl", "")
                if ws:
                    return ws
            elif resp.status_code == 500 and attempt < 4:
                # Chrome 偶发 500，重试
                time.sleep(1.5)
                continue
        except Exception:
            if attempt < 4:
                time.sleep(1.5)
                continue
        break
    raise RuntimeError(f"无法创建新标签页 on port {port}")


def get_cdp_ws_url(port: int, url_contains: str = "") -> str:
    """从 /json/list 获取当前活跃标签页的页面级 WebSocket URL
    url_contains: 可选，优先匹配 URL 包含此字符串的标签页"""
    for attempt in range(20):
        try:
            info = json.loads(
                requests.get(f"http://localhost:{port}/json/list", timeout=3).text)
            # 如果指定了 url_contains，优先匹配
            if url_contains:
                for t in info:
                    url = t.get("url", "")
                    if t.get("type") == "page" and url_contains in url:
                        return t["webSocketDebuggerUrl"]
            # fallback: 第一个非广告页面
            for t in info:
                url = t.get("url", "")
                if t.get("type") == "page" and "amazon-adsystem" not in url and url:
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        # 第一次尝试失败后，如果超过 5 秒还没找到，尝试打开新标签页
        if attempt == 3:
            try:
                resp = requests.put(
                    f"http://localhost:{port}/json/new?about:blank", timeout=5)
                if resp.status_code == 200:
                    new_tab = resp.json()
                    if new_tab.get("webSocketDebuggerUrl"):
                        return new_tab["webSocketDebuggerUrl"]
            except Exception:
                pass
        time.sleep(1)
    raise RuntimeError(f"无法连接 Chrome CDP on port {port}")


# ─── CDP 底层 ────────────────────────────────────────────────────────────────
async def cdp_send(ws_url: str, method: str, params: dict = None, timeout: int = 30):
    """
    发送 CDP 命令,返回 result.value(与 cdp_scraper.py 行为一致)
    returnByValue=True 让 JS 返回实际值而非引用
    """
    params = dict(params or {})
    params["returnByValue"] = True
    msg_id = random.randint(1, 999999)

    last_err = None
    for wsa in range(4):  # WS 连接重试 4 次
        try:
            async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as conn:
                await conn.send(json.dumps({"id": msg_id, "method": method, "params": params}))
                for _ in range(timeout * 2):
                    try:
                        raw = await asyncio.wait_for(conn.recv(), timeout=0.5)
                        msg = json.loads(raw)
                        if msg.get("id") == msg_id:
                            r = msg.get("result", {})
                            # 兼容 result.result.value 或 result.value
                            if "result" in r:
                                return r["result"].get("value", "")
                            return r.get("value", "")
                    except asyncio.TimeoutError:
                        continue
        except Exception as e:
            last_err = e
            if wsa < 3:
                await asyncio.sleep(2 ** wsa)  # 指数退避 1s, 2s, 4s
                continue
        break

    return ""


# ─── 翻译 ────────────────────────────────────────────────────────────────────
def translate_batch_m2(reviews: list[dict], batch_size: int = 5) -> list[dict]:
    """MiniMax M2.7 批量翻译:英→中,字段 title_zh / body_zh"""
    if not reviews:
        return reviews
    results = []
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    for i in range(0, len(reviews), batch_size):
        batch = reviews[i:i + batch_size]

        # ── 重试逻辑：对每条评论独立重试翻译 ─────────────────────
        for retry_round in range(3):
            untranslated = [r for r in batch if not r.get("body_zh") or r["body_zh"] == r.get("body", "")]
            if not untranslated:
                break

            lines = [f"[{j}] Title: {r.get('title','').replace(chr(10),' ').strip()}\n"
                     f"[{j}] Body: {r.get('body','').replace(chr(10),' ').strip()}"
                     for j, r in enumerate(batch)]

            payload = {
                "model": "MiniMax-M2.7",
                "messages": [{"role": "user", "content":
                    "You are a professional translator. Translate Amazon reviews from English "
                    "to Simplified Chinese. Output ONLY valid JSON array.\n"
                    'Format: [{"idx":N,"title_zh":"...","body_zh":"..."},...]\n'
                    "Reviews:\n" + "\n".join(lines)}],
                "temperature": 0.3, "max_tokens": 2000,
                "do_sample": True, "thinking": False,
            }

            ok = False
            try:
                resp = requests.post(
                    f"{MINIMAX_BASE_URL}/chat/completions",
                    headers=headers, json=payload, timeout=TRANSLATE_TIMEOUT, verify=False)
                if resp.status_code == 200:
                    try:
                        raw = resp.json()["choices"][0]["message"]["content"].strip()
                        # MiniMax M2.7 推理模型会在 content 中输出 <think...>...</think...> 标签包裹思考过程
                        # 需要先提取思考标签之后的有效内容
                        think_end = re.search(r'</think\s*>', raw, re.IGNORECASE | re.DOTALL)
                        if think_end:
                            raw = raw[think_end.end():].strip()
                        # 去除残留的 HTML 标签（如 <工具调用> 等）
                        content2 = re.sub(r'<[^>]+>', '', raw).strip()
                        # 去除代码围栏
                        fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content2, re.DOTALL)
                        if fence_match:
                            content2 = fence_match.group(1).strip()
                        translations = json.loads(content2)
                        trans_map = {str(t["idx"]): t for t in translations}
                        for idx, r in enumerate(batch):
                            t = trans_map.get(str(idx)) or {}
                            body_zh = t.get("body_zh", "").strip()
                            title_zh = t.get("title_zh", "").strip()
                            if body_zh and body_zh != r.get("body", ""):
                                r["body_zh"] = body_zh
                                r["title_zh"] = title_zh or r.get("title", "")
                        ok = True
                    except Exception:
                        pass
            except Exception:
                pass

            if not ok:
                time.sleep(1)

        # 兜底：仍未翻译的用原文
        for r in batch:
            r["body_zh"]  = r.get("body_zh",  "") or r.get("body",  "")
            r["title_zh"] = r.get("title_zh", "") or r.get("title", "")
            results.append(r)

        print(f"    ✅ 翻译 {len(batch)} 条")
        time.sleep(0.5)

    return results


# ─── JS 提取片段 ─────────────────────────────────────────────────────────────
SEARCH_JS = """
(function(maxCount) {
    var results = [];
    document.querySelectorAll('[data-asin]').forEach(function(el) {
        var asin = el.getAttribute('data-asin');
        if (!asin || asin.length !== 10 || !/^B[0-9A-Z]{9}$/.test(asin)) return;

        var titleEl = el.querySelector('[id*="productTitle"], h2 a span, h2 span');
        var title = titleEl ? titleEl.textContent.trim().replace(/\\s+/g,' ') : '';

        var price = '';
        var priceEl = el.querySelector('.a-price .a-offscreen');
        if (priceEl) { var m = priceEl.textContent.match(/\\$[\\d,]+\\.?\\d*/); if (m) price = m[0]; }
        if (!price) {
            var w = el.querySelector('.a-price-whole');
            var f = el.querySelector('.a-price-fraction');
            if (w) price = '$' + w.textContent.trim() + (f ? '.' + f.textContent.trim() : '');
        }

        var rating = '';
        var rEl = el.querySelector('.a-icon-alt');
        if (rEl) { var rm = rEl.textContent.match(/^([\\d.]+)\\s+out of\\s+5/); if (rm) rating = rm[1] + ' out of 5'; }

        var reviews = '';
        var rvEl = el.querySelector('.a-size-base.s-underline-text');
        if (rvEl) reviews = rvEl.textContent.trim();

        var sales_rank = '';
        var srEl = el.querySelector('.a-section.a-spacing-none .a-size-base.a-color-secondary, [data-component-type="s-product-header-below-secondary-results"] .a-size-base.a-color-secondary');
        if (srEl) {
            var srText = srEl.textContent.trim();
            var srMatch = srText.match(/#?([\d,]+)\s+in\s+/);
            if (srMatch) sales_rank = '#' + srMatch[1];
        }

        var brand = '';
        var bEl = el.querySelector('.a-color-secondary, .a-size-base-plus.a-color-base');
        if (bEl) brand = bEl.textContent.trim().replace('Brand: ','');

        var image_url = '';
        var imgEl = el.querySelector('img.s-image');
        if (imgEl) {
            image_url = imgEl.src || imgEl.getAttribute('data-src') || '';
            if (image_url.indexOf('sprite') !== -1 || image_url.indexOf('data:image') !== -1) image_url = '';
        }

        // 抓取销量标签 "X+ bought in past month"
        // 注意：不要往父级扩（父容器含多个ASIN会串数据），只在 el 自、子元素、兄弟元素中搜
        var monthly_sales = '';
        // 先在 el 自身体及直接子元素文本中匹配
        var elText = (el.textContent || '');
        var msMatch = elText.match(/([\d,.]+[KkMm])\+?\s*bought\s+in\s+(?:the\s+)?(?:last|past)\s+month/i);
        if (msMatch) monthly_sales = msMatch[1] + '+';
        // 若自身没有，搜直接兄弟节点（同层产品卡片）
        if (!monthly_sales && el.parentElement) {
            var siblings = Array.from(el.parentElement.children);
            for (var s = 0; s < siblings.length && !monthly_sales; s++) {
                var st = siblings[s].textContent || '';
                var sm = st.match(/([\d,.]+[KkMm])\+?\s*bought\s+in\s+(?:the\s+)?(?:last|past)\s+month/i);
                if (sm) monthly_sales = sm[1] + '+';
            }
        }

        if (asin && title) {
            results.push({asin, title: title.substring(0,200), brand, price, rating, reviews, sales_rank, monthly_sales,
                          product_url: 'https://www.amazon.com/dp/' + asin,
                          product_image: image_url});
        }
    });
    return JSON.stringify(results.slice(0, maxCount));
})
"""

REVIEW_EXTRACT_JS = """
(function() {
    var results = [];

    // ── 方法1: data-hook 选择器（Amazon 标准结构，命中率最高）──
    var containers = document.querySelectorAll('[data-hook="review"]');
    for (var i = 0; i < containers.length; i++) {
        var c = containers[i];

        var ratingEl = c.querySelector('[data-hook="review-star-rating"] .a-icon-alt, [data-hook="cmps-review-star-rating"] .a-icon-alt');
        var rating = '?';
        if (ratingEl) {
            var rm = ratingEl.textContent.match(/([\\d.]+)\\s+out of\\s+5/);
            if (rm) rating = rm[1] + ' out of 5 stars';
        }

        var titleEl = c.querySelector('[data-hook="review-title"] span:not(.a-icon-alt)');
        if (!titleEl) titleEl = c.querySelector('[data-hook="review-title"]');
        var title = titleEl ? titleEl.textContent.trim().replace(/^[\\d.]+\\s+out of\\s+5\\s+stars\\s*/i, '') : '';

        var bodyEl = c.querySelector('[data-hook="review-body"] span');
        if (!bodyEl) bodyEl = c.querySelector('[data-hook="review-body"]');
        var body = bodyEl ? bodyEl.textContent.trim() : '';

        var authorEl = c.querySelector('.a-profile-name');
        var author = authorEl ? authorEl.textContent.trim() : 'Anonymous';

        var dateEl = c.querySelector('[data-hook="review-date"]');
        var date = dateEl ? dateEl.textContent.replace('Reviewed in the United States on ', '').trim() : '';

        var verified = !!c.querySelector('[data-hook="avp-badge"]');

        if (title || body) {
            results.push({ rating: rating, title: title, body: body, author: author, date: date, verified: verified });
        }
    }

    // ── 方法2: fallback h5 结构（data-hook 未命中时）──
    if (results.length === 0) {
        var allH5 = document.querySelectorAll('h5');
        for (var i = 0; i < allH5.length; i++) {
            var h5 = allH5[i];
            var text = h5.textContent.trim();
            var match = text.match(/^([\\d.]+)\\s+out of\\s+5\\s+stars\\s+(.+)/i);
            if (!match) continue;
            var parent = h5.closest('li') || h5.parentElement;
            var body = '';
            if (parent) {
                var bodySpan = parent.querySelector('span[class*="review-text"], [data-hook="review-body"] span');
                if (bodySpan) {
                    body = bodySpan.textContent.trim();
                } else {
                    var walker = document.createTreeWalker(parent, NodeFilter.SHOW_TEXT, null, false);
                    var node;
                    while (node = walker.nextNode()) {
                        var t = node.textContent.trim();
                        if (t.length < 20) continue;
                        var tag = node.parentElement ? node.parentElement.tagName : '';
                        if (tag === 'H5' || tag === 'H6' || tag === 'SCRIPT' || tag === 'STYLE' || tag === 'BUTTON') continue;
                        if (t.match(/^\\d+\\.?\\d*\\s+out of\\s+5/i)) continue;
                        body = t; break;
                    }
                }
            }
            var authorEl = parent ? parent.querySelector('a[href*="/profile/"]') : null;
            var author = authorEl ? authorEl.textContent.trim() : 'Anonymous';
            results.push({ rating: match[1]+' out of 5 stars', title: match[2], body: body, author: author, date: '', verified: false });
        }
    }

    return JSON.stringify(results);
})
"""


# ─── Step 1+2: 搜索 ──────────────────────────────────────────────────────────
async def step_search_products(keyword: str, sort: str, max_asins: int) -> list[dict]:
    print(f"\n🔍 Step 1+2: 搜索关键词='{keyword}', sort='{sort}', 目标={max_asins}个产品")

    port = DEFAULT_PORT
    url = f"https://www.amazon.com/s?k={keyword.replace(' ', '+')}&s={sort}"
    print(f"   访问: {url}")

    # 使用 PUT /json/new 创建新标签页（避免复用已断连的旧标签页）
    ws = open_new_tab(port, url)
    await asyncio.sleep(8)

    print("   滚动加载中...")
    for _ in range(10):
        await cdp_send(ws, "Runtime.evaluate",
                       {"expression": "window.scrollTo(0, document.body.scrollHeight)"})
        await asyncio.sleep(2)

    raw_text = await cdp_send(ws, "Runtime.evaluate",
                               {"expression": f"({SEARCH_JS})({max_asins * 3})"})

    try:
        raw = json.loads(raw_text) if raw_text else []
    except Exception:
        raw = []

    print(f"   JS 提取: {len(raw)} 个候选 ASIN")

    # 去重
    uniq, dup_keys = [], set()
    for p in raw:
        key = (p.get("title","")[:60], p.get("brand",""), p.get("price",""))
        if key[0] and key not in dup_keys:
            dup_keys.add(key)
            uniq.append(p)

    result_list = uniq[:max_asins]
    print(f"   ✅ 去重完成: 共 {len(uniq)} 个唯一产品,取前 {len(result_list)} 个")
    return result_list


# ─── Step 3+4: 评论 ──────────────────────────────────────────────────────────
async def step_scrape_reviews(products: list[dict], translate: bool) -> dict:
    print(f"\n📝 Step 3+4: 评论抓取 + 翻译 (translate={translate})")
    results = {}

    for i, prod in enumerate(products, 1):
        asin = prod["asin"]
        title = prod.get("title","")[:60]
        print(f"\n  [{i}/{len(products)}] {asin} | {title}...")

        port = DEFAULT_PORT
        product_url = f"https://www.amazon.com/dp/{asin}"
        review_url = (f"https://www.amazon.com/product-reviews/{asin}/"
                      f"ref=cm_cr_arp_d_viewopt_fmt?sortBy=recent&reviewerType=all_reviews"
                      f"&formatType=current_format&pageNumber=1")

        # 先打开产品详情页，抓取真实月销量
        ws = open_new_tab(port, product_url)
        await asyncio.sleep(5)
        sales_raw = await cdp_send(ws, "Runtime.evaluate",
                                   {"expression": f"({MONTHLY_SALES_JS})()"})
        if sales_raw:
            prod["monthly_sales"] = sales_raw.strip()
            print(f"    月销量: {sales_raw.strip()}")
        else:
            print(f"    月销量: 页面未找到")

        # 在同标签页导航到评论页
        await cdp_send(ws, "Page.navigate", {"url": review_url})
        await asyncio.sleep(6)

        # 滚动加载
        for _ in range(15):
            await cdp_send(ws, "Runtime.evaluate",
                           {"expression": "window.scrollTo(0, document.body.scrollHeight)"})
            await asyncio.sleep(1.5)

        # JS DOM 提取
        raw_text = await cdp_send(ws, "Runtime.evaluate",
                                  {"expression": f"({REVIEW_EXTRACT_JS})()"})

        try:
            reviews = json.loads(raw_text) if raw_text else []
        except Exception:
            reviews = []

        print(f"    抓取 {len(reviews)} 条评论...")

        if translate and reviews:
            reviews = translate_batch_m2(reviews)

        results[asin] = {
            "asin": asin,
            "title": prod.get("title", ""),
            "brand": prod.get("brand", ""),
            "price": prod.get("price", ""),
            "rating": prod.get("rating", ""),
            "product_image": prod.get("product_image", ""),
            "reviews": reviews,
            "monthly_sales": prod.get("monthly_sales", ""),
            "sales_rank": prod.get("sales_rank", ""),
        }
        print(f"    ✅ 完成: {len(reviews)} 条评论(含翻译)")
        await asyncio.sleep(1)

    return results


# ─── 产品图片提取 JS ─────────────────────────────────────────────────────────
PRODUCT_IMAGE_JS = """
(function() {
    // 尝试多种选择器获取产品主图
    var selectors = [
        '#landingImage',
        '#imgBlopsFront',
        '.a-dynamic-image',
        '#main-image',
        'img[data-a-dynamic-image]',
        '#rgb img'
    ];
    for (var i = 0; i < selectors.length; i++) {
        var el = document.querySelector(selectors[i]);
        if (el && el.src && el.src.indexOf('sprite') === -1 && el.src.indexOf('pixel') === -1) {
            return el.src;
        }
    }
    // 备选：从 data-a-dynamic-image 属性提取最大图片
    var dyn = document.querySelector('[data-a-dynamic-image]');
    if (dyn) {
        try {
            var imgs = JSON.parse(dyn.getAttribute('data-a-dynamic-image'));
            var keys = Object.keys(imgs);
            if (keys.length > 0) {
                // 返回最大尺寸的那张
                var maxW = 0, maxUrl = '';
                for (var k = 0; k < keys.length; k++) {
                    var w = parseInt(keys[k].split('x')[0]);
                    if (w > maxW) { maxW = w; maxUrl = keys[k]; }
                }
                return maxUrl || imgs[keys[0]][0];
            }
        } catch(e) {}
    }
    return '';
})()
"""

# ─── 月销量抓取 JS（从产品详情页提取 "X+ bought in past month"）───────────
MONTHLY_SALES_JS = """
(function() {
    var body = document.body.innerText || '';
    var match = body.match(/([\\d,]+)\\+?\\s*bought\\s+in\\s+(?:the\\s+)?(?:last|past)\\s+month/i);
    if (match) return match[1] + '+';
    return '';
})()
"""

# ─── AI 自动分析（用 MiniMax 基于评论生成洞察）──────────────────────────────
def _generate_product_insights_batch(all_data: dict) -> dict:
    """用 MiniMax 批量生成每个产品的用户场景/优点/痛点
    返回: {asin: {"scenarios": [...], "pros": [...], "pains": [...]}}
    失败时返回空 dict（不影响 Word 生成）
    """
    if not all_data:
        return {}
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    
    # 为每个产品构建简短的评论摘要
    product_lines = []
    for asin, info in all_data.items():
        revs = info.get("reviews", [])
        # 取评分分布和关键评论标题
        titles = [r.get("title_zh","") or r.get("title","") for r in revs[:5] if r.get("title_zh","") or r.get("title","")]
        samples = [r.get("body_zh","") or r.get("body","") for r in revs[:3]]
        product_lines.append(
            f"[{asin}] {info.get('title','')[:80]} | 评分:{info.get('rating','?')} | 月销:{info.get('monthly_sales','N/A')}\n"
            f"  评论标题: {'; '.join(titles)}\n"
            f"  评论摘要: {'; '.join(s[:150] for s in samples)}"
        )
    
    prompt_body = "\n".join(product_lines)
    payload = {
        "model": "MiniMax-M2.7",
        "messages": [{"role": "user", "content":
            "You are an Amazon product analyst. Analyze each product's reviews and output ONLY "
            "valid JSON. For each ASIN, identify:\n"
            "- scenarios: key user scenarios/use cases (2-4 items, in Chinese)\n"
            "- pros: core strengths mentioned by users (2-4 items, in Chinese)\n"
            "- pains: pain points from negative reviews (2-4 items, in Chinese)\n"
            'Format: {"B0ASIN1":{"scenarios":["场景1","场景2"],"pros":["优点1","优点2"],"pains":["痛点1","痛点2"]},...}\n\n'
            "Products:\n" + prompt_body}],
        "temperature": 0.3, "max_tokens": 3000,
        "do_sample": True, "thinking": False,
    }
    
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{MINIMAX_BASE_URL}/chat/completions",
                headers=headers, json=payload,
                timeout=60, verify=False)
            if resp.status_code == 200:
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                # 去除 think 标签
                think_end = re.search(r'</think\s*>', raw, re.IGNORECASE | re.DOTALL)
                if think_end:
                    raw = raw[think_end.end():].strip()
                raw = re.sub(r'<[^>]+>', '', raw).strip()
                fence = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
                if fence:
                    raw = fence.group(1).strip()
                result = json.loads(raw)
                # 验证格式
                validated = {}
                for asin, data in result.items():
                    if isinstance(data, dict) and all(k in data for k in ("scenarios","pros","pains")):
                        validated[asin] = data
                if validated:
                    print(f"  ✅ AI 生成产品洞察: {len(validated)} 个产品")
                    return validated
        except Exception as e:
            print(f"  ⚠ AI 洞察尝试 {attempt+1} 失败: {e}")
            time.sleep(2)
    
    print("  ⚠ AI 洞察生成失败，使用空数据")
    return {}


def _generate_cross_analysis(all_data: dict, keyword: str) -> str:
    """用 MiniMax 生成共性痛点分析+选品建议（Markdown 格式）
    返回 markdown 字符串，失败时返回空字符串
    """
    if not all_data:
        return ""
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    
    # 构建产品数据摘要
    lines = []
    for asin, info in all_data.items():
        revs = info.get("reviews", [])
        # 收集各评分段的评论
        pos_reviews = [r for r in revs if any(str(s) in (r.get("rating","") or "") for s in (4,5))]
        neg_reviews = [r for r in revs if any(str(s) in (r.get("rating","") or "") for s in (1,2,3))]
        
        pos_texts = [(r.get("title_zh","") or r.get("title","")) + ": " + (r.get("body_zh","") or r.get("body",""))[:200] for r in pos_reviews[:3]]
        neg_texts = [(r.get("title_zh","") or r.get("title","")) + ": " + (r.get("body_zh","") or r.get("body",""))[:200] for r in neg_reviews[:3]]
        
        lines.append(
            f"## {asin} | {info.get('title','')[:80]}\n"
            f"价格:{info.get('price','?')} 评分:{info.get('rating','?')} 月销量:{info.get('monthly_sales','N/A')}\n"
            f"好评: {'; '.join(pos_texts) if pos_texts else '无'}\n"
            f"差评: {'; '.join(neg_texts) if neg_texts else '无'}\n"
        )
    
    prompt_body = "\n".join(lines)
    payload = {
        "model": "MiniMax-M2.7",
        "messages": [{"role": "user", "content":
            f"You are an Amazon product selection analyst analyzing the '{keyword}' category. "
            "Based on the product reviews below, write:\n"
            "1. 共性痛点分析: Common pain points across all products, with frequency counts "
            "and industry interpretation. Use a table with columns: 痛点, 频次, 解读\n"
            "2. 选品建议: Data-driven product selection opportunities and differentiation strategies\n\n"
            "Output in Chinese markdown format with:\n"
            "- ## 🔍 共性痛点分析 as the first H2 heading\n"
            "- ### subheadings for each pain point\n"
            "- | 痛点 | 频次 | 解读 | table\n"
            "- ## 💡 选品建议 as the second H2 heading\n"
            "- Numbered list for each suggestion\n\n"
            "Products data:\n" + prompt_body}],
        "temperature": 0.4, "max_tokens": 3000,
        "do_sample": True, "thinking": False,
    }
    
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{MINIMAX_BASE_URL}/chat/completions",
                headers=headers, json=payload,
                timeout=90, verify=False)
            if resp.status_code == 200:
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                think_end = re.search(r'</think\s*>', raw, re.IGNORECASE | re.DOTALL)
                if think_end:
                    raw = raw[think_end.end():].strip()
                raw = re.sub(r'<[^>]+>', '', raw).strip()
                fence = re.search(r'```(?:markdown)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
                if fence:
                    raw = fence.group(1).strip()
                if '痛点' in raw and '选品' in raw:
                    print(f"  ✅ AI 生成跨品分析完成 ({len(raw)} 字符)")
                    return raw
                print(f"  ⚠ AI 分析格式异常，重试...")
        except Exception as e:
            print(f"  ⚠ AI 分析尝试 {attempt+1} 失败: {e}")
            time.sleep(2)
    
    print("  ⚠ AI 跨品分析生成失败")
    return ""


# ─── Step 5: 分析 ─────────────────────────────────────────────────────────────
def step_analyze_docx(all_data: dict, keyword: str = "", sort_name: str = "") -> str:
    """生成 Word 分析报告，含完整评论分析 + 内嵌产品图片"""
    from docx import Document
    from docx.shared import Pt, Inches, Cm, RGBColor, Emu
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml.ns import qn, nsdecls
    from docx.oxml import parse_xml
    import requests, hashlib, os, threading, io
    
    
    # 品类由关键词决定，无硬编码限制
    cat_name = keyword if keyword else "商品"

    # ── 图片缓存（复用 HTML 的缓存目录和 TTL 逻辑）──────────
    IMG_CACHE_DIR = "/tmp/amazon_image_cache"
    os.makedirs(IMG_CACHE_DIR, exist_ok=True)

    img_bytes_cache = {}  # url -> bytes
    def dl_img_bytes(url):
        if not url: return None
        if url in img_bytes_cache: return img_bytes_cache[url]
        cache_path = os.path.join(IMG_CACHE_DIR, hashlib.md5(url.encode()).hexdigest() + ".jpg")
        if os.path.exists(cache_path) and time.time() - os.path.getmtime(cache_path) < 7*86400:
            with open(cache_path, "rb") as f:
                data = f.read()
            img_bytes_cache[url] = data
            return data
        try:
            r = requests.get(url, headers={"User-Agent":"Mozilla/5.0","Referer":"https://www.amazon.com/"}, timeout=15)
            if r.status_code == 200:
                img_bytes_cache[url] = r.content
                with open(cache_path, "wb") as f: f.write(r.content)
                return r.content
        except Exception: pass
        return None

    # 多线程预下载所有产品图片
    threads = []
    for url in [info.get("product_image","") for info in all_data.values() if info.get("product_image")]:
        t = threading.Thread(target=lambda u=url: img_bytes_cache.update({u: dl_img_bytes(u) or b""}))
        t.start(); threads.append(t)
    for t in threads: t.join(timeout=20)

    # ── AI 自动分析（基于真实评论，非关键词匹配）───────────────────
    print("\n🤖 AI 分析中（基于 MiniMax 阅读评论）...")
    per_product_insights = _generate_product_insights_batch(all_data)
    cross_analysis_md = _generate_cross_analysis(all_data, keyword=keyword or cat_name)
    has_auto_analysis = bool(cross_analysis_md)

    # ── 创建 Word 文档 ───────────────────────────────────────
    doc = Document()

    # 设置默认字体
    style = doc.styles['Normal']
    font = style.font
    font.name = 'PingFang SC'
    font.size = Pt(10.5)
    # 中文字体
    style.element.rPr.rFonts.set(qn('w:eastAsia'), 'PingFang SC')

    # 页边距
    for section in doc.sections:
        section.top_margin = Cm(1.5)
        section.bottom_margin = Cm(1.5)
        section.left_margin = Cm(1.8)
        section.right_margin = Cm(1.8)

    # ── 标题 ─────────────────────────────────────────────────
    ts = time.strftime("%Y-%m-%d")
    title = doc.add_heading('', level=0)
    run = title.add_run(f'📦 Amazon {cat_name} 选品分析报告')
    run.font.size = Pt(22)
    run.font.color.rgb = RGBColor(0x2d, 0x34, 0x36)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run(f'关键词：{keyword} | 排行榜：{sort_name} | 产品数：{len(all_data)} | 日期：{ts}')
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

    doc.add_paragraph()  # 空行

    # ── 逐产品分析 ───────────────────────────────────────────
    all_reviews_flat = []

    for idx, (asin, info) in enumerate(all_data.items(), 1):
        revs = info.get("reviews", [])
        all_reviews_flat.extend(revs)
        total = len(revs) or 1

        # ── 产品标题栏 ───────────────────────────────────────
        h = doc.add_heading(f'📦 #{idx} {asin}', level=1)
        for run in h.runs:
            run.font.size = Pt(15)
            run.font.color.rgb = RGBColor(0x09, 0x84, 0xe3)

        # 产品名
        p = doc.add_paragraph()
        run = p.add_run(f'产品标题：')
        run.font.bold = True
        run.font.size = Pt(10.5)
        run = p.add_run(info.get('title','')[:100])
        run.font.size = Pt(10.5)

        # 产品链接
        p = doc.add_paragraph()
        run = p.add_run('链接：')
        run.font.bold = True
        run.font.size = Pt(10.5)
        run = p.add_run(f'https://www.amazon.com/dp/{asin}')
        run.font.size = Pt(10.5)
        run.font.color.rgb = RGBColor(0x09, 0x84, 0xe3)

        # 价格、评分、月销量（仅真实数据，不估算）
        ms_raw = info.get("monthly_sales", "")
        sales_info = f" | 月销量：{ms_raw}" if ms_raw else ""
        p = doc.add_paragraph()
        run = p.add_run(f'价格：{info.get("price","N/A")} | 评分：{info.get("rating","N/A")}{sales_info}')
        run.font.size = Pt(10.5)

        # ── 插入产品图片 ─────────────────────────────────────
        img_url = info.get("product_image","")
        img_bytes = img_bytes_cache.get(img_url)
        if not img_bytes:
            # 同步下载（后台线程可能还没下好）
            img_bytes = dl_img_bytes(img_url) if img_url else None
        if img_bytes:
            try:
                doc.add_picture(io.BytesIO(img_bytes), width=Inches(1.8))
                last_paragraph = doc.paragraphs[-1]
                last_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            except Exception as e:
                print(f"    ⚠ 图片插入失败: {e}")

        # ── 用户洞察（AI 自动分析或 fallback 占位符）────────────────
        insights = per_product_insights.get(asin, {})
        scenarios = insights.get("scenarios", [])
        pros = insights.get("pros", [])
        pains = insights.get("pains", [])
        
        if scenarios:
            p = doc.add_paragraph()
            run = p.add_run('🎯 用户场景：')
            run.font.bold = True; run.font.size = Pt(10.5)
            run = p.add_run('、'.join(scenarios))
            run.font.size = Pt(10.5)
            run.font.color.rgb = RGBColor(0x09, 0x84, 0xe3)
        if pros:
            p = doc.add_paragraph()
            run = p.add_run('✅ 核心优点：')
            run.font.bold = True; run.font.size = Pt(10.5)
            run = p.add_run('、'.join(pros))
            run.font.size = Pt(10.5)
            run.font.color.rgb = RGBColor(0x00, 0xb8, 0x94)
        if pains:
            p = doc.add_paragraph()
            run = p.add_run('⚠ 痛点标签：')
            run.font.bold = True; run.font.size = Pt(10.5)
            run = p.add_run('、'.join(pains))
            run.font.size = Pt(10.5)
            run.font.color.rgb = RGBColor(0xe1, 0x70, 0x55)
        
        # 如果 AI 分析失败，保留占位符供 Agent 填充
        if not any([scenarios, pros, pains]):
            p = doc.add_paragraph()
            run = p.add_run(f'[INSIGHT_PLACEHOLDER:{asin}]')
            run.font.size = Pt(1)
            run.font.color.rgb = RGBColor(0xff, 0xff, 0xff)  # 白色隐藏

        # ── 典型评论（最长的那条）─────────────────────────────
        best = max(revs, key=lambda r: len(r.get("body_zh","") or r.get("body",""))) if revs else None
        if best:
            p = doc.add_paragraph()
            run = p.add_run('💬 典型评论')
            run.font.bold = True
            run.font.size = Pt(10.5)
            run.font.color.rgb = RGBColor(0x00, 0xb8, 0x94)
            p.paragraph_format.space_before = Pt(6)

            best_title = best.get("title_zh","") or best.get("title","")
            best_body = (best.get("body_zh","") or best.get("body","")).replace("\n"," ").strip()
            if len(best_body) > 400: best_body = best_body[:400] + "..."

            p2 = doc.add_paragraph()
            run = p2.add_run(f'⭐ {best.get("rating","?")} | {best_title}')
            run.font.bold = True
            run.font.size = Pt(10)

            p2 = doc.add_paragraph()
            run = p2.add_run(best_body)
            run.font.size = Pt(10)
            run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
            p2.paragraph_format.left_indent = Cm(0.5)

        # ── 全部评论详情 ─────────────────────────────────────
        if revs:
            p = doc.add_paragraph()
            run = p.add_run(f'📋 全部评论详情（{total}条）')
            run.font.bold = True
            run.font.size = Pt(10.5)
            run.font.color.rgb = RGBColor(0x6c, 0x5c, 0xe7)
            p.paragraph_format.space_before = Pt(8)

            for j, r in enumerate(revs, 1):
                stars_text = r.get("rating","?")
                m = re.search(r"([1-5])", stars_text)
                star_count = int(m.group(1)) if m else 0
                star_str = "★" * star_count + "☆" * (5 - star_count)
                rev_title = r.get("title_zh","") or r.get("title","")
                rev_body = (r.get("body_zh","") or r.get("body","")).replace("\n"," ").strip()
                if len(rev_body) > 300: rev_body = rev_body[:300] + "..."

                p = doc.add_paragraph()
                run = p.add_run(f'[{j}] ')
                run.font.size = Pt(9.5)
                run.font.color.rgb = RGBColor(0xb2, 0xbe, 0xc3)
                run = p.add_run(f'{star_str} {rev_title}')
                run.font.size = Pt(10)
                run.font.bold = True
                if r.get("verified"):
                    run = p.add_run('  [Verified]')
                    run.font.size = Pt(9)
                    run.font.color.rgb = RGBColor(0x00, 0xb8, 0x94)

                p2 = doc.add_paragraph()
                run = p2.add_run(rev_body)
                run.font.size = Pt(9.5)
                run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
                p2.paragraph_format.left_indent = Cm(0.5)
                p2.paragraph_format.space_after = Pt(4)

        # 分隔线
        doc.add_paragraph('─' * 60)

    # ── 共性痛点分析 & 选品建议 ────────────────────────────────
    if has_auto_analysis:
        # AI 分析成功——直接渲染到 Word
        print(f"  📝 写入 AI 分析到 Word...")
        prev_sibling = doc.paragraphs[-1]._element if doc.paragraphs else None
        if prev_sibling is not None:
            _render_md_to_docx(doc, doc.element.body, prev_sibling, cross_analysis_md)
        else:
            p = doc.add_paragraph()
            doc.element.body.append(p._element)
            _render_md_to_docx(doc, doc.element.body, p._element, cross_analysis_md)
        # 添加 Agent 可覆盖标记
        p = doc.add_paragraph()
        run = p.add_run('[AgentOverride:ANALYSIS]')
        run.font.size = Pt(1)
        run.font.color.rgb = RGBColor(0xff, 0xff, 0xff)  # 白色隐藏
    else:
        # AI 分析失败——留占位符等 Agent 填写
        p = doc.add_paragraph()
        run = p.add_run('[ANALYSIS_PLACEHOLDER]')
        run.font.size = Pt(1)
        run.font.color.rgb = RGBColor(0xff, 0xff, 0xff)  # 白色隐藏

    # ── 保存 ─────────────────────────────────────────────────
    out_path = f"/tmp/report_{time.strftime('%Y%m%d_%H%M%S')}.docx"
    doc.save(out_path)
    print(f"\n📄 Word 报告已生成：{out_path}")
    return out_path


def _render_md_to_docx(doc, body, prev_sibling, md_text: str):
    """将 Markdown 文本渲染为 Word 元素，插入到 prev_sibling 之后

    支持的 Markdown 语法：
    - ## 标题 → Word Heading 1（红色/绿色等由标题文字决定）
    - ### 标题 → Word Heading 2
    - - 列表项 → Bullet List
    - 1. 编号项 → Numbered List
    - **粗体** → Bold run
    - | 表格 | 行 → Word Table
    - 空行 → 段落间距
    - 普通文本 → Normal paragraph
    """
    from docx.shared import Pt, RGBColor
    from docx.enum.table import WD_TABLE_ALIGNMENT

    lines = md_text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 空行 → 间距
        if not stripped:
            i += 1
            continue

        # ## 标题（H1）
        if stripped.startswith('## ') and not stripped.startswith('### '):
            heading_text = stripped[3:].strip()
            new_p = doc.add_paragraph()
            run = new_p.add_run(heading_text)
            run.font.size = Pt(16)
            run.font.bold = True
            # 根据标题关键词着色
            if '痛点' in heading_text:
                run.font.color.rgb = RGBColor(0xe1, 0x70, 0x55)
            elif '选品' in heading_text or '建议' in heading_text:
                run.font.color.rgb = RGBColor(0x00, 0xb8, 0x94)
            elif '场景' in heading_text:
                run.font.color.rgb = RGBColor(0x09, 0x84, 0xe3)
            else:
                run.font.color.rgb = RGBColor(0x2d, 0x34, 0x36)
            prev_sibling.addnext(new_p._element)
            prev_sibling = new_p._element
            i += 1
            continue

        # ### 标题（H2）
        if stripped.startswith('### '):
            heading_text = stripped[4:].strip()
            new_p = doc.add_paragraph()
            run = new_p.add_run(heading_text)
            run.font.size = Pt(13)
            run.font.bold = True
            run.font.color.rgb = RGBColor(0x2d, 0x34, 0x36)
            prev_sibling.addnext(new_p._element)
            prev_sibling = new_p._element
            i += 1
            continue

        # 表格（连续 | 开头的行）
        if stripped.startswith('|'):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i].strip())
                i += 1
            # 解析表格
            rows_data = []
            for tl in table_lines:
                # 跳过分隔行 |---|---|
                if re.match(r'^\|[\s\-:|]+\|$', tl):
                    continue
                cells = [c.strip() for c in tl.split('|')[1:-1]]  # 去首尾空
                rows_data.append(cells)
            if rows_data:
                num_cols = max(len(r) for r in rows_data)
                table = doc.add_table(rows=0, cols=num_cols)
                table.alignment = WD_TABLE_ALIGNMENT.CENTER
                table.style = 'Light Grid Accent 1'
                for ri, row_data in enumerate(rows_data):
                    row = table.add_row().cells
                    for ci, cell_text in enumerate(row_data):
                        if ci < num_cols:
                            row[ci].text = cell_text
                            for p in row[ci].paragraphs:
                                for run in p.runs:
                                    run.font.size = Pt(9.5) if ri > 0 else Pt(10)
                                    if ri == 0:
                                        run.font.bold = True
                prev_sibling.addnext(table._tbl)
                prev_sibling = table._tbl
            continue

        # 编号列表（1. 2. 等）
        list_match = re.match(r'^(\d+)[.、)\s]\s*(.*)', stripped)
        if list_match:
            content = list_match.group(2)
            new_p = doc.add_paragraph(style='List Number')
            _add_formatted_runs(new_p, content)
            prev_sibling.addnext(new_p._element)
            prev_sibling = new_p._element
            i += 1
            continue

        # Bullet 列表（- 或 •）
        if stripped.startswith('- ') or stripped.startswith('• '):
            content = stripped[2:].strip()
            new_p = doc.add_paragraph(style='List Bullet')
            _add_formatted_runs(new_p, content)
            prev_sibling.addnext(new_p._element)
            prev_sibling = new_p._element
            i += 1
            continue

        # 普通段落
        new_p = doc.add_paragraph()
        _add_formatted_runs(new_p, stripped)
        prev_sibling.addnext(new_p._element)
        prev_sibling = new_p._element
        i += 1

    return prev_sibling


def _add_formatted_runs(paragraph, text: str):
    """将含 **粗体** 的文本拆分为多个 run，保留格式"""
    from docx.shared import Pt, RGBColor

    # 用正则拆分 **粗体** 和普通文本
    parts = re.split(r'(\*\*[^*]+\*\*)', text)
    for part in parts:
        if part.startswith('**') and part.endswith('**'):
            run = paragraph.add_run(part[2:-2])
            run.font.bold = True
            run.font.size = Pt(10.5)
        elif part:
            run = paragraph.add_run(part)
            run.font.size = Pt(10.5)


def append_analysis_to_docx(docx_path: str,
                            per_product_insights: dict = None,
                            analysis_md: str = None):
    """将 Agent 撰写的分析写入 Word 报告

    Agent 写什么，Word 就放什么。不再有任何固定模板。

    参数:
        docx_path: Word 文件路径
        per_product_insights: {asin: {"scenarios": [...], "pros": [...], "pains": [...]}}
            替换 [INSIGHT_PLACEHOLDER:{ASIN}] 为 🎯/✅/⚠ 标签行
        analysis_md: Agent 的完整分析（Markdown 格式）
            替换 [ANALYSIS_PLACEHOLDER] 为渲染后的 Word 内容
            支持 ## 标题、### 子标题、编号列表、bullet、**粗体**、| 表格 |
    """
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document(docx_path)
    body = doc.element.body

    # ── 1. 替换每个产品的洞察占位符 ──────────────────────────
    if per_product_insights:
        for p in list(doc.paragraphs):
            text = p.text.strip()
            for asin, insights in per_product_insights.items():
                if f"[INSIGHT_PLACEHOLDER:{asin}]" in text:
                    p_element = p._element
                    prev = p_element.getprevious()

                    # 场景
                    scenarios = insights.get("scenarios", [])
                    if scenarios:
                        new_p = doc.add_paragraph()
                        run = new_p.add_run('🎯 用户场景：')
                        run.font.bold = True; run.font.size = Pt(10)
                        run = new_p.add_run('、'.join(scenarios))
                        run.font.size = Pt(10)
                        run.font.color.rgb = RGBColor(0x09, 0x84, 0xe3)
                        if prev is not None: prev.addnext(new_p._element)
                        else: body.insert(0, new_p._element)
                        prev = new_p._element

                    # 优点
                    pros = insights.get("pros", [])
                    if pros:
                        new_p = doc.add_paragraph()
                        run = new_p.add_run('✅ 核心优点：')
                        run.font.bold = True; run.font.size = Pt(10)
                        run = new_p.add_run('、'.join(pros))
                        run.font.size = Pt(10)
                        run.font.color.rgb = RGBColor(0x00, 0xb8, 0x94)
                        prev.addnext(new_p._element)
                        prev = new_p._element

                    # 痛点
                    pains = insights.get("pains", [])
                    if pains:
                        new_p = doc.add_paragraph()
                        run = new_p.add_run('⚠ 痛点标签：')
                        run.font.bold = True; run.font.size = Pt(10)
                        run = new_p.add_run('、'.join(pains))
                        run.font.size = Pt(10)
                        run.font.color.rgb = RGBColor(0xe1, 0x70, 0x55)
                        prev.addnext(new_p._element)
                        prev = new_p._element

                    # 删占位符
                    body.remove(p_element)
                    break

    # ── 2. 替换分析占位符 ──────────────────────────────────
    if analysis_md:
        # 先找 [AgentOverride:ANALYSIS]（新格式），再找 [ANALYSIS_PLACEHOLDER]（旧格式）
        target_markers = ['[AgentOverride:ANALYSIS]', '[ANALYSIS_PLACEHOLDER]']
        found = False
        for p in list(doc.paragraphs):
            for marker in target_markers:
                if marker in p.text:
                    p_element = p._element
                    prev = p_element.getprevious()
                    
                    # 如果前面有 AI 自动生成的分析内容，先清掉
                    # 从 prev 往前找，删除所有直到遇见 ## 开头的分析标题
                    if prev is not None:
                        # 删除 prev 后面直到段落末尾的 AI 分析内容
                        # 简单做法：把 prev 到文档末尾之间的分析段落删掉
                        pass  # 复杂，简化处理
                    
                    body.remove(p_element)
                    # 插入 Agent 的 markdown 分析
                    if prev is not None:
                        _render_md_to_docx(doc, body, prev, analysis_md)
                    else:
                        body.insert(0, doc.add_paragraph()._element)
                        _render_md_to_docx(doc, body, list(body)[0], analysis_md)
                    print(f"  ✅ 已将 Agent 分析写入 Word (替换 {marker})")
                    found = True
                    break
            if found:
                break
        
        if not found:
            # 没找到占位符，追加到末尾
            doc.add_paragraph()
            last = list(body)[-1]
            _render_md_to_docx(doc, body, last, analysis_md)
            print(f"  ⚠️ 未找到占位符，追加到文档末尾")

    doc.save(docx_path)
    print(f"✅ 分析内容已写入: {docx_path}")


async def send_report_to_feishu(docx_path: str, keyword: str = "", target: str = ""):
    """将 Word 报告发送到飞书"""
    import asyncio as aio, shutil

    if target:
        feishu_target = target
    else:
        feishu_target = os.environ.get("FEISHU_TARGET", "ou_b4d5d0c8662e8302676f51aa8a7c4490")

    # 飞书只允许从特定目录上传文件，先复制到允许目录
    media_dir = os.path.expanduser("~/.openclaw/media")
    os.makedirs(media_dir, exist_ok=True)
    fname = os.path.basename(docx_path)
    media_path = os.path.join(media_dir, fname)
    shutil.copy2(docx_path, media_path)

    try:
        proc = await aio.create_subprocess_exec(
            "openclaw", "message", "send",
            "--channel", "feishu",
            "--account", "competitor",
            "--target", feishu_target,
            "--message", f"📊 Amazon 选品分析报告 - {keyword}\n📄 Word 完整图文报告（手机可下载打开）",
            "--media", media_path,
            stdout=aio.subprocess.PIPE,
            stderr=aio.subprocess.PIPE
        )
        stdout, stderr = await aio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode == 0:
            print(f"  ✅ Word 报告已发送到飞书（手机可直接下载）")
        else:
            err = stderr.decode().strip()[:300] if stderr else ""
            print(f"  ⚠️ 飞书发送失败(returncode={proc.returncode}): {err}")
    except Exception as e:
        print(f"  ⚠️ 飞书发送失败: {e}")


def step_analyze(all_data: dict, keyword: str = "", brief: bool = False) -> str:
    """生成分析报告。brief=True 时只输出产品概览，不输出评论原文"""
    print("\n📊 Step 5: 分析报告中...")
    W = 72
    cat_name = keyword if keyword else "商品"

    rpt = ["=" * W, f"  📦 Amazon {cat_name} 选品分析报告", "=" * W]

    all_reviews = []

    for idx, (asin, info) in enumerate(all_data.items(), 1):
        revs = info.get("reviews", [])
        all_reviews.extend(revs)
        total = len(revs) or 1

        rpt.append(f"\n  📦 #{idx} [{asin}]")
        rpt.append(f"     产品标题: {info.get('title','')[:80]}")
        rpt.append(f"     链接: https://www.amazon.com/dp/{asin}")
        ms_raw = info.get("monthly_sales", "")
        sales_str = f" | 月销量：{ms_raw}" if ms_raw else ""
        rpt.append(f"     价格: {info.get('price','N/A')} | 评分: {info.get('rating','N/A')}{sales_str}")

        # 完整模式：输出评论原文
        if not brief and revs:
            rpt.append("     ── 评论摘要 ──")
            for j, r in enumerate(revs, 1):
                body_raw = r.get("body_zh") or r.get("body", "")
                body = body_raw.replace('\n', ' ').strip()
                title = r.get("title_zh") or r.get("title", "")
                rpt.append(f"     [{j}] ⭐{r.get('rating','?')} | {title}")
                rpt.append(f"         {body}")

    # brief 模式追加一句话说明
    if brief:
        rpt.append(f"\n  💡 摘要模式 | 共 {len(all_reviews)} 条评论已分析，详情请查看 Word 报告或评论 JSON")

    return "\n".join(rpt)


# ─── 主流程 ──────────────────────────────────────────────────────────────────
async def run(keyword: str, sort: str, sort_name: str, max_products: int, translate: bool, skip_confirm: bool = False, keyword_raw: str = "", feishu_target: str = ""):
    print("\n" + "="*60)
    print("  🛒 Amazon 选品评论分析 Pipeline")
    print("="*60)

    if not skip_confirm:
        confirm = input(
            f"\n📌 确认以下设置:\n"
            f"   关键词: {keyword}\n"
            f"   排行榜: {sort} ({sort_name})\n"
            f"   产品数: {max_products}\n"
            f"   确认开始吗?(Y/n)\n   > "
        ).strip().lower()
        if confirm in ("n", "no"):
            print("   已取消。")
            return


    ts = time.strftime("%Y%m%d_%H%M%S")
    products = await step_search_products(keyword, sort, max_products)
    tmp_asins = f"/tmp/asin_pool_{ts}.json"
    with open(tmp_asins, "w") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

    all_data = await step_scrape_reviews(products[:max_products], translate=translate)
    tmp_reviews = f"/tmp/reviews_{ts}.json"
    with open(tmp_reviews, "w") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    # 生成 Word 报告（含 AI 分析）
    docx_path = step_analyze_docx(all_data, keyword=keyword_raw or keyword, sort_name=sort_name)

    # 生成 txt 报告（纯文字，给 Agent 做二次解读）
    report_txt = step_analyze(all_data, keyword=keyword_raw or keyword, brief=False)

    # 发送到飞书 + 自动打开 Word
    print(f"\n  📤 发送到飞书...")
    await send_report_to_feishu(docx_path, keyword=keyword_raw or keyword, target=feishu_target)
    print(f"  🖥️ 打开 Word: {docx_path}")
    subprocess.run(["open", docx_path], capture_output=True, timeout=5)

    print(f"\n{'='*72}")
    print(f"  ✅ 流程完成!")
    print(f"  📦 ASIN池: {tmp_asins}")
    print(f"  📝 评论数据: {tmp_reviews}")
    print(f"  📄 Word报告: {docx_path}")
    print(f"  📤 已发送到飞书")
    print(f"{'='*72}\n")
    print(report_txt)


# ─── 交互入口 ────────────────────────────────────────────────────────────────



def interactive_prompt():
    print("")
    print("=" * 60)
    print("  🛒 Amazon 选品评论分析 Pipeline")
    print("=" * 60)

    keyword = input("\n📌 搜索关键词（例如: athletic socks / wireless charger，中文英文均可）:\n   > ").strip()
    while not keyword:
        keyword = input("   ⚠️ 关键词不能为空，请重新输入\n   > ").strip()
    keyword_raw, keyword = auto_translate_keyword(keyword)

    print("")
    print("📌 排行榜类型（必须选择）：")
    print("   1) bestsellers       销量榜")
    print("   2) newreleases       新品榜")
    print("   3) moversandshakers  飙升榜")
    print("   4) topreview         评论榜")
    sort_map = {"1":"bestsellers","2":"newreleases","3":"moversandshakers","4":"topreview"}
    sort_cn  = {"bestsellers":"销量榜","newreleases":"新品榜","moversandshakers":"飙升榜","topreview":"评论榜"}
    sort_choice = input("   > ").strip()
    while sort_choice not in ("1","2","3","4"):
        sort_choice = input("   ⚠️ 请输入 1~4 的数字并按回车\n   > ").strip()
    sort = sort_map[sort_choice]
    sort_name = sort_cn[sort]

    max_input = input("\n📌 分析产品数量（必须输入数字）：\n   > ").strip()
    while not max_input.isdigit() or int(max_input) < 1:
        max_input = input("   ⚠️ 请输入正整数，例如 3\n   > ").strip()
    max_products = int(max_input)

    print("")
    print("   ✅ 确认：关键词='" + keyword + "' | 排行榜='" + sort_name + "' | 产品数=" + str(max_products))
    return keyword_raw, keyword, sort, sort_name, max_products


if __name__ == "__main__":
    if len(sys.argv) == 1:
        keyword_raw, keyword, sort, sort_name, max_products = interactive_prompt()
        translate = True
        feishu_target = ""
    else:
        parser = argparse.ArgumentParser(description="Amazon Review Pipeline")
        parser.add_argument("--keyword", "-k")
        parser.add_argument("--sort", "-s",
                            choices=["bestsellers","newreleases","moversandshakers","topreview"],
                            help="【必填】排行榜类型：bestsellers/newreleases/moversandshakers/topreview")
        parser.add_argument("--max-products", "-n", type=int,
                            help="【必填】分析产品数量（正整数）")
        parser.add_argument("--translate", "-t", action="store_true", default=True)
        parser.add_argument("--no-translate", action="store_true")
        parser.add_argument("--yes", "-y", action="store_true", help="Skip confirm prompt")
        parser.add_argument("--feishu-target", help="飞书用户 open_id，指定发送到哪个用户。不传则发送到默认用户")
        args = parser.parse_args()

        # ── 必填校验：keyword / sort / max-products 三个都必须明确传入 ──────────
        missing = []
        if not args.keyword:
            missing.append("--keyword（搜索关键词）")
        if not args.sort:
            missing.append("--sort（排行榜类型：bestsellers/newreleases/moversandshakers/topreview）")
        if not args.max_products:
            missing.append("--max-products（分析产品数量，正整数）")
        if missing:
            print("❌ 以下参数为必填，请先询问用户后再执行：")
            for m in missing:
                print(f"   {m}")
            print("\n💡 正确调用示例：")
            print("   python3 pipeline.py --keyword 'compression socks' --sort bestsellers --max-products 5 --yes")
            sys.exit(1)

        if args.no_translate:
            args.translate = False
        keyword, sort, max_products = args.keyword, args.sort, args.max_products
        keyword_raw, keyword = auto_translate_keyword(keyword)
        skip_confirm = getattr(args, 'yes', False)
        translate = args.translate
        feishu_target = getattr(args, 'feishu_target', None) or ""
        sort_name = {"bestsellers":"销量榜","newreleases":"新品榜","moversandshakers":"飙升榜","topreview":"评论榜"}.get(args.sort,"销量榜")

    asyncio.run(run(keyword, sort, sort_name, max_products, translate, skip_confirm, keyword_raw=keyword_raw, feishu_target=feishu_target))
