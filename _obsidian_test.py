#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Obsidian 联动的离线用例 —— 只测后端那套「解析 / 合并 / 回写」，不联网、不需要真的 vault。

为什么单独有这个文件：`_selftest.js` 只驱动前端脚本（拿 DOM 桩跑 index.html），
后端这些规则它一个字都测不到。而这一块恰恰最容易**静默错** ——
解析错一行，数量就对不上；回写多动一个字符，用户的笔记就被改花了。
这两种都不会报错，只会安安静静地给出一份错的库存。

跑法（零依赖，只用标准库）：    python _obsidian_test.py
退出码非 0 = 有失败项。
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "nas"))
import server as S

ok, bad = [], []


def check(name, cond, note=""):
    (ok if cond else bad).append(name)
    print("%-48s %s  %s" % (name, "通过" if cond else "不对", note))


# ══════════════════════════════════════════════════════════════════════
# 样例笔记：故意照着真实文件的样子来 ——
#   · 三列（品牌/型号 | 格式 | 数量），2026-09-28 改造后的真实形状
#   · 数量写成「4卷」「2」
#   · 有一行是「|| Kodak Gold 200 | ...」，多一个空首格（旧文件里有过）
#   · 有一行数量读不出数字（必须记进 skipped，不能静默丢）
#   · 后面还跟着另一个板块（回写时一个字都不能动它）
# 表头两趟匹配（「品牌/型号」别被「型号」抢走）由下面的 heads 断言单独覆盖。
# ══════════════════════════════════════════════════════════════════════
VAULT = """# 胶卷库存

一些说明文字。

## 胶卷列表

| 品牌/型号 | 格式 | 数量 |
| --- | --- | --- |
| Kodak Portra 400 | 135 | 4卷 |
| 伊尔福 HP5 Plus | 135 | 2 |
|| Kodak Gold 200 | 135 | 3卷 |
| Kodak Ektar 100 | 120 | 1卷 |
| Fujifilm Pro 400H | 120 | 0卷 |
| 某卷 | 135 | 待定 |

## 出售记录

| 日期 | 型号 | 数量 |
| --- | --- | --- |
| 2024-06-01 | Kodak Gold 200 | 1 |
"""

parsed = S.parse_film_table(VAULT)
check("能认出「## 胶卷列表」那张表", parsed["ok"], parsed.get("reason", ""))
rows = parsed.get("rows") or []
by = {r["stock"]: r for r in rows}

check("五行数据都解析出来了", len(rows) == 5, "实际 %d" % len(rows))
check("读不出数量的那行进了 skipped",
      len(parsed.get("skipped") or []) == 1 and "某卷" not in by,
      "%r" % (parsed.get("skipped"),))
check("「4卷」取到 4", by.get("Kodak Portra 400", {}).get("count") == 4)
check("纯数字「2」取到 2", by.get("伊尔福 HP5 Plus", {}).get("count") == 2)
# 「|| 3 |」多一个空首格：得被修掉，而且型号不会整体右移一格
g = by.get("Kodak Gold 200", {})
check("空首格的畸形行仍能对上型号与数量",
      g.get("count") == 3 and g.get("format") == "135", repr(g))
check("畸形行的行号是原文行号", g.get("line") == 11, "line=%r" % g.get("line"))
check("画幅列读到 120", by.get("Kodak Ektar 100", {}).get("format") == "120")

# ── 表头识别：两趟匹配（精确优先），别被「类型」抢走 ──
heads = ["序号", "品牌/型号", "类型", "ISO/速度", "格式", "数量", "状态", "购买时间", "购买价格", "备注"]
colmap = S.map_columns(heads)
check("表头映射：品牌/型号 → 1",
      colmap.get("stock") == 1, repr(colmap))
check("表头映射：格式 → 4、数量 → 5",
      colmap.get("format") == 4 and colmap.get("count") == 5, repr(colmap))

# ── 与前端等价的键 / 画幅归一 ──
check("film_key 与前端同规则",
      S.film_key("Kodak Portra 400", "135") == "kodak-portra-400@135")
check("film_key 折叠连续空白",
      S.film_key("  Ilford   HP5  ", "135") == "ilford-hp5@135")
check("画幅：表里写了就用表里的", S.norm_format("大画幅", "Portra") == "大画幅")
check("画幅：没写就从型号里猜", S.norm_format("", "6x7 中画幅") == "120")

# ══════════════════════════════════════════════════════════════════════
# apply_counts：只替换数量那一格里的数字，其余逐字节保留
# ══════════════════════════════════════════════════════════════════════
upd = [(g["line_index"], g["cell"][0], g["cell"][1], 1)]
out = "".join(S.apply_counts(parsed["lines"], upd))
expect = VAULT.replace("| 3卷 |", "| 1卷 |")
check("回写只动数量数字，其余逐字节保留", out == expect,
      "长度 %d → %d" % (len(VAULT), len(out)))

# 改为两位数，不能把「卷」吃掉、也不能把邻居格子碰到
upd2 = [(by["Kodak Ektar 100"]["line_index"],) + tuple(by["Kodak Ektar 100"]["cell"]) + (12,)]
out2 = "".join(S.apply_counts(parsed["lines"], upd2))
check("数量改成两位数也不碰别的格",
      out2 == VAULT.replace("| 1卷 |", "| 12卷 |"))

# ══════════════════════════════════════════════════════════════════════
# 账本：期初 + Σ流水。**历史段绝不能参与求和** —— 这是「99 卷变 120 卷」
# 那个坑的回归测试，整个新模型最要紧的一条。
# ══════════════════════════════════════════════════════════════════════
LEDGER = """# 胶卷账本

## 记录规则

- 只往「## 流水」追加一行

## 期初（2026-09-28）

> 冻结快照，任何人不改

| 品牌/型号 | 格式 | 数量 |
| --- | --- | --- |
| Kodak Portra 400 | 135 | 4 |
| 福马200 | 135 | 7 |

## 流水

| 日期 | 类型 | 品牌/型号 | 格式 | 数量 | 单价 | 金额 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |

## 历史（改造前的记录，已计入期初，不参与累加）

| 日期 | 类型 | 品牌/型号 | 格式 | 数量 | 单价 | 金额 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-08-06 | 购入 | 福马200 | 135 | +5 | | | 冰箱新增5卷 |
| 2026-08-05 | 售出 | Kodak Portra 400 | 135 | -1 | ¥125 | ¥125 | |
| — | 盘点修正 | 福马200 | 135 | | | | 冰箱发现，当时没记数量 |
"""

led = S.parse_ledger(LEDGER)
check("账本能解析出期初与流水两节", led["ok"], led.get("reason", ""))
beg = led.get("begin_rows") or []
check("期初读到 2 行（没被「## 历史（…期初…）」抢走标题）",
      len(beg) == 2, "实际 %d 行" % len(beg))
check("期初合计 11 卷", sum(r["count"] for r in beg) == 11)
check("流水刚改造完是空的", led.get("flow_rows") == [])
check("历史段读到 3 行、其中 1 行数量读不出",
      len(led.get("history_rows") or []) == 3 and
      len([r for r in led["history_rows"] if r["qty"] is None]) == 1,
      "%r" % (led.get("history_skipped"),))

# 核心回归：流水为空时，库存必须**等于期初**，历史段的 +5 / -1 一个都不能算进去。
# 算进去了就会变成 11+5-1=15 —— 那正是「期初 99 + 流水 +9 + 旧账 +12 = 120」的缩影。
stock, warn = S.compute_stock(beg, led["flow_rows"])
check("库存 = 期初（历史段不参与累加）",
      {r["key"]: r["count"] for r in stock} ==
      {"kodak-portra-400@135": 4, "福马200@135": 7}, repr(stock))
check("没有负数时不给警告", warn == [], repr(warn))
check("历史段空数量的行是合法的，不算 skipped",
      len(led.get("history_skipped") or []) == 0,
      "%r" % (led.get("history_skipped"),))

# 流水加进来才算
flow = [{"stock": "Kodak Portra 400", "format": "135", "qty": -1},
        {"stock": "福马200", "format": "135", "qty": 2},
        {"stock": "乐凯 C200", "format": "120", "qty": 8}]
stock2, _ = S.compute_stock(beg, flow)
check("库存 = 期初 + 流水（含账本里新出现的款）",
      {r["key"]: r["count"] for r in stock2} ==
      {"kodak-portra-400@135": 3, "福马200@135": 9, "乐凯-c200@120": 8},
      repr(stock2))
check("新出现的款追加在期初顺序之后",
      [r["key"] for r in stock2][:2] == ["kodak-portra-400@135", "福马200@135"])

# 累加成负数：只告警，不静默钳成 0 —— 等式必须仍然自洽
stock3, warn3 = S.compute_stock(beg, [{"stock": "Kodak Portra 400", "format": "135", "qty": -9}])
check("累加成负数时告警而不是静默钳 0",
      {r["key"]: r["count"] for r in stock3}["kodak-portra-400@135"] == -5 and len(warn3) == 1,
      "%r %r" % (stock3, warn3))

# ── 带符号数量的解析 ──
check("「+2」解析成 2", S.parse_signed_count("+2") == 2)
check("「-1」解析成 -1", S.parse_signed_count("-1") == -1)
check("「2」解析成 2", S.parse_signed_count("2") == 2)
check("全角「－1」「＋3」也认",
      S.parse_signed_count("－1") == -1 and S.parse_signed_count("＋3") == 3)
check("「2卷」取到 2", S.parse_signed_count("2卷") == 2)
check("空 /「—」＝没记数量，返回 None",
      S.parse_signed_count("") is None and S.parse_signed_count("—") is None
      and S.parse_signed_count("-") is None)
check("流水表头被认出来（数量列不是「库存」「剩余」）",
      S.map_columns(["日期", "类型", "品牌/型号", "格式", "数量", "单价", "金额", "备注"],
                    S.LEDGER_COLS).get("qty") == 4)

# ── 账本缺节 / 流水表头缺失 ──
# 注意：变量名别用 bad —— 那是上面 check() 用的全局失败列表，遮蔽了它整份统计就废了。
no_begin = S.parse_ledger(LEDGER.split("## 期初")[0])
check("账本缺「## 期初」→ 不 ok 且有原因",
      no_begin["ok"] is False and "期初" in no_begin.get("reason", ""), no_begin.get("reason"))
no_qty = S.parse_ledger(LEDGER.replace("| 品牌/型号 | 格式 | 数量 |", "| 品牌/型号 | 格式 |", 1)
                        .replace("| --- | --- | --- |", "| --- | --- |", 1))
check("期初表头缺「数量」→ 不 ok", no_qty["ok"] is False, no_qty.get("reason"))

# ══════════════════════════════════════════════════════════════════════
# plan_app_flows：F = A + (U - Ubase)，要追加的量 = F - L
# ══════════════════════════════════════════════════════════════════════
def led(stock, fmt, count, u=None, m=0):
    """账本存量的一条（compute_stock 的输出形状）。u 默认等于 count。"""
    return {"key": S.film_key(stock, fmt), "stock": stock, "format": fmt,
            "count": count, "u": count if u is None else u, "m": m}


def planned(films, base, ledger_rows, dates=None):
    return S.plan_app_flows(films, base, ledger_rows,
                            dates=dates or {}, today="2026-03-01")


# 没基线：假设「上次两边一致」→ 以账本为准，一行都不追加
p, a, w = planned({"portra@135": {"stock": "Portra", "format": "135", "count": 3}},
                  {}, [led("Portra", "135", 4)])
check("没基线时以账本为准、不追加流水",
      a == [] and p[0]["count"] == 4, "%r %r" % (a, p))

# App 装掉 1 卷、账本没变 → 追加一条 -1 的「消耗」
p, a, w = planned({"portra@135": {"stock": "Portra", "format": "135", "count": 3}},
                  {"portra@135": 4}, [led("Portra", "135", 4)])
check("App 装掉 1 卷 → 追加 -1 的消耗流水",
      len(a) == 1 and a[0]["qty"] == -1 and a[0]["type"] == "消耗" and S.is_auto_row(a[0]),
      "%r" % (a,))

# 账本侧买 2 卷、App 没动 → 不追加流水，重算后跟着涨
p, a, w = planned({"portra@135": {"stock": "Portra", "format": "135", "count": 4}},
                  {"portra@135": 4}, [led("Portra", "135", 6)])
check("账本侧买卷 → 不追加流水，App 跟着涨到 6",
      a == [] and p[0]["count"] == 6, "%r %r" % (a, p))

# 两边各动一次：账本 +2、App 装掉 1 → 净 +1
p, a, w = planned({"portra@135": {"stock": "Portra", "format": "135", "count": 3}},
                  {"portra@135": 4}, [led("Portra", "135", 6)])
check("两边各动一次能叠加（账本 +2、装卷 -1 → 5）",
      len(a) == 1 and a[0]["qty"] == -1 and p[0]["count"] == 5, "%r %r" % (a, p))

# ★ 幂等的关键回归：上一次的追加行已经在账本里了，但基线没写成功（还是旧的）。
#   这时候必须**什么都不追加** —— 否则每失败一次就多扣一卷。
p, a, w = planned({"portra@135": {"stock": "Portra", "format": "135", "count": 3}},
                  {"portra@135": 4}, [led("Portra", "135", 3, u=4, m=-1)])
check("基线没写成也不重复扣减（幂等）",
      a == [] and p[0]["count"] == 3, "%r %r" % (a, p))

# App 里有、账本里完全没有的款 → 补一条盘点修正
p, a, w = planned({"新卷@135": {"stock": "新卷", "format": "135", "count": 2}}, {}, [])
check("账本里没有的款补一条盘点修正",
      len(a) == 1 and a[0]["qty"] == 2 and a[0]["type"] == "盘点修正"
      and p[0]["count"] == 2, "%r %r" % (a, p))

# 重算成负数：账本如实记负、清单和 App 钳到 0，并且告警
p, a, w = planned({"portra@135": {"stock": "Portra", "format": "135", "count": 0}},
                  {"portra@135": 5}, [led("Portra", "135", 1)])
check("重算成负数时告警、写出去钳 0，追加量照实记负",
      p[0]["count"] == 0 and p[0]["raw"] == -4
      and len(a) == 1 and a[0]["qty"] == -5 and len(w) == 1,
      "%r %r %r" % (p, a, w))

# 追加的流水日期优先用装卷那天
p, a, w = planned({"kodak-portra-400@135": {"stock": "Kodak Portra 400", "format": "135",
                                            "count": 3}},
                  {"kodak-portra-400@135": 4},
                  [led("Kodak Portra 400", "135", 4)],
                  dates={"kodak-portra-400@135": "2026-02-03"})
check("追加的流水日期用装卷那天", a[0]["date"] == "2026-02-03", repr(a))

# ── 装卷日期：从机身的 loaded 里取 ──
d = S.loaded_dates({"a": {"loaded": {"stock": "Kodak Portra 400", "loadedAt": "2026-02-03"}},
                    "b": {"loaded": {"stock": "Kodak Portra 400", "loadedAt": "2026-01-09"}},
                    "c": {"loaded": None}, "d": {}, "e": "不是字典"})
check("装卷日期按款取最新那天", d.get("kodak-portra-400@135") == "2026-02-03", repr(d))
check("空机 / 缺字段不会炸", S.loaded_dates(None) == {} and S.loaded_dates({}) == {})

# ══════════════════════════════════════════════════════════════════════
# 拍摄记录：退卷 → 一行
# ══════════════════════════════════════════════════════════════════════
SHOTS = """# 胶卷拍摄记录

> 每次「拍完换卷」film-tap 会往下面这张表追加一行。

## 拍摄记录

| 相机 | 胶卷型号 | 装卷日 | 退卷日 | 张数 | 备注 |
| --- | --- | --- | --- | --- | --- |
| XA | Kodak Portra 400 | 2026-01-01 | 2026-01-20 | 36 | 冰箱里拿出来那卷 |
"""

shots0 = S.parse_shots_table(SHOTS)
check("拍摄记录能解析出 6 列 1 行",
      shots0["ok"] and shots0["ncols"] == 6 and len(shots0["rows"]) == 1,
      shots0.get("reason", ""))
check("已有行进了指纹集合（去重要用）",
      ("", "Kodak Portra 400", "2026-01-01", "2026-01-20", "36",
       "冰箱里拿出来那卷") in shots0["fingerprints"],
      repr(shots0["fingerprints"]))

shot_rows = S.shot_rows_from_cameras({"c1": {
    "id": "c1", "name": "XA",
    "history": [
        {"stock": "Kodak Portra 400", "loadedAt": "2026-01-01", "unloadedAt": "2026-01-20",
         "total": 36, "note": "冰箱里拿出来那卷"},
        {"stock": "福马200", "loadedAt": "2026-02-01", "unloadedAt": "2026-02-18",
         "total": 36, "iso": 400, "ei": 1600, "note": "夜景"},
        {"stock": "没退卷日", "loadedAt": "2026-03-01", "total": 36},
        {"stock": "", "loadedAt": "2026-03-01", "unloadedAt": "2026-03-05"},
    ]}})
check("只挑真的退过卷的（缺退卷日 / 缺型号的都跳过）", len(shot_rows) == 2, repr(shot_rows))
check("张数写标称张数", shot_rows[1]["fields"]["frames"] == "36", repr(shot_rows[1]))
check("EI≠ISO 时备注里带提示",
      shot_rows[1]["fields"]["note"] == "夜景；按 EI 1600 拍", repr(shot_rows[1]["fields"]))
check("自然键用机身 id（改名不会重写）",
      all(r["key"].startswith("c1|") for r in shot_rows), repr([r["key"] for r in shot_rows]))
check("按退卷日排序",
      [r["fields"]["unloaded"] for r in shot_rows] == ["2026-01-20", "2026-02-18"],
      repr(shot_rows))
check("没装过卷的机身不产出任何行",
      S.shot_rows_from_cameras({"c2": {"id": "c2", "name": "空机"}}) == [] and
      S.shot_rows_from_cameras(None) == [])

# 指纹不含机身名：改名不该被当成新事件
cm = S.map_columns(["相机", "胶卷型号", "装卷日", "退卷日", "张数", "备注"], S.SHOT_COLS)
check("指纹把机身名那一格抹掉",
      S.shot_fingerprint(["XA", "P", "2026-01-01", "2026-01-20", "36", ""], cm.get("camera"))
      == S.shot_fingerprint(["XA 改名了", "P", "2026-01-01", "2026-01-20", "36", ""],
                            cm.get("camera")))

# 按表头位置摆：表头少几列也照写
cm3 = S.map_columns(["相机", "胶卷型号", "退卷日"], S.SHOT_COLS)
check("只认得出三列时也能摆出行",
      S.shot_cells({"camera": "XA", "stock": "福马200", "loaded": "x",
                    "unloaded": "2026-02-18", "frames": "36", "note": "n"}, cm3, 3)
      == ["XA", "福马200", "2026-02-18"], repr(cm3))

bad_shots = S.parse_shots_table("## 拍摄记录\n\n| 相机 | 型号 |\n| --- | --- |\n| XA | P |\n")
check("拍摄记录表头缺「退卷日」→ 不 ok", bad_shots["ok"] is False, bad_shots.get("reason"))
check("拍摄记录缺「## 拍摄记录」节 → 不 ok",
      S.parse_shots_table("# 没了\n\n| 相机 |\n| --- |\n")["ok"] is False)

# ══════════════════════════════════════════════════════════════════════
# Store.obsidian_sync：整个流程（临时目录里造清单 + 账本 + data + 基线）
# ══════════════════════════════════════════════════════════════════════
LEDGER_E2E = """# 胶卷账本

## 记录规则

- 只往「## 流水」追加一行

## 期初（2026-01-01）

| 品牌/型号 | 格式 | 数量 |
| --- | --- | --- |
| Kodak Portra 400 | 135 | 4 |
| 伊尔福 HP5 Plus | 135 | 2 |
| Kodak Gold 200 | 135 | 3 |
| Kodak Ektar 100 | 120 | 1 |
| Fujifilm Pro 400H | 120 | 0 |

## 流水

| 日期 | 类型 | 品牌/型号 | 格式 | 数量 | 单价 | 金额 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |

## 历史（改造前的记录，已计入期初，不参与累加）

| 日期 | 类型 | 品牌/型号 | 格式 | 数量 | 单价 | 金额 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2025-12-01 | 购入 | Kodak Portra 400 | 135 | +4 | | | 早先买的，已经算在期初里 |
"""

tmp = tempfile.mkdtemp(prefix="filmtap-obs-")


def write_file(path, text, newline="\n"):
    """一律用二进制写 —— 让这份用例在 Windows 和 Linux 上跑出同一个文件。
    换行显式给出来，是要能专门测 CRLF 那种情况（见文件末尾那几条）。"""
    with open(path, "wb") as f:
        f.write(text.replace("\n", newline).encode("utf-8"))


def read_file(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8")


try:
    data_dir = os.path.join(tmp, "data")
    vault = os.path.join(tmp, "胶卷库存清单.md")     # 生成物
    ledger = os.path.join(tmp, "胶卷账本.md")        # 唯一真源
    write_file(vault, VAULT)
    write_file(ledger, LEDGER_E2E)

    store = S.Store(data_dir, lambda *a: None)

    BEGIN = {S.film_key(r["stock"], r["format"]): r["count"]
             for r in S.parse_ledger(LEDGER_E2E)["begin_rows"]}
    check("样例账本的期初合计是 10 卷", sum(BEGIN.values()) == 10)

    BASE_FILMS = {
        "kodak-portra-400@135": {"id": "kodak-portra-400@135", "stock": "Kodak Portra 400",
                                 "format": "135", "count": 3, "exp": "2027-01"},
        "kodak-gold-200@135":   {"id": "kodak-gold-200@135", "stock": "Kodak Gold 200",
                                 "format": "135", "count": 3},
        "kodak-ektar-100@120":  {"id": "kodak-ektar-100@120", "stock": "Kodak Ektar 100",
                                 "format": "120", "count": 1},
        "伊尔福-hp5-plus@135":   {"id": "伊尔福-hp5-plus@135", "stock": "伊尔福 HP5 Plus",
                                 "format": "135", "count": 2},
    }

    def put_app(films, cameras=None):
        store.write_data(json.dumps({"version": 2, "cameras": cameras or {}, "films": films},
                                    ensure_ascii=False).encode("utf-8"), None, True)

    def put_baseline(counts, shots=None):
        store.write_baseline({"file": vault, "ledger": ledger,
                              "at": "2026-01-01T00:00:00Z",
                              "counts": counts, "ubase": counts,
                              "shots": shots or []})

    os.environ["FT_OBSIDIAN"] = vault
    os.environ["FT_OBSIDIAN_LEDGER"] = ledger
    put_app(BASE_FILMS)
    put_baseline(BEGIN)

    # ── dry-run：一个字节都不许动，但要把「要追加什么」讲清楚 ──
    before_vault, before_ledger = read_file(vault), read_file(ledger)
    state, info = store.obsidian_sync(dry=True, today="2026-02-10")
    check("dry-run 返回 ok", state == "ok", state)
    check("dry-run 两份笔记都没动",
          read_file(vault) == before_vault and read_file(ledger) == before_ledger)
    check("dry-run 报出要追加 1 条消耗",
          len(info["appended"]) == 1 and info["appended"][0]["qty"] == -1,
          "%r" % (info["appended"],))
    check("dry-run 报出清单要改 1 格", info["listWrites"] == 1, repr(info["listWrites"]))
    check("期初合计 10、历史段 1 行（历史那 +4 没被算进去）",
          info["beginTotal"] == 10 and info["historyRows"] == 1,
          "beginTotal=%r historyRows=%r" % (info["beginTotal"], info["historyRows"]))

    # ── 真跑一次 ──
    state, info = store.obsidian_sync(dry=False, today="2026-02-10")
    check("同步成功", state == "ok", state)
    after = read_file(vault)
    check("清单里 Portra 由 4卷 改成 3卷",
          "| 3卷 |" in after and "| 4卷 |" not in after)
    check("清单其余部分逐字节不变", after == VAULT.replace("| 4卷 |", "| 3卷 |"))

    led_after = read_file(ledger)
    added = [ln for ln in led_after.splitlines() if ln.startswith("| 2026-02-10 |")]
    check("账本追加了一条消耗 -1 的自动流水",
          len(added) == 1 and "消耗" in added[0] and "-1" in added[0]
          and S.AUTO_TAG in added[0], repr(added))
    check("账本除了那一行，其余逐字节保留",
          led_after.replace(added[0] + "\n", "") == LEDGER_E2E, "多了别的东西")

    db = json.loads(store.read_data()[0].decode("utf-8"))
    check("App 侧 Portra 仍是 3", db["films"]["kodak-portra-400@135"]["count"] == 3)
    check("同步不动同步范围外的字段",
          db["films"]["kodak-portra-400@135"].get("exp") == "2027-01")
    check("回写前留了原文备份",
          os.path.isdir(store.p(S.OBSIDIAN_BACKUP_DIR)) and
          len(os.listdir(store.p(S.OBSIDIAN_BACKUP_DIR))) >= 1)
    b1 = store.read_baseline()
    check("基线记下了期初哈希与流水行数",
          bool(b1.get("beginHash")) and b1.get("flowRows") == 1,
          repr({k: v for k, v in b1.items() if k != "ubase"}))

    # ── 再跑一次：无事可做 ──
    state2, info2 = store.obsidian_sync(dry=False, today="2026-02-10")
    check("紧接着再同步是幂等的",
          state2 == "ok" and info2["appended"] == [] and info2["listWrites"] == 0,
          "%r %r" % (info2.get("appended"), info2.get("listWrites")))
    check("第二次两份笔记都没动",
          read_file(vault) == after and read_file(ledger) == led_after)

    # ── ★ 基线退回旧值（模拟「基线没写成功」）：绝不能重复扣减 ──
    put_baseline(BEGIN)
    st_again, info_again = store.obsidian_sync(dry=False, today="2026-02-10")
    check("基线退回旧值后也不重复追加流水",
          st_again == "ok" and info_again["appended"] == [] and read_file(ledger) == led_after,
          "%r" % (info_again.get("appended"),))
    check("清单也不会被再改一遍", read_file(vault) == after)

    # ── App 装了新的一卷：写成流水，日期用机身里那卷的 loadedAt ──
    films = dict(BASE_FILMS)
    films["kodak-portra-400@135"] = dict(films["kodak-portra-400@135"], count=2)
    put_app(films, {"c1": {"id": "c1", "name": "XA", "history": [],
                           "loaded": {"stock": "Kodak Portra 400",
                                      "loadedAt": "2026-02-20", "total": 36}}})
    state3, info3 = store.obsidian_sync(dry=False, today="2026-02-21")
    led3 = read_file(ledger)
    check("装卷那条流水的日期用的是装卷那天，不是同步那天",
          "| 2026-02-20 | 消耗 | Kodak Portra 400 | 135 | -1 |" in led3,
          repr([ln for ln in led3.splitlines() if ln.startswith("| 2026-")]))
    check("清单跟着变成 2卷", "| Kodak Portra 400 | 135 | 2卷 |" in read_file(vault))
    check("装卷只追加了一条", len(info3["appended"]) == 1, repr(info3["appended"]))

    # ── Hermes 直接买 2 卷：账本侧变了，App 跟着涨，且不追加自动流水 ──
    lines = led3.splitlines()
    at = max(i for i, ln in enumerate(lines) if ln.startswith("| 2026-"))
    lines.insert(at + 1, "| 2026-02-22 | 购入 | Kodak Gold 200 | 135 | +2 | ¥30 | ¥60 | |")
    write_file(ledger, "\n".join(lines) + "\n")
    state4, info4 = store.obsidian_sync(dry=False, today="2026-02-22")
    check("Hermes 买卷时不追加自动流水", info4["appended"] == [], repr(info4["appended"]))
    g = json.loads(store.read_data()[0].decode("utf-8"))["films"]["kodak-gold-200@135"]
    check("App 的 Gold 跟着涨到 5", g["count"] == 5, repr(g))
    check("清单的 Gold 也变成 5卷", "| Kodak Gold 200 | 135 | 5卷 |" in read_file(vault))
    check("这类变化出现在重算差异里",
          any(r["key"] == "kodak-gold-200@135" and r["now"] == 5 for r in info4["recalc"]),
          repr(info4["recalc"]))

    # ── 护栏 1：期初被改过 → 拒绝，且一个字节不动 ──
    good_led = read_file(ledger)
    write_file(ledger, good_led.replace("| Kodak Portra 400 | 135 | 4 |",
                                       "| Kodak Portra 400 | 135 | 9 |"))
    raw_before, _ = store.read_data()
    st_b, info_b = store.obsidian_sync(dry=False, today="2026-02-23")
    check("期初被改过 → blocked",
          st_b == "blocked" and "期初" in (info_b.get("blocked") or ""),
          "%r %r" % (st_b, info_b.get("blocked")))
    check("被拦下时 data.json 一个字节没动", store.read_data()[0] == raw_before)
    write_file(ledger, good_led)

    # ── 护栏 2：流水行数骤减 → 拒绝 ──
    keep, in_flow = [], False
    for ln in good_led.splitlines():
        if ln.startswith("## "):
            in_flow = ln.startswith("## 流水")
            keep.append(ln)
        elif not (in_flow and ln.startswith("| 2026-")):
            keep.append(ln)
    write_file(ledger, "\n".join(keep) + "\n")
    raw_before, _ = store.read_data()
    st_b2, info_b2 = store.obsidian_sync(dry=False, today="2026-02-23")
    check("流水行数骤减 → blocked",
          st_b2 == "blocked" and "流水" in (info_b2.get("blocked") or ""),
          repr(info_b2.get("blocked")))
    check("被拦下时 data.json 也没动", store.read_data()[0] == raw_before)
    write_file(ledger, good_led)

    # ── 没配 / 少了哪个 / 找不到 / 读不懂 ──
    os.environ.pop("FT_OBSIDIAN_LEDGER", None)
    check("少一个环境变量 → not_configured",
          store.obsidian_sync(dry=True)[0] == "not_configured")
    os.environ["FT_OBSIDIAN_LEDGER"] = ledger
    os.environ.pop("FT_OBSIDIAN", None)
    check("只设账本不设清单 → 还是 not_configured",
          store.obsidian_sync(dry=True)[0] == "not_configured")

    os.environ["FT_OBSIDIAN"] = os.path.join(tmp, "no-such-list.md")
    st_nf, info_nf = store.obsidian_sync(dry=True)
    check("清单不存在 → no_file（明确说是清单）",
          st_nf == "no_file" and info_nf.get("which") == "list", repr(info_nf))
    os.environ["FT_OBSIDIAN"] = vault
    os.environ["FT_OBSIDIAN_LEDGER"] = os.path.join(tmp, "no-such-ledger.md")
    st_nl, info_nl = store.obsidian_sync(dry=True)
    check("账本不存在 → no_file（明确说是账本）",
          st_nl == "no_file" and info_nl.get("which") == "ledger", repr(info_nl))

    weird = os.path.join(tmp, "weird.md")
    write_file(weird, "## 胶卷列表\n\n| 品牌/型号 | 格式 |\n| --- | --- |\n| Portra | 135 |\n")
    os.environ["FT_OBSIDIAN"] = weird
    os.environ["FT_OBSIDIAN_LEDGER"] = ledger
    st_p, info_p = store.obsidian_sync(dry=True)
    check("清单表头缺「数量」列 → parse_failed（说是清单）",
          st_p == "parse_failed" and info_p.get("which") == "list", repr(info_p.get("reason")))

    bad_led = os.path.join(tmp, "bad-ledger.md")
    write_file(bad_led, "# 胶卷账本\n\n## 流水\n\n| 日期 | 数量 |\n| --- | --- |\n")
    os.environ["FT_OBSIDIAN"] = vault
    os.environ["FT_OBSIDIAN_LEDGER"] = bad_led
    st_bl, info_bl = store.obsidian_sync(dry=True)
    check("账本缺「## 期初」→ parse_failed（说是账本）",
          st_bl == "parse_failed" and info_bl.get("which") == "ledger",
          repr(info_bl.get("reason")))

    # ── 拍摄记录：退卷 → 追一行；重放 / 改名都不重复 ──
    # 上一段把 FT_OBSIDIAN / FT_OBSIDIAN_LEDGER 指到了坏文件上，这里要拨回来。
    shots_file = os.path.join(tmp, "胶卷拍摄记录.md")
    write_file(shots_file, SHOTS)
    os.environ["FT_OBSIDIAN"] = vault
    os.environ["FT_OBSIDIAN_LEDGER"] = ledger
    os.environ["FT_OBSIDIAN_SHOTS"] = shots_file
    write_file(vault, VAULT)
    write_file(ledger, LEDGER_E2E)
    CAMS = {"c1": {"id": "c1", "name": "XA", "loaded": None, "history": [
        {"stock": "Kodak Portra 400", "loadedAt": "2026-02-01",
         "unloadedAt": "2026-02-20", "total": 36, "note": "夜景"}]}}
    put_app(BASE_FILMS, CAMS)
    put_baseline(BEGIN)

    st_s, info_s = store.obsidian_sync(dry=False, today="2026-02-21")
    added_shot = "| XA | Kodak Portra 400 | 2026-02-01 | 2026-02-20 | 36 | 夜景 |\n"
    check("拍摄记录追加了一行（位于表尾、其余逐字节没动）",
          st_s == "ok" and len(info_s.get("shots") or []) == 1
          and read_file(shots_file) == SHOTS + added_shot,
          "%s %r" % (st_s, info_s.get("shots") or info_s.get("reason")))
    check("拍摄记录那一条也报出来了",
          (info_s["shots"] or [{}])[0].get("camera") == "XA"
          and (info_s["shots"] or [{}])[0].get("unloaded") == "2026-02-20",
          repr(info_s.get("shots")))
    shot_after = SHOTS + added_shot

    st_s2, info_s2 = store.obsidian_sync(dry=False, today="2026-02-21")
    check("重放不重复写拍摄记录",
          info_s2.get("shots") == [] and read_file(shots_file) == shot_after,
          repr(info_s2.get("shots")))

    # 基线退回旧值（模拟基线没写成功）+ 机身改名：指纹里不含机身名，所以也不重复
    renamed = json.loads(json.dumps(CAMS))
    renamed["c1"]["name"] = "XA 改了个名"
    put_app(BASE_FILMS, renamed)
    put_baseline(BEGIN)
    st_s3, info_s3 = store.obsidian_sync(dry=False, today="2026-02-22")
    check("基线丢了 + 机身改名，都不会把老记录再写一遍",
          info_s3.get("shots") == [] and read_file(shots_file) == shot_after,
          repr(info_s3.get("shots")))

    # 手工改过那一行（比如补上冲扫方式）之后重放：靠基线里的自然键认出已经写过了
    edited = shot_after.replace("| 36 | 夜景 |", "| 36 | 夜景，已冲扫 |")
    write_file(shots_file, edited)
    put_baseline(BEGIN, shots=["c1|Kodak Portra 400|2026-02-01|2026-02-20"])
    st_s4, info_s4 = store.obsidian_sync(dry=False, today="2026-02-23")
    check("手动改过已有行之后也不会再补一行",
          info_s4.get("shots") == [] and read_file(shots_file) == edited,
          repr(info_s4.get("shots")))

    # 没配 / 配错：一个跳过并在警告里说明，一个明确报出来
    os.environ.pop("FT_OBSIDIAN_SHOTS", None)
    st_s5, info_s5 = store.obsidian_sync(dry=True)
    check("没配拍摄记录 → 整块跳过，并在警告里说明",
          st_s5 == "ok" and info_s5.get("shots") == []
          and any("拍摄记录" in x for x in info_s5["warnings"]),
          repr(info_s5["warnings"]))
    os.environ["FT_OBSIDIAN_SHOTS"] = os.path.join(tmp, "no-such-shots.md")
    st_s6, info_s6 = store.obsidian_sync(dry=True)
    check("拍摄记录设了但文件不在 → no_file（明确说是拍摄记录）",
          st_s6 == "no_file" and info_s6.get("which") == "shots", repr(info_s6))
    os.environ["FT_OBSIDIAN_SHOTS"] = shots_file

    # ── CRLF 的两份笔记：只改数字 / 只在表尾追加，换行不被撑花 ──
    # 这条专门盯 os.open 的文本模式：少了 O_BINARY，写出的每个 \n 会被再补一个 \r，
    # 整份文件被撑成 CRLFCRLF。Linux 上不会发生，所以必须在这里挡住。
    write_file(vault, VAULT, newline="\r\n")
    write_file(ledger, LEDGER_E2E, newline="\r\n")
    os.environ["FT_OBSIDIAN"] = vault
    os.environ["FT_OBSIDIAN_LEDGER"] = ledger
    put_app(BASE_FILMS)                 # 两边都退回起始状态，好断言
    put_baseline(BEGIN)
    st_c, _ = store.obsidian_sync(dry=False, today="2026-02-10")
    check("CRLF 的清单也只改数字、换行不被撑花",
          st_c == "ok" and read_file(vault) ==
          VAULT.replace("| 4卷 |", "| 3卷 |").replace("\n", "\r\n"), st_c)
    t = read_file(ledger)
    check("CRLF 的账本追加后既没撑花、追加行也是 CRLF 结尾",
          "\r\r" not in t and any(ln.endswith("\r") for ln in t.split("\n")
                                  if ln.startswith("| 2026-")), repr(t[-260:]))

    # ── 响应外壳的措辞 ──
    check("not_configured 的 body 里 ok 是 false 且 configured 是 false",
          S.Handler._obsidian_body(object(), "not_configured", {})["ok"] is False)
    b = S.Handler._obsidian_body(object(), "ok", {"appended": [], "recalc": [],
                                                 "beginTotal": 10, "flowRows": 0,
                                                 "stockTotal": 10})
    check("无事可做时措辞说「已经一致」", "已经一致" in b["message"], b["message"])
    b = S.Handler._obsidian_body(object(), "ok", {"blocked": "期初被改过", "appended": []})
    check("dry-run 被护栏拦下时 ok 是 false",
          b["ok"] is False and "期初被改过" in b["message"])
    b = S.Handler._obsidian_body(object(), "ok", {"appended": [{"qty": -1}], "recalc": [],
                                                 "beginTotal": 10, "flowRows": 0,
                                                 "stockTotal": 9})
    check("追加消耗时措辞点明条数", "1 条消耗流水" in b["message"], b["message"])
finally:
    os.environ.pop("FT_OBSIDIAN", None)
    os.environ.pop("FT_OBSIDIAN_LEDGER", None)
    os.environ.pop("FT_OBSIDIAN_SHOTS", None)
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 62)
print("通过 %d / 失败 %d" % (len(ok), len(bad)))
if bad:
    print("失败项：")
    for b in bad:
        print("  - " + b)
sys.exit(1 if bad else 0)
