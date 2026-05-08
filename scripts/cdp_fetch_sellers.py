#!/usr/bin/env python3
"""
Amazon Seller 批量抓取（CDP Chrome 登录态）
从 ASIN 列表批量抓 Seller Name / Seller Store URL

流程：
  读取 reviews JSON → 提取 ASIN 列表 → 逐个导航 offer-listing 页 → 提取卖家信息

输出：{asin: {brand, sold_by, seller_id, seller_store_url}}

用法：
  python3 cdp_fetch_sellers.py --reviews /tmp/reviews_combined_*.json
  python3 cdp_fetch_sellers.py --asins B0C96NNJ86 B075Y6Y9H5
  python3 cdp_fetch_sellers.py --reviews /tmp/reviews_*.json --port 18800
"""

import json, time, re, os, argparse, subprocess, websocket, sys, glob
from datetime import datetime

DEFAULT_PORT = 9222
RATE_LIMIT = 1.5  # 秒（两次 ASIN 之间）
OUTPUT_FILE = "/tmp/seller_results_{ts}.json"

# ── Chrome CDP 连接 ─────────────────────────────────────────

def get_page_ws(port):
    info = json.loads(subprocess.check_output(
        ['curl', '-s', f'http://localhost:{port}/json/list']))
    for t in info:
        url = t.get('url', '')
        if t.get('type') == 'page' and 'amazon-adsystem' not in url and url:
            return websocket.create_connection(
                t['webSocketDebuggerUrl'], timeout=120)
    raise Exception(f"[错误] 无法连接 Chrome 端口 {port}")

def cdp_eval(ws, expr, timeout=30):
    cid = abs(hash(expr)) % 9999
    ws.send(json.dumps({"id": cid, "method": "Runtime.evaluate",
                         "params": {"expression": expr, "returnByValue": True}}))
    for _ in range(timeout):
        try:
            r = json.loads(ws.recv())
            if r.get("id") == cid:
                return r.get("result", {}).get("result", {}).get("value", "")
        except Exception:
            pass
        time.sleep(0.5)
    return ""

def navigate_wait(ws, url, wait_sel, timeout=20):
    """导航到 URL，等指定元素出现"""
    ws.send(json.dumps({"id": 99, "method": "Page.navigate",
                         "params": {"url": url}}))
    time.sleep(timeout * 0.3)
    for _ in range(timeout):
        ok = cdp_eval(ws, f'document.querySelector("{wait_sel}") !== null')
        if ok in (True, 'true'):
            time.sleep(1.5)
            return True
        time.sleep(1)
    return False

# ── Offer-listing 卖家信息提取 JS ──────────────────────────

OFFER_SELLER_JS = r"""
(function() {
    var q = function(s) {
        var e = document.querySelector(s);
        return e ? e.textContent.trim().replace(/\s+/g, ' ') : '';
    };
    var qa = function(s, a) {
        var e = document.querySelector(s);
        return e ? (e[a] || e.getAttribute(a) || '') : '';
    };

    // Sold by
    var soldBy = '';
    var sellerA = document.querySelector('#sellerName a, #sellerName');
    if (sellerA) soldBy = sellerA.textContent.trim().replace(/\s+/g, ' ');
    // Fallback: offer box
    if (!soldBy) {
        var boxes = document.querySelectorAll('.a-section .a-spacing-mini');
        for (var i = 0; i < boxes.length; i++) {
            var t = boxes[i].textContent || '';
            var m = t.match(/sold by\s+([^\n<]{2,50})/i);
            if (m) { soldBy = m[1].trim(); break; }
        }
    }
    if (!soldBy) {
        var sp = document.querySelector('[data-feature-name="seller"]');
        if (sp) soldBy = sp.textContent.trim().replace(/\s+/g, ' ');
    }

    // Seller Store URL → extract seller ID (grab ALL of it from URL)
    // Priority: seller=XXXXX > stores/SHORT/page/UUID > stores/SHORT
    var storeUrl = '';
    var storeId = '';
    var links = document.querySelectorAll('a[href]');
    for (var i = 0; i < links.length; i++) {
        var href = links[i].href || '';
        if (!href.includes('seller=') && !href.includes('/stores/')) continue;
        // Priority 1: seller= (full merchant ID)
        var m = href.match(/seller=([A-Z0-9]+)/);
        if (m) { storeId = m[1]; storeUrl = href; break; }
        // Priority 2: /stores/SHORT/page/UUID (full store ID)
        m = href.match(/amazon\.com\/stores\/([A-Z0-9-]+)\/page\/([A-Z0-9-]+)/);
        if (m) { storeId = m[1] + '/' + m[2]; storeUrl = href; break; }
        // Priority 3: /stores/SHORT (short store ID)
        if (!storeId) {
            m = href.match(/amazon\.com\/stores\/([A-Z0-9-]+)/);
            if (m) { storeId = m[1]; storeUrl = href; }
        }
    }

    // Brand from product title
    var title = q('#productTitle');
    var brand = '';
    if (title) {
        var parts = title.split(/\s+/);
        if (parts[0]) brand = parts[0];
    }

    return JSON.stringify({
        sold_by: soldBy,
        seller_id: storeId,
        seller_store_url: storeUrl,
        brand: brand,
        url: window.location.href
    });
})()
"""

# ── 产品页备用提取 JS ──────────────────────────────────────

PRODUCT_SELLER_JS = r"""
(function() {
    var q = function(s) {
        var e = document.querySelector(s);
        return e ? e.textContent.trim().replace(/\s+/g, ' ') : '';
    };

    // Brand from detail table
    var brand = '';
    var tables = document.querySelectorAll('#productDetails_detailBullets_sections1 td');
    for (var i = 0; i < tables.length; i++) {
        var header = tables[i].textContent || '';
        var next = tables[i + 1] ? tables[i + 1].textContent || '' : '';
        if (/brand/i.test(header) && next) {
            brand = next.trim();
            break;
        }
    }
    // Fallback from title
    if (!brand) {
        var title = q('#productTitle');
        if (title) {
            var parts = title.split(/\s+/);
            if (parts[0]) brand = parts[0];
        }
    }

    // Sold by
    var soldBy = '';
    var sellerA = document.querySelector('#sellerName a, #sellerName');
    if (sellerA) soldBy = sellerA.textContent.trim().replace(/\s+/g, ' ');
    if (!soldBy) {
        var sp = document.querySelector('[data-feature-name="seller"]');
        if (sp) soldBy = sp.textContent.trim().replace(/\s+/g, ' ');
    }

    // Seller store link - full store ID from URL
    var storeUrl = '';
    var storeId = '';
    var links = document.querySelectorAll('a[href]');
    for (var i = 0; i < links.length; i++) {
        var href = links[i].href || '';
        if (!href.includes('seller=') && !href.includes('/stores/')) continue;
        var m = href.match(/seller=([A-Z0-9]+)/);
        if (m) { storeId = m[1]; storeUrl = href; break; }
        m = href.match(/amazon\.com\/stores\/([A-Z0-9-]+)\/page\/([A-Z0-9-]+)/);
        if (m) { storeId = m[1] + '/' + m[2]; storeUrl = href; break; }
        if (!storeId) {
            m = href.match(/amazon\.com\/stores\/([A-Z0-9-]+)/);
            if (m) { storeId = m[1]; storeUrl = href; }
        }
    }

    return JSON.stringify({
        brand: brand,
        sold_by: soldBy,
        seller_id: storeId,
        seller_store_url: storeUrl,
        url: window.location.href
    });
})()
"""

# ── 核心逻辑 ───────────────────────────────────────────────

def extract_brand_from_title(title: str) -> str:
    if not title:
        return ""
    words = title.split()
    return words[0].strip() if words else ""


def load_asins_from_reviews(reviews_path: str) -> list:
    """从评论 JSON 加载 ASIN 列表"""
    try:
        data = json.load(open(reviews_path))
        if isinstance(data, dict):
            return list(data.keys())
        elif isinstance(data, list):
            return [item.get("asin", "") for item in data if item.get("asin")]
    except Exception as e:
        print(f"[警告] 无法读取 {reviews_path}: {e}")
    return []


def fetch_sellers_for_asins(asins: list, ws, progress=True) -> dict:
    """对每个 ASIN 抓卖家信息"""
    results = {}
    
    for i, asin in enumerate(asins):
        if progress:
            print(f"[{i+1}/{len(asins)}] {asin}...", end=" ", flush=True)
        
        result = {"asin": asin, "brand": "", "sold_by": "", "seller_id": "", "seller_store_url": "", "url": ""}
        
        # 先试 offer-listing 页面（清理 ASIN 参数）
        asin_clean = re.sub(r'\?.*', '', asin)  # 去掉 ?th=1 等后缀
        url = f"https://www.amazon.com/gp/offer-listing/{asin_clean}/ref=olp_f_new&lm_rbd=2"
        ok = navigate_wait(ws, url, '.a-section, #sellerName, .olpOffer', timeout=15)
        
        if ok:
            raw = cdp_eval(ws, OFFER_SELLER_JS)
            if raw and raw != 'null':
                try:
                    info = json.loads(raw)
                    if info.get("sold_by") or info.get("seller_id"):
                        result.update(info)
                except json.JSONDecodeError:
                    pass
        
        # offer-listing 失败 → 试产品页
        if not result["sold_by"] and not result["seller_id"]:
            url2 = f"https://www.amazon.com/dp/{asin}"
            ok2 = navigate_wait(ws, url2, '#productTitle, #sellerName', timeout=15)
            if ok2:
                raw2 = cdp_eval(ws, PRODUCT_SELLER_JS)
                if raw2 and raw2 != 'null':
                    try:
                        info = json.loads(raw2)
                        if info.get("brand") or info.get("sold_by"):
                            result.update(info)
                    except json.JSONDecodeError:
                        pass
        
        # 兜底：用标题第一个词当品牌
        if not result["brand"]:
            result["brand"] = asin  # 放 ASIN，后面会替换
        
        results[asin] = result
        
        if progress:
            print(f"✅ {result['brand']} | {result['sold_by'] or '?'} | ID:{result['seller_id'] or '?'}")
        
        if i < len(asins) - 1:
            time.sleep(RATE_LIMIT)
    
    return results


# ── 入口 ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Amazon Seller 批量抓取（CDP Chrome）")
    parser.add_argument("--reviews", type=str, help="评论 JSON 路径（支持 glob 如 /tmp/reviews_*.json）")
    parser.add_argument("--asins", nargs="*", help="ASIN 列表")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome 调试端口")
    parser.add_argument("--output", type=str, help="输出 JSON 路径")
    parser.add_argument("--max", type=int, default=50, help="最多处理 ASIN 数")
    args = parser.parse_args()

    print("🔍 Amazon Seller 批量抓取（CDP Chrome）")
    print("=" * 50)
    
    # 收集 ASIN
    asins = []
    if args.asins:
        asins = args.asins[:args.max]
    elif args.reviews:
        import glob as gb
        files = gb.glob(args.reviews)
        print(f"扫描文件: {files}")
        for f in files:
            asins.extend(load_asins_from_reviews(f))
        asins = list(dict.fromkeys(asins))[:args.max]  # 去重
    else:
        print("[错误] 必须指定 --reviews 或 --asins")
        sys.exit(1)
    
    if not asins:
        print("[错误] 未找到 ASIN")
        sys.exit(1)
    
    print(f"📦 共 {len(asins)} 个 ASIN\n")
    
    # 连接 Chrome
    try:
        ws = get_page_ws(args.port)
        print(f"[CDP] ✅ 已连接 Chrome\n")
    except Exception as e:
        print(f"[错误] {e}")
        print("\n请先启动 Chrome：")
        print("  nohup /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\\\")
        print("    --remote-debugging-port=9222 \\\\")
        print("    --user-data-dir=~/Library/Application\\ Support/Google/Chrome \\\\")
        print("    --remote-allow-origins='*' > /tmp/chrome.log 2>&1 &")
        sys.exit(1)
    
    # 抓取
    results = fetch_sellers_for_asins(asins, ws)
    ws.close()
    
    # 保存
    out_path = args.output or OUTPUT_FILE.format(ts=datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(out_path, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    # 统计
    with_seller = sum(1 for r in results.values() if r.get("seller_id") or r.get("sold_by"))
    with_id = sum(1 for r in results.values() if r.get("seller_id"))
    
    print(f"\n✅ 完成！{len(results)}/{len(asins)} 个 ASIN 有卖家信息")
    print(f"   有 Seller ID: {with_id} 个")
    print(f"   有店铺名: {with_seller} 个")
    print(f"💾 已保存: {out_path}")


if __name__ == "__main__":
    main()
