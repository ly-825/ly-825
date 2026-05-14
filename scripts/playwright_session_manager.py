#!/usr/bin/env python3
"""
Amazon 多站点 Playwright Persistent Context 会话管理器

功能：
  --setup        首次登录：逐个站点打开浏览器，手工登录后自动保存 Cookie
  --check        检查所有站点登录状态
  --keep-alive   定期保活：访问各站点首页，维持 Cookie 活性

原理：
  每个 Amazon 站点使用独立的 Persistent Context 目录。
  第一次登录后，Cookie/LocalStorage 写入硬盘。
  后续加载同一目录，直接恢复登录态。
  playwright-stealth 隐藏自动化特征，降低被踢概率。

目录结构：
  ~/.amazon-review-pipeline/playwright-profiles/
    ├── us/    ← 美国站 Cookie
    ├── jp/    ← 日本站 Cookie
    ├── de/    ← 德国站 Cookie
    └── ...

使用：
  python3 playwright_session_manager.py --setup              # 首次登录所有站点
  python3 playwright_session_manager.py --setup --regions us,jp  # 只登录指定站点
  python3 playwright_session_manager.py --check              # 检查所有站点
  python3 playwright_session_manager.py --keep-alive         # 保活所有站点
"""

import argparse
import asyncio
import os
import random
import sys
import time
from pathlib import Path

PROFILES_DIR = Path.home() / ".amazon-review-pipeline" / "playwright-profiles"

AMAZON_SITES = {
    "us": {"domain": "www.amazon.com",     "name": "美国", "tld": "com"},
    "uk": {"domain": "www.amazon.co.uk",   "name": "英国", "tld": "co.uk"},
    "de": {"domain": "www.amazon.de",      "name": "德国", "tld": "de"},
    "jp": {"domain": "www.amazon.co.jp",   "name": "日本", "tld": "co.jp"},
    "fr": {"domain": "www.amazon.fr",      "name": "法国", "tld": "fr"},
    "it": {"domain": "www.amazon.it",      "name": "意大利", "tld": "it"},
    "es": {"domain": "www.amazon.es",      "name": "西班牙", "tld": "es"},
    "ca": {"domain": "www.amazon.ca",      "name": "加拿大", "tld": "ca"},
    "in": {"domain": "www.amazon.in",      "name": "印度", "tld": "in"},
    "au": {"domain": "www.amazon.com.au",  "name": "澳大利亚", "tld": "com.au"},
    "mx": {"domain": "www.amazon.com.mx",  "name": "墨西哥", "tld": "com.mx"},
    "br": {"domain": "www.amazon.com.br",  "name": "巴西", "tld": "com.br"},
    "nl": {"domain": "www.amazon.nl",      "name": "荷兰", "tld": "nl"},
}


async def check_login_status(page) -> dict:
    """检查当前 Amazon 页面是否已登录"""
    script = r"""
    (function(){
        const text = (document.body && document.body.innerText || '').toLowerCase();
        const accountEl = document.querySelector('#nav-link-accountList, #nav-link-accountList-nav-line-1');
        const accountText = accountEl ? accountEl.innerText : '';
        const hasSignIn = /sign[\s-]*in|hello, sign in|ログイン|サインイン|请登录|anmelden|identifiez-vous|identifícate|accedi|iniciar sesión|faça seu login|inloggen|logga in|zaloguj się|welcome, sign|bonjour, identifiez/i.test(accountText);
        const hasCaptcha = /captcha|robot check|enter the characters|automated access|画像に表示|認証/i.test(text);
        const resultItems = document.querySelectorAll('[data-asin]').length;
        return JSON.stringify({
            hasSignIn, hasCaptcha, resultItems,
            accountText: accountText.slice(0, 60),
            title: (document.title || '').slice(0, 80),
            url: location.href
        });
    })()
    """
    try:
        result = await page.evaluate(script)
        import json
        return json.loads(result)
    except Exception as e:
        return {"error": str(e)}


async def setup_region(region_code: str, headless: bool = False):
    """为单个地区创建/更新 Persistent Context，引导用户登录"""
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    site = AMAZON_SITES[region_code]
    profile_dir = PROFILES_DIR / region_code
    profile_dir.mkdir(parents=True, exist_ok=True)

    is_first_time = not (profile_dir / "Default").exists()

    print(f"\n{'='*55}")
    print(f"  🌍 {region_code.upper()} - {site['name']} ({site['domain']})")
    if is_first_time:
        print(f"  🆕 首次设置，请在浏览器中登录 Amazon")
    else:
        print(f"  📂 已有 profile，检查登录状态...")

    async with async_playwright() as p:
        stealth = Stealth()
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        page = context.pages[0] if context.pages else await context.new_page()
        await stealth.apply_stealth_async(page)

        home_url = f"https://{site['domain']}/"
        try:
            await page.goto(home_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  ⚠️ 首次导航失败: {e}")
            # 重试一次
            await asyncio.sleep(3)
            try:
                await page.goto(home_url, wait_until="load", timeout=45000)
            except Exception:
                print(f"  ❌ 无法访问 {site['domain']}，跳过")
                await context.close()
                return {"region": region_code, "ok": False, "error": str(e)[:80]}
        await asyncio.sleep(3)

        status = await check_login_status(page)

        if status.get("hasCaptcha"):
            print(f"  ⚠️ 触发验证码，需要手动完成验证")
            if headless:
                await context.close()
                return {"region": region_code, "ok": False, "captcha": True}

        if status.get("hasSignIn") or not status.get("accountText"):
            if headless:
                print(f"  ❌ 未登录（headless 模式，跳过）")
                await context.close()
                return {"region": region_code, "ok": False, "need_login": True}

            print(f"  🔑 请在弹出的浏览器中登录 Amazon {site['name']}站")
            print(f"     ⚠️ 勾选「Keep me signed in / 保持登录状态」")
            print(f"     ⚠️ 登录成功后自动检测继续（最多等5分钟）...")
            print(f"     ⏳ 等待登录", end="", flush=True)

            # 轮询检测登录状态，最多等 5 分钟
            for poll_count in range(60):
                await asyncio.sleep(5)
                try:
                    # 只在当前页面上检测，不重新导航（避免导航冲突）
                    status = await check_login_status(page)
                except Exception:
                    # 如果页面不可用，尝试导航回去
                    try:
                        await page.goto(home_url, wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(2)
                        status = await check_login_status(page)
                    except Exception:
                        print("x", end="", flush=True)
                        continue

                if not status.get("hasSignIn") and status.get("accountText"):
                    print(" ✅")
                    print(f"     登录成功: {status.get('accountText','')[:50]}")
                    break
                print(".", end="", flush=True)
            else:
                print(" 超时")
                # 最后尝试一次重新导航后检测
                try:
                    await page.goto(home_url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(3)
                    status = await check_login_status(page)
                except Exception:
                    pass

        if status.get("hasSignIn") or not status.get("accountText"):
            print(f"  ❌ 登录未完成，跳过")
            await context.close()
            return {"region": region_code, "ok": False, "need_login": True}

        print(f"  ✅ 已登录: {status.get('accountText', 'OK')}")

        # 访问个人中心页面，让 Amazon 记录完整 session
        try:
            account_url = f"https://{site['domain']}/gp/css/homepage.html"
            await page.goto(account_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
        except Exception:
            pass

        await context.close()
        return {"region": region_code, "ok": True, "account": status.get("accountText", "")}


async def check_region(region_code: str, headless: bool = True):
    """无头检查单个地区的登录状态"""
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    site = AMAZON_SITES[region_code]
    profile_dir = PROFILES_DIR / region_code

    if not (profile_dir / "Default").exists():
        return {"region": region_code, "ok": False, "reason": "profile 不存在，请先 --setup"}

    async with async_playwright() as p:
        stealth = Stealth()
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await stealth.apply_stealth_async(page)

            home_url = f"https://{site['domain']}/"
            await page.goto(home_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            status = await check_login_status(page)
            await context.close()

            if status.get("hasCaptcha"):
                return {"region": region_code, "ok": False, "reason": "验证码"}
            if status.get("hasSignIn") or not status.get("accountText"):
                return {"region": region_code, "ok": False, "reason": "未登录"}
            return {"region": region_code, "ok": True, "account": status.get("accountText", "")}

        except Exception as e:
            return {"region": region_code, "ok": False, "reason": str(e)[:80]}


async def keep_alive_region(region_code: str):
    """访问站点首页和个人中心，保持 Cookie 活性"""
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    site = AMAZON_SITES[region_code]
    profile_dir = PROFILES_DIR / region_code

    if not (profile_dir / "Default").exists():
        return {"region": region_code, "ok": False, "reason": "profile 不存在"}

    async with async_playwright() as p:
        stealth = Stealth()
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
                viewport={"width": 1280, "height": 800},
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await stealth.apply_stealth_async(page)

            # 访问首页
            home_url = f"https://{site['domain']}/"
            await page.goto(home_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2, 4))
            await page.evaluate("window.scrollTo(0, Math.min(400, document.body.scrollHeight/3))")
            await asyncio.sleep(1)

            # 访问个人中心
            try:
                account_url = f"https://{site['domain']}/gp/css/homepage.html"
                await page.goto(account_url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
            except Exception:
                pass

            status = await check_login_status(page)
            await context.close()

            ok = not status.get("hasSignIn") and bool(status.get("accountText"))
            return {"region": region_code, "ok": ok, "captcha": status.get("hasCaptcha", False)}

        except Exception as e:
            return {"region": region_code, "ok": False, "reason": str(e)[:80]}


async def run_setup(regions: list, headless: bool = False):
    """首次登录所有指定站点"""
    print("🔑 Amazon 多站点首次登录设置")
    print(f"   站点数: {len(regions)}")
    print(f"   模式: {'无头（后台）' if headless else '有头（可视）'}")
    print(f"   Profile 目录: {PROFILES_DIR}")
    print()

    results = []
    for i, region in enumerate(regions, 1):
        print(f"[{i}/{len(regions)}]", end="")
        result = await setup_region(region, headless=headless)
        results.append(result)
        if i < len(regions):
            await asyncio.sleep(random.uniform(1, 2))

    print_summary("设置结果", results)


async def run_check(regions: list, headless: bool = True):
    """检查所有站点登录状态"""
    print("🔍 Amazon 登录状态检查")
    print(f"   站点数: {len(regions)}, 模式: {'无头' if headless else '有头'}")
    print()

    results = []
    for region in regions:
        site = AMAZON_SITES[region]
        print(f"  {region} ({site['name']})...", end=" ", flush=True)
        result = await check_region(region, headless=headless)
        icon = "✅" if result["ok"] else "❌"
        detail = result.get("account", result.get("reason", ""))
        print(f"{icon} {detail}")
        results.append(result)

    print_summary("检查结果", results)


def send_feishu_alert(message: str):
    """发送飞书提醒"""
    import subprocess
    try:
        subprocess.run([
            os.path.expanduser("~/.npm-global/bin/openclaw"),
            "message", "send",
            "--channel", "feishu",
            "--account", "competitor",
            "--target", "user:ou_2e20fbe54f0f207861644fe56396ef78",
            "--message", message,
        ], capture_output=True, timeout=30)
    except Exception:
        pass


async def run_keep_alive(regions: list):
    """保活所有站点"""
    print("🔄 Amazon 保活巡检")
    print(f"   站点数: {len(regions)}, 时间: {time.strftime('%Y-%m-%d %H:%M')}")
    print()

    results = []
    for region in regions:
        site = AMAZON_SITES[region]
        print(f"  {region} ({site['name']})...", end=" ", flush=True)
        result = await keep_alive_region(region)
        icon = "✅" if result["ok"] else ("⚠️" if result.get("captcha") else "❌")
        detail = "验证码" if result.get("captcha") else ("" if result["ok"] else result.get("reason", "失败"))
        print(f"{icon} {detail}")
        results.append(result)

    print_summary("保活结果", results)

    # 有异常时发飞书提醒
    failed = [r for r in results if not r["ok"]]
    if failed:
        failed_list = "\n".join(
            f"• {r['region']} ({AMAZON_SITES[r['region']]['name']})"
            f" - {'验证码' if r.get('captcha') else r.get('reason', '登录过期')}"
            for r in failed
        )
        msg = (
            f"⚠️ Amazon 登录异常提醒\n\n"
            f"以下站点需要重新登录：\n{failed_list}\n\n"
            f"修复命令：\n"
            f"python3 ~/.openclaw/workspace/skills/amazon-review-pipeline/"
            f"scripts/playwright_session_manager.py --setup --regions "
            f"{','.join(r['region'] for r in failed)}"
        )
        send_feishu_alert(msg)


def print_summary(title, results):
    """打印汇总"""
    ok = sum(1 for r in results if r["ok"])
    fail = len(results) - ok
    print(f"\n{'='*55}")
    print(f"  📊 {title}: {ok}/{len(results)} 正常", end="")
    if fail:
        print(f", {fail} 异常")
        failed_regions = [r["region"] for r in results if not r["ok"]]
        print(f"  ❌ 需处理: {', '.join(failed_regions)}")
        print(f"  运行 python3 {__file__} --setup --regions {','.join(failed_regions)} 补登")
    else:
        print(" ✅")
    print(f"{'='*55}")


async def main():
    parser = argparse.ArgumentParser(description="Amazon 多站点 Playwright 会话管理器")
    parser.add_argument("--setup", action="store_true", help="首次登录设置")
    parser.add_argument("--check", action="store_true", help="检查所有站点登录状态")
    parser.add_argument("--keep-alive", action="store_true", help="保活巡检")
    parser.add_argument("--regions", default="",
                        help="指定站点，逗号分隔（如 us,jp,de），默认全部")
    parser.add_argument("--headless", action="store_true", help="无头模式（默认 setup 可见，check/keep-alive 无头）")
    parser.add_argument("--visible", action="store_true", help="强制可见模式")
    args = parser.parse_args()

    # 解析站点
    if args.regions:
        regions = [r.strip() for r in args.regions.split(",") if r.strip() in AMAZON_SITES]
    else:
        regions = list(AMAZON_SITES.keys())
    if not regions:
        print("❌ 未指定有效站点")
        sys.exit(1)

    if args.setup:
        await run_setup(regions, headless=args.headless and not args.visible)
    elif args.check:
        await run_check(regions, headless=not args.visible)
    elif args.keep_alive:
        await run_keep_alive(regions)
    else:
        # 默认：检查状态
        print("未指定操作，默认执行 --check\n")
        await run_check(regions, headless=not args.visible)


if __name__ == "__main__":
    asyncio.run(main())
