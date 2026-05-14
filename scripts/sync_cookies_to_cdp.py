#!/usr/bin/env python3
"""
Cookie 同步脚本：Playwright Persistent Context → Chrome CDP

从 Playwright 管理的各站点 profile 中提取 Amazon Cookie，
通过 CDP Network.setCookie 注入到 Chrome（让现有 pipeline 复用登录态）。

用法：
  python3 sync_cookies_to_cdp.py            # 同步所有站点
  python3 sync_cookies_to_cdp.py --regions us,jp,de  # 只同步指定站点
  python3 sync_cookies_to_cdp.py --from-profile us   # 用指定站点的 cookie 覆盖所有站点
"""

import argparse
import asyncio
import json
import os
import random
import sys
from pathlib import Path

import requests
import websockets

PROFILES_DIR = Path.home() / ".amazon-review-pipeline" / "playwright-profiles"
CDP_PORT = int(os.environ.get("CDP_PORT", "18800"))

AMAZON_SITES = {
    "us": {"domain": "www.amazon.com",     "name": "美国"},
    "uk": {"domain": "www.amazon.co.uk",   "name": "英国"},
    "de": {"domain": "www.amazon.de",      "name": "德国"},
    "jp": {"domain": "www.amazon.co.jp",   "name": "日本"},
    "fr": {"domain": "www.amazon.fr",      "name": "法国"},
    "it": {"domain": "www.amazon.it",      "name": "意大利"},
    "es": {"domain": "www.amazon.es",      "name": "西班牙"},
    "ca": {"domain": "www.amazon.ca",      "name": "加拿大"},
    "in": {"domain": "www.amazon.in",      "name": "印度"},
    "au": {"domain": "www.amazon.com.au",  "name": "澳大利亚"},
    "mx": {"domain": "www.amazon.com.mx",  "name": "墨西哥"},
    "br": {"domain": "www.amazon.com.br",  "name": "巴西"},
    "nl": {"domain": "www.amazon.nl",      "name": "荷兰"},
}


async def check_cdp_alive(port: int) -> bool:
    """检查 Chrome CDP 是否在运行"""
    try:
        resp = requests.get(f"http://localhost:{port}/json/list", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def get_cdp_ws(port: int, domain: str) -> str:
    """获取 Chrome CDP 中已打开的目标站点 WebSocket，没有则新建标签"""
    try:
        resp = requests.get(f"http://localhost:{port}/json/list", timeout=5)
        pages = resp.json()
        # 找已打开的对应站点页面
        for page in pages:
            if page.get("type") == "page" and domain in page.get("url", ""):
                return page["webSocketDebuggerUrl"]
    except Exception:
        pass

    # 没有对应页面，新建标签
    resp = requests.put(
        f"http://localhost:{port}/json/new?https://{domain}/",
        timeout=10
    )
    tab = resp.json()
    ws = tab.get("webSocketDebuggerUrl", "")
    if not ws:
        raise RuntimeError(f"无法为 {domain} 创建 CDP 标签")
    return ws


async def inject_cookie(ws, cookie: dict, domain: str) -> bool:
    """通过 CDP 注入单个 cookie"""
    params = {
        "name": cookie["name"],
        "value": cookie["value"],
        "domain": cookie.get("domain", f".{domain}"),
        "path": cookie.get("path", "/"),
    }
    if cookie.get("secure"):
        params["secure"] = True
    if cookie.get("httpOnly"):
        params["httpOnly"] = True
    if cookie.get("sameSite"):
        same = cookie["sameSite"].lower()
        if same in ("lax", "strict", "none"):
            params["sameSite"] = same.capitalize()
    if cookie.get("expires") and cookie["expires"] > 0:
        params["expires"] = cookie["expires"]

    msg_id = random.randint(1, 999999)
    await ws.send(json.dumps({
        "id": msg_id,
        "method": "Network.setCookie",
        "params": params,
    }))
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=8)
        result = json.loads(raw).get("result", {})
        return result.get("success", False)
    except asyncio.TimeoutError:
        return False


async def get_cookies_from_playwright(region_code: str) -> list:
    """从 Playwright profile 读取 Amazon cookies"""
    from playwright.async_api import async_playwright

    profile_dir = PROFILES_DIR / region_code
    if not (profile_dir / "Default").exists():
        print(f"     ⚠️ {region_code}: profile 不存在，跳过")
        return []

    site = AMAZON_SITES[region_code]
    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
                viewport={"width": 1280, "height": 800},
            )
            # 确保页面加载，让 cookies 可用
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(
                    f"https://{site['domain']}/",
                    wait_until="domcontentloaded",
                    timeout=20000
                )
                await asyncio.sleep(1)
            except Exception:
                pass

            cookies = await context.cookies()
            await context.close()

            # 过滤：只要 Amazon 相关的 cookie
            amazon_cookies = [
                c for c in cookies
                if "amazon" in c.get("domain", "")
            ]
            return amazon_cookies
        except Exception as e:
            print(f"     ⚠️ {region_code}: 读取失败 - {e}")
            return []


async def sync_region(region_code: str, port: int) -> dict:
    """同步单个站点的 cookie 到 CDP"""
    site = AMAZON_SITES[region_code]
    domain = site["domain"]
    print(f"  {region_code} ({site['name']})...", end=" ", flush=True)

    # 1. 从 Playwright 读 cookie
    cookies = await get_cookies_from_playwright(region_code)
    if not cookies:
        print("❌ 无 cookie")
        return {"region": region_code, "ok": False, "count": 0}

    # 2. 注入到 CDP
    try:
        ws = get_cdp_ws(port, domain)
    except Exception as e:
        print(f"❌ CDP 连接失败: {e}")
        return {"region": region_code, "ok": False, "count": 0}

    success = 0
    async with websockets.connect(ws, max_size=10 * 1024 * 1024) as conn:
        for cookie in cookies:
            if await inject_cookie(conn, cookie, domain):
                success += 1

    print(f"✅ {success}/{len(cookies)} cookies")
    return {"region": region_code, "ok": success > 0, "count": success}


async def sync_from_profile(source_region: str, target_regions: list, port: int):
    """从单个 Playwright profile 提取 cookie，注入到 CDP 的所有目标站点"""
    site = AMAZON_SITES[source_region]
    print(f"📤 从 {source_region} ({site['name']}) 提取 cookie...")
    cookies = await get_cookies_from_playwright(source_region)

    if not cookies:
        print("❌ 无 cookie")
        return

    print(f"   获取 {len(cookies)} 个 Amazon cookies")
    print(f"📥 注入到 {len(target_regions)} 个站点...")

    results = []
    for region in target_regions:
        target_site = AMAZON_SITES[region]
        target_domain = target_site["domain"]
        print(f"  {region} ({target_site['name']})...", end=" ", flush=True)

        try:
            ws = get_cdp_ws(port, target_domain)
        except Exception as e:
            print(f"❌ CDP 失败: {e}")
            results.append({"region": region, "ok": False})
            continue

        success = 0
        async with websockets.connect(ws, max_size=10 * 1024 * 1024) as conn:
            for cookie in cookies:
                if await inject_cookie(conn, cookie, target_domain):
                    success += 1

        print(f"✅ {success}/{len(cookies)}")
        results.append({"region": region, "ok": success > 0, "count": success})

    ok = sum(1 for r in results if r["ok"])
    print(f"\n📊 同步完成: {ok}/{len(results)} 站点成功")


async def main():
    parser = argparse.ArgumentParser(description="Playwright → Chrome CDP Cookie 同步")
    parser.add_argument("--regions", default="",
                        help="目标站点，逗号分隔（默认全部）")
    parser.add_argument("--from-profile", default="",
                        help="从指定 Playwright profile 提取 cookie，注入到所有目标站点")
    parser.add_argument("--port", type=int, default=CDP_PORT,
                        help=f"Chrome CDP 端口（默认 {CDP_PORT}）")
    args = parser.parse_args()

    # 解析站点
    if args.regions:
        regions = [r.strip() for r in args.regions.split(",") if r.strip() in AMAZON_SITES]
    else:
        regions = list(AMAZON_SITES.keys())

    # 检查 CDP 是否在线
    if not await check_cdp_alive(args.port):
        print(f"❌ Chrome CDP 未运行（端口 {args.port}）")
        print(f"   请先执行: scripts/start_chrome_cdp.sh")
        sys.exit(1)

    print("🔄 Playwright → Chrome CDP Cookie 同步")
    print(f"   CDP 端口: {args.port}")

    if args.from_profile:
        if args.from_profile not in AMAZON_SITES:
            print(f"❌ 无效的源站点: {args.from_profile}")
            sys.exit(1)
        await sync_from_profile(args.from_profile, regions, args.port)
    else:
        print(f"   同步站点数: {len(regions)}")
        print()
        results = []
        for region in regions:
            result = await sync_region(region, args.port)
            results.append(result)
        ok = sum(1 for r in results if r["ok"])
        print(f"\n📊 同步完成: {ok}/{len(results)} 站点成功")


if __name__ == "__main__":
    asyncio.run(main())
