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
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append("console." + m.type + ": " + m.text)
                if m.type == "error" else None)

        for name, path, hash_, reset in VIEWS:
            if reset:
                # 同一个浏览器上下文里本地存储是共享的，
                # 不主动清掉的话这张"空模板"截出来是上一张的状态。
                page.goto(BASE + "/", wait_until="load")
                page.evaluate("() => { try { localStorage.clear() } catch(e){} }")
            page.goto(BASE + path, wait_until="load")
            if hash_:
                page.evaluate("h => { location.hash = h }", hash_)
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
