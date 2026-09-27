#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给网页 App 出一组界面截图，用来肉眼验收排版。

用本机已装的 Edge（channel="msedge"），不需要额外下载浏览器内核。

用法：
    python _shots.py                 # 默认打 127.0.0.1:8123
    python _shots.py http://localhost:8000
输出：shots/*.png
"""

import os
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8123"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")

# 截图里要看的登录态，一律是**注入的占位状态**，不是真凭据。
# 早先这里造的是 GitHub 私有仓库的登录态（头像、令牌、仓库列表）；
# 那整套功能已经拆掉了，现在只剩自建 NAS 这一栏 —— 它的状态是
# “探活成功 / 会话有效”两个布尔值，注入起来不涉及任何凭据。

VIEWS = [
    ("01-机库",        "/?demo=1",              "#/",            False),
    ("02-机身卡片",     "/?demo=1&c=m6",         None,            False),
    ("03-该冲了",       "/?demo=1&c=xa2",        None,            False),
    ("04-换卷表单",     "/?demo=1&c=m6",         "#/c/m6/load",   False),
    ("05-历史记录",     "/?demo=1&c=m6",         "#/c/m6/history",False),
    ("06-新挂件登记",   "/?demo=1&c=brand-new",  None,            False),
    ("07-设置与备份",   "/?demo=1&c=m6",         "#/settings",    False),
    ("08-统计",        "/?demo=1&c=m6",         "#/stats",       False),
    ("09-编辑机身",     "/?demo=1&c=rollei",     "#/c/rollei/setup", False),
    ("10-空模板首页",   "/",                    "#/",            True),
    ("11-胶卷库存",     "/?demo=1&c=m6",         "#/films",       False),
    ("12-新增库存",     "/?demo=1&c=m6",         "#/films/new",   False),
    ("13-库存导入",     "/?demo=1&c=m6",         "#/films/import",False),
    # 导入的第二步（对列）是整个过程里最容易出错的一屏，必须单独看一眼。
    # 它要求先把表格喂给页面，所以脚本得在**导航之后**再注入 —— 见下面 after。
    ("14-导入对列",     "/?demo=1&c=m6",         "#/films/import",False, None,
     "loadImportText("
     "'型号,数量,画幅,有效期,购入日期,单价,存放\\n'"
     "+ 'Kodak Portra 400,12,135,2027-06,2026-03-12,78,冰箱\\n'"
     "+ 'Fuji 分装,3,120,2026-11,2026-08-01,45,防潮箱\\n'"
     "+ 'Adox CMS 20 II,2,,2030-01,,64,\\n'"
     ", '我的库存.csv')"),
    # 「没连上服务端就不进应用」之后，这一屏成了整个应用的入口 ——
    # 截图默认打的是本机静态服务器（探不到 /api/health），所以这里把
    # gate 直接摆成 login 再渲染：状态是注入的，排版是真的。
    # 用 setTimeout 兜一下，避免和启动时那次异步探测（会把它改回 offline）抢。
    ("15-连接NAS入口",  "/?demo=1&c=m6",        "#/settings",    True,  None,
     "setTimeout(() => { gate='login'; render(true); }, 60);"),
    ("16-自建NAS已连上", "/?demo=1&c=m6",        "#/settings",    True,  None,
     "setTimeout(() => { gate='ready'; nasState='yes'; nasSess=true;"
     " saveNas({at:'2026-09-27T07:30:00Z'}); render(true); }, 60);"),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        ctx = browser.new_context(
            viewport={"width": 402, "height": 874},   # iPhone 16 逻辑分辨率
            device_scale_factor=2,
            locale="zh-CN",
        )
        page = ctx.new_page()
        errs = []

        def on_console(m):
            if m.type != "error":
                return
            # 页面启动时会探一次 /api/health，用来判断自己是不是跑在自建后端上。
            # 静态服务器上这个请求必然 404 —— 那正是「不是自建」这个
            # **答案本身**，不是故障。所以只放过打到 /api/ 的 404，
            # 别的报错照旧拦下来。
            # ⚠️ 路径不在 m.text 里（那里只有「status of 404」这几个字），
            #    得从 m.location 取 URL。
            try:
                url = (m.location or {}).get("url", "") or ""
            except Exception:
                url = ""
            if "/api/" in url and "404" in m.text:
                return
            errs.append("console.error: " + m.text)

        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", on_console)

        for row in VIEWS:
            name, path, hash_, reset = row[0], row[1], row[2], row[3]
            inject = row[4] if len(row) > 4 else None
            after  = row[5] if len(row) > 5 else None

            if reset or inject:
                # 同一个浏览器上下文里本地存储是共享的，
                # 不主动清掉的话这张"空模板"截出来是上一张的状态。
                page.goto(BASE + "/", wait_until="load")
                page.evaluate("() => { try { localStorage.clear() } catch(e){} }")
                if inject:
                    page.evaluate("s => eval(s)", inject)

            page.goto(BASE + path, wait_until="load")
            if hash_:
                page.evaluate("h => { location.hash = h }", hash_)
            # 导航会把页面里的中间状态（比如导入的第一步）清空，
            # 所以需要"先到那一屏、再喂状态"的用 after，不能用 inject。
            if after:
                page.evaluate("s => eval(s)", after)
            page.wait_for_timeout(400)
            f = os.path.join(OUT, name + ".png")
            page.screenshot(path=f, full_page=True)
            h = page.evaluate("document.querySelector('#app').innerHTML.length")
            title = page.evaluate("(document.querySelector('h1')||{}).textContent || ''")
            n_banner = page.evaluate("document.querySelectorAll('.banner').length")
            print("%-16s 渲染 %5d 字符   标题「%s」  提示条 %d" % (name, h, title, n_banner))

        browser.close()

    if errs:
        print("\n页面报错：")
        for e in errs:
            print("  " + e)
        sys.exit(1)
    print("\n无 JS 报错，截图已写入 shots/")


if __name__ == "__main__":
    main()
