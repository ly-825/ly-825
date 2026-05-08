#!/usr/bin/env python3
"""
天眼查公司查询（CDP Chrome）
"""

import json, time, argparse, subprocess, websocket, urllib.parse
from datetime import datetime

PORT = 18800

# ═══════════════════════════════════════
# Chrome CDP
# ═══════════════════════════════════════

def get_ws():
    info = json.loads(subprocess.check_output(["curl", "-s", f"http://localhost:{PORT}/json/list"]))
    for t in info:
        if t.get("type") == "page":
            return websocket.create_connection(t["webSocketDebuggerUrl"], timeout=120)
    raise RuntimeError("No Chrome tab")

def cdp_eval(ws, expr, timeout=25):
    cid = abs(hash(expr)) % 9999
    ws.send(json.dumps({"id": cid, "method": "Runtime.evaluate",
                       "params": {"expression": expr, "returnByValue": True}}))
    for _ in range(timeout):
        try:
            r = json.loads(ws.recv())
            if r.get("id") == cid:
                v = r.get("result", {}).get("result", {}).get("value", "")
                return v if isinstance(v, str) else json.dumps(v)
        except Exception:
            pass
        time.sleep(0.5)
    return ""

def navigate_wait(ws, url, wait_sel, timeout=15):
    ws.send(json.dumps({"id": 99, "method": "Page.navigate",
                        "params": {"url": url}}))
    time.sleep(timeout * 0.3)
    for _ in range(timeout):
        ok = cdp_eval(ws, f'document.querySelector("{wait_sel}") !== null')
        if ok in (True, "true"):
            time.sleep(1.5)
            return True
        time.sleep(1)
    return False

# ═══════════════════════════════════════
# 英文公司名 → 中文搜索词
# ═══════════════════════════════════════

TRANSLATIONS = {
    "shenzhen": ["深圳"],
    "guangzhou": ["广州"],
    "dongguan": ["东莞"],
    "beijing": ["北京"],
    "international": ["国际"],
    "trading": ["贸易"],
    "technology": ["科技"],
    "co": ["有限公司"],
    "ltd": ["有限公司"],
    "limited": ["有限公司"],
    "hk": ["香港"],
}

def translate_company(name):
    """英文名 → 多个中文搜索词"""
    parts = name.replace(",", " ").replace(".", " ").lower().split()
    city, others = "", []
    for p in parts:
        if p in TRANSLATIONS:
            vals = TRANSLATIONS[p]
            if p in ("shenzhen", "guangzhou", "dongguan", "beijing", "hk"):
                city = vals[0]
            else:
                others.extend(v for v in vals if v)
        elif p not in ("the", "and", "of", "a", "an", "inc"):
            others.append(p)

    queries = []
    if city and others:
        queries.append(city + " " + " ".join(others[:2]))
    if city:
        queries.append(city)
    if others:
        queries.append(" ".join(others[:3]))
    seen, uniq = set(), []
    for q in queries:
        q = q.strip()
        if q and q not in seen and len(q) > 2:
            seen.add(q)
            uniq.append(q)
    return uniq

# ═══════════════════════════════════════
# JS 片段
# ═══════════════════════════════════════

SEARCH_JS = r"""
(function() {
    var out = [];
    var links = document.querySelectorAll('a[href*="/company/"]');
    for (var i = 0; i < Math.min(links.length, 8); i++) {
        var t = links[i].innerText || "";
        if (t.length > 3 && t.length < 120) {
            out.push({name: t.substring(0, 80), url: links[i].href || ""});
        }
    }
    return JSON.stringify(out);
})()
"""

DETAIL_JS = r"""
(function() {
    var text = document.body.innerText.substring(0, 5000);
    var phone = "", email = "", rep = "", capital = "", addr = "";
    var m;
    m = text.match(/电话[：:]\s*([^\n电话]{5,30})/);
    if (m) phone = m[1].trim();
    m = text.match(/邮箱[：:]\s*([^\n邮箱]{5,50})/);
    if (m) email = m[1].trim();
    m = text.match(/法定代表人[：:]\s*([^\n]{2,20})/);
    if (m) rep = m[1].trim();
    m = text.match(/注册资本[：:]\s*([^\n]{5,40})/);
    if (m) capital = m[1].trim();
    m = text.match(/注册地址[：:]\s*([^\n]{5,120})/);
    if (m) addr = m[1].trim();
    return JSON.stringify({phone: phone, email: email, legal_rep: rep, reg_capital: capital, address: addr});
})()
"""

# ═══════════════════════════════════════
# 搜索 + 详情
# ═══════════════════════════════════════

def do_search(ws, company):
    print(f"  关键词: {company}")
    queries = [company]
    if all(ord(c) < 128 for c in company):
        queries = translate_company(company)
        queries.insert(0, company)
    print(f"  尝试: {queries}")
    for kw in queries:
        encoded = urllib.parse.quote(kw)
        ok = navigate_wait(ws,
            f"https://www.tianyancha.com/search?key={encoded}",
            "a[href*='/company/']")
        if not ok:
            time.sleep(2)
            continue
        time.sleep(2)
        raw = cdp_eval(ws, SEARCH_JS)
        if raw and raw not in ("", "null", "[]"):
            try:
                results = json.loads(raw)
                if results:
                    print(f"  搜到 {len(results)} 个")
                    return results
            except Exception:
                pass
        time.sleep(1)
    return []

def do_detail(ws, url):
    ok = navigate_wait(ws, url, "body")
    if not ok:
        return {}
    time.sleep(2)
    raw = cdp_eval(ws, DETAIL_JS)
    if raw and raw not in ("", "null"):
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}

def lookup(ws, company):
    print(f"  搜天眼查: {company}")
    results = do_search(ws, company)
    if not results:
        print("  未搜到")
        return {"status": "not_found", "company_name": company}
    top = results[0]
    print(f"  进详情: {top['name']}")
    detail = do_detail(ws, top["url"])
    detail["status"] = "found"
    detail["company_name"] = top["name"]
    detail["tianyancha_url"] = top["url"]
    detail["search_results"] = results
    print(f"  电话: {detail.get('phone', '?')}")
    print(f"  邮箱: {detail.get('email', '?')}")
    print(f"  法人: {detail.get('legal_rep', '?')}")
    print(f"  资本: {detail.get('reg_capital', '?')}")
    print(f"  地址: {detail.get('address', '?')}")
    return detail

# ═══════════════════════════════════════
# CLI
# ═══════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="天眼查公司查询（CDP Chrome）")
    ap.add_argument("--name", help="公司名")
    ap.add_argument("--batch", help="批量: seller JSON 路径")
    ap.add_argument("-o", "--output", dest="output", help="输出 JSON 路径")
    args = ap.parse_args()

    print("天眼查查询\n" + "=" * 40)
    try:
        ws = get_ws()
        print("CDP 连接成功\n")
    except Exception as e:
        print(f"连接失败: {e}")
        return

    try:
        if args.batch:
            sellers = json.load(open(args.batch))
            print(f"批量: {len(sellers)} 个\n")
            outputs = {}
            for i, (asin, info) in enumerate(sellers.items()):
                company = (info.get("business_name") or info.get("brand") or "").strip()
                print(f"[{i+1}/{len(sellers)}] {asin} - {company or '?'}")
                if not company:
                    outputs[asin] = {"status": "no_company", **info}
                    continue
                d = lookup(ws, company)
                outputs[asin] = {**info, **d}
                if i < len(sellers) - 1:
                    time.sleep(2)

            out_path = args.output or f"/tmp/tianyancha_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            json.dump(outputs, open(out_path, "w"), ensure_ascii=False, indent=2)
            found = sum(1 for v in outputs.values() if v.get("status") == "found")
            print(f"\n完成: {found}/{len(outputs)} 查到\n保存: {out_path}")

        elif args.name:
            d = lookup(ws, args.name)
            if args.output:
                json.dump(d, open(args.output, "w"), ensure_ascii=False, indent=2)
                print(f"\n已保存: {args.output}")

        else:
            ap.print_help()
    finally:
        ws.close()

if __name__ == "__main__":
    main()
