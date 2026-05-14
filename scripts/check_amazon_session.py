#!/usr/bin/env python3

import argparse
import asyncio
import json
import random
import time
from urllib.parse import quote_plus

import requests
import websockets

AMAZON_DOMAINS = {
    "us": "www.amazon.com", "uk": "www.amazon.co.uk", "de": "www.amazon.de",
    "jp": "www.amazon.co.jp", "fr": "www.amazon.fr", "it": "www.amazon.it",
    "es": "www.amazon.es", "ca": "www.amazon.ca", "in": "www.amazon.in",
    "au": "www.amazon.com.au", "mx": "www.amazon.com.mx", "br": "www.amazon.com.br",
    "nl": "www.amazon.nl",
}

REGION_NAMES = {
    "us": "美国", "uk": "英国", "de": "德国", "jp": "日本", "fr": "法国",
    "it": "意大利", "es": "西班牙", "ca": "加拿大", "in": "印度",
    "au": "澳大利亚", "mx": "墨西哥", "br": "巴西", "nl": "荷兰",
}

DEFAULT_PORT = int(os.environ.get("CDP_PORT", "18800"))
CDP_TAB_IDS = {}


def open_new_tab(port: int, url: str) -> str:
    for attempt in range(5):
        try:
            resp = requests.put(f"http://localhost:{port}/json/new?{url}", timeout=10)
            if resp.status_code == 200:
                tab = resp.json()
                ws = tab.get("webSocketDebuggerUrl", "")
                if ws:
                    CDP_TAB_IDS[ws] = tab.get("id", "")
                    return ws
            if attempt < 4:
                time.sleep(1.5)
        except Exception:
            if attempt < 4:
                time.sleep(1.5)
    raise RuntimeError(f"无法连接 Chrome CDP，请确认 Chrome 已用 --remote-debugging-port={port} 启动")


def close_tab(port: int, ws_url: str):
    tab_id = CDP_TAB_IDS.pop(ws_url, "")
    if not tab_id:
        return
    try:
        requests.get(f"http://localhost:{port}/json/close/{tab_id}", timeout=3)
    except Exception:
        pass


async def cdp_send(ws_url: str, method: str, params: dict = None, timeout: int = 30):
    params = dict(params or {})
    params["returnByValue"] = True
    msg_id = random.randint(1, 999999)
    async with websockets.connect(ws_url, max_size=20 * 1024 * 1024) as conn:
        await conn.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        for _ in range(timeout * 2):
            try:
                raw = await asyncio.wait_for(conn.recv(), timeout=0.5)
                msg = json.loads(raw)
                if msg.get("id") == msg_id:
                    result = msg.get("result", {})
                    if "result" in result:
                        return result["result"].get("value", "")
                    return result.get("value", "")
            except asyncio.TimeoutError:
                continue
    return ""


async def inspect_page(ws_url: str):
    script = r"""
(function() {
    const text = (document.body && document.body.innerText || '').toLowerCase();
    const title = document.title || '';
    const url = location.href;
    const accountText = document.querySelector('#nav-link-accountList, #nav-link-accountList-nav-line-1, [data-nav-role="signin"]')?.innerText || '';
    const searchBox = !!document.querySelector('#twotabsearchtextbox, input[name="field-keywords"]');
    const resultItems = document.querySelectorAll('[data-asin][data-component-type="s-search-result"], [data-asin]').length;
    const hasCaptcha = /captcha|robot check|enter the characters|automated access|画像に表示されている文字|認証/.test(text);
    const signInHints = ['sign in', 'hello, sign in', 'signin', 'ログイン', 'サインイン', 'anmelden', 'identifiez-vous', 'accedi', 'iniciar sesión', 'olá, faça seu login'];
    const hasSignInHint = signInHints.some(x => text.includes(x)) || /sign in/i.test(accountText);
    const hasAccountName = accountText && !/sign in|ログイン|サインイン|anmelden|identifiez-vous|accedi|iniciar sesión/i.test(accountText);
    return JSON.stringify({url, title, accountText, searchBox, resultItems, hasCaptcha, hasSignInHint, hasAccountName});
})()
"""
    raw = await cdp_send(ws_url, "Runtime.evaluate", {"expression": script}, timeout=20)
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {"raw": raw}


async def check_region(region: str, keyword: str, port: int):
    domain = AMAZON_DOMAINS[region]
    home_url = f"https://{domain}/"
    search_url = f"https://{domain}/s?k={quote_plus(keyword)}"
    print(f"\n🌍 检查 {region} ({REGION_NAMES.get(region, region)}) - {domain}")
    print(f"   打开首页: {home_url}")
    home_ws = open_new_tab(port, home_url)
    try:
        await asyncio.sleep(5)
        home = await inspect_page(home_ws)
    finally:
        close_tab(port, home_ws)

    print(f"   打开搜索页: {search_url}")
    search_ws = open_new_tab(port, search_url)
    try:
        await asyncio.sleep(6)
        search = await inspect_page(search_ws)
    finally:
        close_tab(port, search_ws)

    issues = []
    if home.get("hasCaptcha") or search.get("hasCaptcha"):
        issues.append("触发验证码/Robot Check，请在浏览器里手动完成验证")
    if home.get("hasSignInHint") and not home.get("hasAccountName"):
        issues.append("可能未登录，请在该站点手动登录 Amazon 账号")
    if not search.get("searchBox"):
        issues.append("搜索页结构异常，可能被跳转、拦截或地区/语言弹窗遮挡")
    if search.get("resultItems", 0) == 0:
        issues.append("搜索结果为空或未加载，建议确认页面是否正常显示商品")

    if issues:
        print("   ⚠️ 状态: 需要处理")
        for issue in issues:
            print(f"   - {issue}")
    else:
        print("   ✅ 状态: 基本可用")
        print(f"   - 搜索页识别到结果节点: {search.get('resultItems', 0)}")
        if home.get("accountText"):
            print(f"   - 账号区域文本: {home.get('accountText')[:80]}")

    print("   建议: 使用同一个 Chrome user-data-dir 登录一次后，后续 pipeline 会复用 Cookie。")
    return {"region": region, "domain": domain, "home": home, "search": search, "issues": issues}


async def main_async(args):
    regions = args.regions or [args.region]
    results = []
    for region in regions:
        results.append(await check_region(region, args.keyword, args.port))
    failed = [r for r in results if r["issues"]]
    print("\n" + "=" * 72)
    print(f"检查完成: {len(results) - len(failed)}/{len(results)} 个站点基本可用")
    if failed:
        print("需要处理的站点: " + ", ".join(r["region"] for r in failed))
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(description="检查 Amazon 多地区登录态和页面可用性")
    parser.add_argument("--region", "-r", choices=list(AMAZON_DOMAINS.keys()), default="us", help="目标 Amazon 地区")
    parser.add_argument("--regions", nargs="*", choices=list(AMAZON_DOMAINS.keys()), help="一次检查多个地区")
    parser.add_argument("--keyword", "-k", default="water bottle", help="用于测试搜索页的关键词")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome CDP 端口")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
