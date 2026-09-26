#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从零跑一遍首次使用路径 —— 在**真实部署的页面上**点，不是在内存里造数据。

为什么单独有这么一个文件：`_selftest.js` 是直接 `save(demoData())` + 直接跳哈希，
从来不点按钮、不进表单、不经过 Service Worker。于是「空库 → 登记第一台机身 →
装卷 → 刷新 → 数据还在」这条**每个新用户唯一会走的**路径，在自检里是完全空白的。
这个脚本专门补它，同时顺带验证线上拿到的确实是新版（SW 有没有同步）。

默认打线上 Pages，因为「本地跑通」和「线上生效」是两件事：
- 本地过、线上没过 → sw.js 忘了动，手机还在吃旧缓存
- 线上过、本地没过 → 基本不可能，但也能立刻看出来

用法：
    python _firstrun.py                        # 线上 Pages
    python _firstrun.py http://127.0.0.1:8123   # 本地服务

退出码非 0 = 有失败项或有 JS 报错。需要 playwright（用本机已装的 Edge，
不额外下载内核，跟 _shots.py 一致）。
"""

import sys
from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://escaper929.github.io/film-tap").rstrip("/")

ok, bad = [], []


def check(name, cond, note=""):
    (ok if cond else bad).append(name)
    print("%-34s %s  %s" % (name, "通过" if cond else "不对", note))


def app(page):
    return page.evaluate("document.querySelector('#app').innerHTML")


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge")
    ctx = browser.new_context(
        viewport={"width": 402, "height": 874},
        device_scale_factor=2,
        locale="zh-CN",
    )
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
    page.on("console", lambda m: errs.append("console.error: " + m.text) if m.type == "error" else None)

    # 空库第一次点「登记第一台机身」会弹原生 prompt
    page.on("dialog", lambda d: d.accept("我的第一台"))

    # ── ① 空状态 ──
    page.goto(BASE + "/", wait_until="load")
    page.wait_for_timeout(600)
    h = app(page)
    check("空库首页是空状态", "还没有登记任何机身" in h and "登记第一台机身" in h,
          "%d 字符" % len(h))
    check("空库首页没有示例横幅", "这是示例数据" not in h)
    check("空库首页四个入口齐", all(s in h for s in
          ["登记第一台机身", "胶卷库存", "载入示例数据", "设置与备份"]))
    # 空库时不该有「干净数据」之类的东西自己冒出来
    check("空库不写脏 localStorage",
          page.evaluate("Object.keys(localStorage).filter(k=>k.indexOf('filmtap')===0).length") == 0,
          str(page.evaluate("Object.keys(localStorage).filter(k=>k.indexOf('filmtap')===0)")))

    # ── ② 登记第一台机身（走原生 prompt）──
    page.click("button:has-text('登记第一台机身')")
    page.wait_for_timeout(500)
    h = app(page)
    check("prompt 之后落在机身页", "我的第一台" in h and "当前是空的" in h)
    cam_hash = page.evaluate("location.hash")
    check("机身 ID 自动分配", cam_hash.startswith("#/c/cam-"), cam_hash)
    check("空机身主按钮是装一卷", "装一卷" in h and "换一卷" not in h)

    # ── ③ 装卷（真实填表 + 提交）──
    page.click("button:has-text('装一卷')")
    page.wait_for_timeout(400)
    h = app(page)
    check("装卷表单有型号/ISO/总张数", all(s in h for s in
          ["胶卷型号", "标称 ISO", "这一卷总共多少张"]))
    check("装卷表单不再问已拍张数", "已经拍了多少张" not in h and "f-shots" not in h)
    check("总张数按画幅预填 36", 'id="f-total"' in h and 'value="36"' in h)

    page.fill("#f-stock", "Kodak Portra 400")
    page.fill("#f-iso", "400")
    page.fill("#f-ei", "1600")
    page.click("button:has-text('确认装卷')")
    page.wait_for_timeout(500)
    h = app(page)
    check("装卷后回到机身页", "Kodak Portra 400" in h and cam_hash in page.evaluate("location.hash"))
    check("装卷后能看到推拉提示", "推 2 档" in h)
    check("机身页没有过片按钮", "过片" not in h and "advanceShot" not in h)
    check("机身页没有进度条", 'class="filmbar"' not in h and "还剩" not in h)
    check("机身页主按钮已是换一卷", "换一卷" in h and 'btn primary' in h)

    # ── ④ 刷新一次，数据要活着（顺带验证 SW 装上后不影响）──
    page.reload(wait_until="load")
    page.wait_for_timeout(700)
    h = app(page)
    check("刷新后数据还在", "Kodak Portra 400" in h and "我的第一台" in h)
    # app 只在 https 下注册 SW（`location.protocol === "https:"` 那道判断），
    # 所以拿 http 本地服务跑的时候这条天然不成立 —— 跳过，不算失败。
    if BASE.startswith("https:"):
        check("刷新后 SW 已接管",
              page.evaluate("!!navigator.serviceWorker.controller") is True)
    else:
        print("%-34s %s" % ("刷新后 SW 已接管", "跳过（http 本地跑，SW 只在 https 注册）"))

    # ── ⑤ 统计页：一台上不了卷的机身 ──
    page.evaluate("location.hash = '#/stats'")
    page.wait_for_timeout(400)
    h = app(page)
    n = h.count('<div class="stat">')
    check("统计页三格", n == 3, "实际 %d 格" % n)
    check("统计页无累计快门", "累计快门" not in h and "按满卷算" not in h)

    # ── ⑥ 设置页：未登录时的三步向导 ──
    page.evaluate("location.hash = '#/settings'")
    page.wait_for_timeout(400)
    h = app(page)
    check("设置页三步向导在", all(s in h for s in
          ["验证并登录", "建一个私有仓库", "建一枚 fine-grained 令牌"]),
          "文案齐" if "建一个私有仓库" in h else "缺文案")

    # ── ⑥b WebDAV 的两个前提（先跑这条，它不改 gh 那边的状态）──
    # 这个页面若是 https，http 地址就**根本发不出请求**（混合内容），
    # 拦得很彻底，现象和「NAS 没开机」一模一样 —— 保存时就该挡下来。
    # ⚠️ 但守卫本身是判 location.protocol 的，所以拿 http 本地服务跑时
    #    它**不应该**触发（同协议不受混合内容约束）。这一点必须跟着 BASE 走。
    check("NAS 区有测试连接按钮", "testDav()" in app(page))
    page.fill("#dav-url", "http://" + "nas.example.com:5005/dav/film/")
    page.fill("#dav-user", "filmapp")
    page.click("button:has-text('保存同步设置')")
    page.wait_for_timeout(300)
    guard2 = page.evaluate("document.querySelector('#toast').textContent")
    saved_http = page.evaluate("!!localStorage.getItem('filmtap.webdav')")
    if BASE.startswith("https:"):
        check("http 地址被挡下且没保存",
              ("https 页面发不出 http 请求" in guard2) and not saved_http, repr(guard2))
    else:
        check("http 地址在 http 页面上放行", saved_http, repr(guard2))

    page.fill("#dav-url", "https://" + "nas.example.com/dav/film/")
    page.click("button:has-text('保存同步设置')")
    page.wait_for_timeout(300)
    check("https 地址可以保存",
          page.evaluate("!!localStorage.getItem('filmtap.webdav')"))

    # 真的点一次「测试连接」。用 .invalid（RFC 2606 保留，保证解析不了），
    # 这样既不去碰任何真实主机，结论也是确定的 —— 应该落在
    # 「连不到这个地址」那一层，正好验证分层诊断的第一层是通的。
    page.fill("#dav-url", "https://" + "no-such-nas.invalid/dav/film/")
    mark = len(errs)
    page.click("button:has-text('测试连接')")
    page.wait_for_timeout(2500)
    probe = page.evaluate("document.querySelector('#toast').textContent")
    check("测试连接给出分层结论", "连不到这个地址" in probe, repr(probe))
    # 这次探测是**故意**打一个坏域名的，浏览器会为它记一条 net::ERR_ 资源错误。
    # 那是预期内的，不该算成页面报错 —— 只把这一段里的那类错误滤掉。
    errs[mark:] = [e for e in errs[mark:] if "Failed to load resource" not in e]
    page.evaluate("() => { try { localStorage.removeItem('filmtap.webdav') } catch(e){} }")

    # ── ⑥c 令牌框 / 验证按钮的联动 ──
    # 这是用户在真实手机上踩到的坑：按钮一直亮着、空着也能点，
    # 点完只回一句「先粘一枚令牌」—— 分不清是自己没粘上还是功能坏了。
    # 现在空着必须是禁用的，粘进去才亮。
    dis = lambda: page.evaluate("!!document.querySelector('#gh-verify').disabled")
    check("未粘令牌时验证按钮禁用", dis() is True)
    check("令牌框挂上了 oninput", 'oninput="syncGhVerifyBtn()"' in h)

    page.fill("#gh-token", "probe" + "-token-0123456789")
    page.wait_for_timeout(250)
    check("粘了令牌按钮就亮", dis() is False)

    page.fill("#gh-token", "   ")
    page.wait_for_timeout(250)
    check("只填空格不算粘了", dis() is True)

    # 守卫的文案要指路（说「哪个框」），而不是只丢一句「先粘令牌」
    page.evaluate("() => { document.querySelector('#gh-token').value = ''; }")
    page.evaluate("() => verifyGh()")
    page.wait_for_timeout(250)
    guard = page.evaluate("document.querySelector('#toast').textContent")
    check("空令牌的提示会指路", "访问令牌" in guard, repr(guard))

    # 真的粘上之后，点下去必须走到发请求那一步
    page.fill("#gh-token", "probe" + "-token-0123456789")
    page.wait_for_timeout(200)
    page.evaluate("""() => {
        window.__hits = [];
        window.fetch = (u, o) => { window.__hits.push(String(u));
            return Promise.resolve({ ok:true, status:200,
                json: async () => ({ login:'probe', name:'Probe' }) }); };
    }""")
    page.click("#gh-verify")
    page.wait_for_timeout(600)
    hits = " ".join(page.evaluate("window.__hits"))
    check("粘了之后点得动且真的发请求",
          "api.github.com/user" in hits, hits[:60] or "一个请求都没发")
    check("验证成功后头部显示账号", "@probe" in app(page))
    # 把这次登录态清掉，免得影响后面几步
    page.evaluate("() => { try { localStorage.removeItem('filmtap.github') } catch(e){} }")

    # ── ⑦ 库里有东西时，?demo=1 必须拒绝覆盖 ──
    # bootDemo 里有 `if (!Object.keys(load().cameras).length)` 这道判断，
    # 所以这条本身就是「示例数据不会顺手抹掉你的机身」的回归验证。
    page.goto(BASE + "/?demo=1", wait_until="load")
    page.wait_for_timeout(700)
    h = app(page)
    check("非空库不会被示例数据覆盖",
          "Leica M6" not in h and "我的第一台" in h,
          "示例没灌进来" if "Leica M6" not in h else "被覆盖了")

    # ── ⑧ 真的清空之后再灌示例，看新排版 ──
    page.goto(BASE + "/", wait_until="load")
    page.evaluate("() => { try { localStorage.clear() } catch(e){} }")
    page.goto(BASE + "/?demo=1", wait_until="load")
    page.wait_for_timeout(700)
    h = app(page)
    check("空库时示例数据灌进来", all(s in h for s in ["Leica M6", "禄来 3.5F"]))
    check("首页机身行不带已拍张数", "/36 张" not in h and "24/36" not in h,
          "36 张" if "· 36 张" in h else "未见张数")
    check("首页机身行还有总张数", "· 36 张" in h)

    page.evaluate("location.hash = '#/c/m6'")
    page.wait_for_timeout(400)
    h = app(page)
    check("示例机身页正常", "Kodak Portra 400" in h and "135 · 36 张" in h and "换一卷" in h)

    browser.close()

print("\n" + "=" * 62)
print("通过 %d / 失败 %d" % (len(ok), len(bad)))
if bad:
    print("失败项：")
    for b in bad:
        print("  - " + b)
if errs:
    print("页面报错：")
    for e in errs[:10]:
        print("  " + e)
sys.exit(1 if (bad or errs) else 0)
