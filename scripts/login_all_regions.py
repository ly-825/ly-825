#!/usr/bin/env python3
"""
Amazon 全站点一键登录助手
在 amazon.com 登录一次，自动传播到其余 14 个站点

使用：
  python3 login_all_regions.py           # 引导式登录
  python3 login_all_regions.py --check   # 仅检查所有站点登录状态
"""

import argparse, asyncio, json, random, time
import requests, websockets

AMAZON_DOMAINS = {
    "us": "www.amazon.com", "uk": "www.amazon.co.uk", "de": "www.amazon.de",
    "jp": "www.amazon.co.jp", "fr": "www.amazon.fr", "it": "www.amazon.it",
    "es": "www.amazon.es", "ca": "www.amazon.ca", "in": "www.amazon.in",
    "au": "www.amazon.com.au", "mx": "www.amazon.com.mx", "br": "www.amazon.com.br",
    "nl": "www.amazon.nl",
}

REGION_NAMES = {
    "us":"美国","uk":"英国","de":"德国","jp":"日本","fr":"法国","it":"意大利",
    "es":"西班牙","ca":"加拿大","in":"印度","au":"澳大利亚","mx":"墨西哥",
    "br":"巴西","nl":"荷兰",
}

DEFAULT_PORT = 18800


async def cdp_send(ws, method, params=None, timeout=15):
    params = dict(params or {})
    params["returnByValue"] = True
    msg_id = random.randint(1, 999999)
    await ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        if msg.get("id") == msg_id:
            result = msg.get("result", {})
            if "result" in result:
                return result["result"].get("value", "")
            return result.get("value", "")
    except asyncio.TimeoutError:
        pass
    return ""


async def check_login_status(ws) -> dict:
    """检查当前页面是否登录"""
    script = r"""
    (function(){
        const text = (document.body && document.body.innerText || '').toLowerCase();
        const accountEl = document.querySelector('#nav-link-accountList, #nav-link-accountList-nav-line-1');
        const accountText = accountEl ? accountEl.innerText : '';
        const hasSignIn = /sign[\s-]*in|hello, sign in|ログイン|サインイン|anmelden|identifiez-vous|accedi|iniciar sesión/i.test(accountText);
        const hasCaptcha = /captcha|robot check|automated access|画像に表示|enter the characters/i.test(text);
        const hasAccount = accountText.length > 0 && !hasSignIn;
        const hasContinue = /continue|続行|weiter|continuer|continuar/i.test(text);
        return JSON.stringify({hasSignIn, hasAccount, hasCaptcha, hasContinue, accountText: accountText.slice(0,50)});
    })()
    """
    result = await cdp_send(ws, "Runtime.evaluate", {"expression": script})
    try:
        return json.loads(result) if result else {}
    except Exception:
        return {}


async def login_region(port, region, domain):
    """打开站点，检查状态，支持一键 Continue"""
    print(f"\n  🌍 {region} ({REGION_NAMES[region]}) - {domain}")

    resp = requests.put(f"http://localhost:{port}/json/new?https://{domain}/", timeout=10)
    tab = resp.json()
    ws_url = tab.get("webSocketDebuggerUrl", "")
    tab_id = tab.get("id", "")

    if not ws_url:
        return {"region": region, "ok": False, "error": "CDP 连接失败"}

    async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
        await asyncio.sleep(5)
        status = await check_login_status(ws)

        if status.get("hasCaptcha"):
            print(f"     ⚠️ 验证码，需手动处理")
            return {"region": region, "ok": False, "captcha": True}

        if status.get("hasAccount") and not status.get("hasSignIn"):
            print(f"     ✅ 已登录: {status.get('accountText','')}")
            return {"region": region, "ok": True}

        if status.get("hasContinue"):
            # 尝试自动点击 Continue
            print(f"     🔄 检测到 Continue 提示，自动确认...")
            await cdp_send(ws, "Runtime.evaluate", {
                "expression": r"""
                (function(){
                    const btns = document.querySelectorAll('a, button, input[type=submit]');
                    for (const b of btns) {
                        if (/continue|続行|weiter|continuer|continuar/i.test(b.innerText || b.value || '')) {
                            b.click(); return 'clicked: ' + (b.innerText||b.value||'').slice(0,30);
                        }
                    }
                    return 'no button found';
                })()
                """})
            await asyncio.sleep(4)
            status2 = await check_login_status(ws)
            if status2.get("hasAccount") and not status2.get("hasSignIn"):
                print(f"     ✅ Continue 后已登录")
                return {"region": region, "ok": True}

        if status.get("hasSignIn") or not status.get("hasAccount"):
            print(f"     ❌ 未登录")

    # 关闭标签
    if tab_id:
        try:
            requests.get(f"http://localhost:{port}/json/close/{tab_id}", timeout=3)
        except Exception:
            pass

    return {"region": region, "ok": False, "need_login": True}


async def main():
    parser = argparse.ArgumentParser(description="Amazon 全站点一键登录")
    parser.add_argument("--check", action="store_true", help="仅检查登录状态")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    regions = list(AMAZON_DOMAINS.keys())
    # us 排第一（主登录站点），其余按字母排
    regions = ["us"] + sorted(r for r in regions if r != "us")

    if args.check:
        print("🔍 Amazon 全站点登录状态检查")
    else:
        print("🔑 Amazon 全站点一键登录")
        print(f"\n⏳ 即将在 Chrome 中打开 Amazon 美国站")
        print("   在浏览器中登录你的 Amazon 账号（勾选 Keep me signed in）")
        print("   登录完成后，回到这里按 Enter 继续自动传播到其余 14 个站点...")
        print()

        # 第一步：打开美国站让用户手动登录
        domain = AMAZON_DOMAINS["us"]
        resp = requests.put(f"http://localhost:{args.port}/json/new?https://{domain}/", timeout=10)
        tab = resp.json()
        ws_url = tab.get("webSocketDebuggerUrl", "")
        tab_id = tab.get("id", "")

        print(f"   📂 已打开 https://{domain}/")
        print(f"   👆 请在 Chrome 窗口中登录 Amazon...")
        input(f"   ✅ 登录完成后按 Enter 继续...")

        # 确认登录成功
        async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
            await asyncio.sleep(2)
            status = await check_login_status(ws)
            if not status.get("hasAccount") or status.get("hasSignIn"):
                print(f"   ⚠️ 看起来还没登录成功，继续传播但可能需要逐个登录\n")
            else:
                print(f"   ✅ 美国站登录确认: {status.get('accountText','')}\n")

        if tab_id:
            try:
                requests.get(f"http://localhost:{args.port}/json/close/{tab_id}", timeout=3)
            except Exception:
                pass

        # 第二步：自动传播到其他站点
        other_regions = [r for r in regions if r != "us"]
        print(f"🚀 自动传播登录到 {len(other_regions)} 个站点...")
    else:
        other_regions = regions

    # 逐个检查/传播
    ok_list, fail_list = [], []
    for region in regions if args.check else other_regions:
        domain = AMAZON_DOMAINS[region]
        result = await login_region(args.port, region, domain)
        if result["ok"]:
            ok_list.append(region)
        else:
            fail_list.append(region)
        await asyncio.sleep(random.uniform(1.5, 3))

    # 汇总
    print(f"\n{'='*55}")
    print(f"📊 登录状态汇总")
    print(f"{'='*55}")
    print(f"  ✅ 正常 ({len(ok_list)}): {', '.join(f'{r}({REGION_NAMES[r]})' for r in ok_list) if ok_list else '无'}")
    if fail_list:
        print(f"  ❌ 需登录 ({len(fail_list)}): {', '.join(f'{r}({REGION_NAMES[r]})' for r in fail_list)}")
        print(f"\n  对于未成功的站点，重新运行此脚本即可自动补登。")
        print(f"  如果一键 Continue 不生效，手动在 Chrome 中点击对应标签页登录。")
    print(f"{'='*55}")

asyncio.run(main())
