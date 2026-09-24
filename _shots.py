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
    # 配好私有仓库之后会多出「立即上传 / 从仓库拉取 / 自动同步 / 清除」四个按钮，
    # 是另一套排版，单独出一张。写入的令牌是**编造的占位值**，不是真令牌；
    # 而且刻意从变量传入，免得在这里留下一段像令牌的东西 ——
    # 这个文件也在凭据护栏的扫描范围内，界面上也从不回显令牌。
    ("11-私有仓库已配置", "/?demo=1&c=m6",       "#/settings",    False,
     "var fake = 'sample' + '-not-a-real-token';"
     "localStorage.setItem('filmtap.github', JSON.stringify({"
     "owner:'Escaper929', repo:'film-tap-data', path:'data.json',"
     "branch:'main', auto:true, token:fake}))"),
    # 库存这一组要有数据才看得出排序和过期标签，所以都带 ?demo=1。
    ("12-胶卷库存",     "/?demo=1&c=m6",         "#/films",       False),
    ("13-新增库存",     "/?demo=1&c=m6",         "#/films/new",   False),
    ("14-库存导入",     "/?demo=1&c=m6",         "#/films/import",False),
    # 导入的第二步（对列）是整个过程里最容易出错的一屏，必须单独看一眼。
    # 它要求先把表格喂给页面，所以脚本得在**导航之后**再注入 —— 见下面 after。
    ("15-导入对列",     "/?demo=1&c=m6",         "#/films/import",False, None,
     "loadImportText("
     "'型号,数量,画幅,有效期,购入日期,单价,存放\\n'"
     "+ 'Kodak Portra 400,12,135,2027-06,2026-03-12,78,冰箱\\n'"
     "+ 'Fuji 分装,3,120,2026-11,2026-08-01,45,防潮箱\\n'"
     "+ 'Adox CMS 20 II,2,,2030-01,,64,\\n'"
     ", '我的库存.csv')"),
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
