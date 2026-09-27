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
#   · 表头是「品牌/型号」，和另一列「类型」挨着（两趟匹配必须认得出）
#   · 数量写成「4卷」「2」
#   · 有一行是「|| 3 | ...」，多一个空首格（真实文件里就有）
#   · 有一行数量读不出数字（必须记进 skipped，不能静默丢）
#   · 后面还跟着另一个板块（回写时一个字都不能动它）
# ══════════════════════════════════════════════════════════════════════
VAULT = """# 胶卷库存

一些说明文字。

## 胶卷列表

| 序号 | 品牌/型号 | 类型 | ISO/速度 | 格式 | 数量 | 状态 | 购买时间 | 购买价格 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Kodak Portra 400 | 彩色负片 | 400 | 135 | 4卷 | 未拆 | 2024-01-01 | 60 | |
| 2 | 伊尔福 HP5 Plus | 黑白负片 | 400 | 135 | 2 | 已拆 | 2024-02-01 | 45 | |
|| 3 | Kodak Gold 200 | 彩色负片 | 200 | 135 | 3卷 | 未拆 | 2024-03-01 | 30 | |
| 4 | Kodak Ektar 100 | 彩色负片 | 100 | 120 | 1卷 | 未拆 | 2024-04-01 | 55 | 中画幅 |
| 5 | Fujifilm Pro 400H | 彩色负片 | 400 | 120 | 0卷 | 用完 | 2024-05-01 | 70 | |
| 6 | 某卷 | 彩色负片 | 100 | 135 | 待定 | 未拆 | | | |

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
# merge_films：带基线的增量合并
# ══════════════════════════════════════════════════════════════════════
def row(stock, fmt, count, line=9):
    return {"stock": stock, "format": fmt, "count": count, "line": line,
            "line_index": line - 1, "cell": (0, 0), "raw_count": str(count)}


def only(rows_, films, base):
    ch, un, wn = S.merge_films(rows_, films, base)
    return ch[0]


# 基线 4：App 也 4、笔记改了 5（买 1 卷）→ 5
c = only([row("Portra", "135", 5)], {"portra@135": {"count": 4}}, {"portra@135": 4})
check("笔记 +1、App 没动 → 5", c["new"] == 5 and c["action"] == "change", repr(c["new"]))

# 基线 4：App 拍到 3、笔记也买了 1 变 5 → 4（一加一减，互不冲突）
c = only([row("Portra", "135", 5)], {"portra@135": {"count": 3}}, {"portra@135": 4})
check("两边各动一次能抵掉 → 4", c["new"] == 4 and c["basis"] == "merge", repr(c["new"]))

# 基线 4：App 拍到 3、笔记还是 4 → 3（笔记侧没变，就以 App 为准）★关键用例
c = only([row("Portra", "135", 4)], {"portra@135": {"count": 3}}, {"portra@135": 4})
check("App 扣减、笔记没改 → 3 且 action 仍是 same",
      c["new"] == 3 and c["action"] == "same", repr(c))

# 没有基线：一律以笔记为准，并标出来
c = only([row("Portra", "135", 7)], {}, {})
check("首次同步以笔记为准", c["new"] == 7 and c["basis"] == "obsidian" and c["action"] == "new")

# 合并成负数：按 0 计，并且留下警告。
# 基线 2、笔记 1（少 1）、App 已经是 0（少 2）→ 合起来 -1 → 记成 0，多扣 1 卷。
c = only([row("Portra", "135", 1)], {"portra@135": {"count": 0}}, {"portra@135": 2})
check("合并成负数按 0 计并给警告",
      c["new"] == 0 and c["overflow"] == 1, "new=%r overflow=%r" % (c["new"], c["overflow"]))

# ══════════════════════════════════════════════════════════════════════
# Store.obsidian_sync：整个流程（临时目录里造 vault + data + 基线）
# ══════════════════════════════════════════════════════════════════════
tmp = tempfile.mkdtemp(prefix="filmtap-obs-")


def write_vault(text, newline="\n"):
    """一律用二进制写 —— 让这份用例在 Windows 和 Linux 上跑出同一个文件。
    换行显式给出来，是要能专门测 CRLF 那种情况（见文件末尾那一条）。"""
    with open(vault, "wb") as f:
        f.write(text.replace("\n", newline).encode("utf-8"))


def read_vault():
    with open(vault, "rb") as f:
        return f.read().decode("utf-8")


try:
    data_dir = os.path.join(tmp, "data")
    vault = os.path.join(tmp, "胶卷库存清单.md")
    write_vault(VAULT)

    store = S.Store(data_dir, lambda *a: None)

    # App 侧：Portra 拍到 3（基线 4，笔记没改）、Gold 3、Ektar 1
    app_db = {"version": 2, "cameras": {},
              "films": {
                  "kodak-portra-400@135": {"id": "kodak-portra-400@135", "stock": "Kodak Portra 400",
                                           "format": "135", "count": 3, "exp": "2027-01"},
                  "kodak-gold-200@135":   {"id": "kodak-gold-200@135", "stock": "Kodak Gold 200",
                                           "format": "135", "count": 3},
                  "kodak-ektar-100@120":  {"id": "kodak-ektar-100@120", "stock": "Kodak Ektar 100",
                                           "format": "120", "count": 1},
              }}
    store.write_data(json.dumps(app_db, ensure_ascii=False).encode("utf-8"), None, True)
    # 基线：就是「上次两边一致时，每一行是多少卷」。
    # 键直接用 film_key 算，跟运行时同一套规则，不会两边对不上。
    store.write_baseline({"file": vault, "at": "2026-01-01T00:00:00Z", "rows": len(rows),
                          "counts": {S.film_key(r["stock"], r["format"]): r["count"]
                                     for r in rows}})

    os.environ["FT_OBSIDIAN"] = vault

    # ── dry-run 不许改任何东西 ──
    before_vault = read_vault()
    state, info = store.obsidian_sync(dry=True)
    check("dry-run 返回 ok", state == "ok", state)
    check("dry-run 不改笔记", read_vault() == before_vault)
    body = S.Handler._obsidian_body(object(), state, info)
    check("dry-run 预览里有「回写笔记」那一类",
          any(c["action"] == "same" and c["new"] != c["obs"] for c in info["changes"]),
          body["message"])

    # ── 真跑一次 ──
    state, info = store.obsidian_sync(dry=False)
    check("同步成功", state == "ok", state)
    after = read_vault()
    check("笔记里 Portra 由 4卷 改成 3卷",
          "| 3卷 |" in after and "| 4卷 |" not in after)
    check("笔记的其余部分逐字节不变",
          after == VAULT.replace("| 4卷 |", "| 3卷 |"))
    check("回写前留了原文备份",
          os.path.isdir(store.p(S.OBSIDIAN_BACKUP_DIR)) and
          len(os.listdir(store.p(S.OBSIDIAN_BACKUP_DIR))) == 1)

    raw, _ = store.read_data()
    db = json.loads(raw.decode("utf-8"))
    check("App 侧 Portra 仍是 3", db["films"]["kodak-portra-400@135"]["count"] == 3)
    hp5 = [v for v in db["films"].values() if "HP5" in v.get("stock", "")]
    check("笔记里新增的 HP5 进了 App", len(hp5) == 1 and hp5[0]["count"] == 2,
          repr(hp5))
    check("同步不动同步范围外的字段", db["films"]["kodak-portra-400@135"].get("exp") == "2027-01")
    check("基线已更新", store.read_baseline().get("at"))

    # ── 再跑一次应该是「无事可做」（幂等）──
    state2, info2 = store.obsidian_sync(dry=False)
    check("紧接着再同步是幂等的（没有要改的）",
          state2 == "ok" and not info2["changes"], "%r" % (info2.get("changes"),))
    check("第二次没再写笔记", read_vault() == after)

    # ── CRLF 的笔记（Windows 上编辑过的很可能就是这种）也要逐字节保留 ──
    # 这一条专门盯 os.open 的文本模式：少了 O_BINARY，写出的每个 \n 会被再补一个 \r，
    # 整份文件被撑成 CRLFCRLF。Linux 上不会发生，所以必须在这里挡住。
    write_vault(VAULT, newline="\r\n")
    store.write_baseline({"file": vault, "at": "2026-01-02T00:00:00Z", "rows": len(rows),
                          "counts": {S.film_key(r["stock"], r["format"]): r["count"]
                                     for r in rows}})
    st4, _ = store.obsidian_sync(dry=False)
    check("CRLF 的笔记也只改数字、换行不被撑花",
          st4 == "ok" and read_vault() == VAULT.replace("| 4卷 |", "| 3卷 |").replace("\n", "\r\n"),
          st4)

    # ── 护栏：行数骤减必须拦下，且不碰任何数据 ──
    # 基线记着 5 行，这里只留 1 行 —— 典型的「文件被写坏了」的样子。
    one_row = VAULT.split("| 2 |")[0] + "\n"
    write_vault(one_row)
    raw_before, _ = store.read_data()
    state3, info3 = store.obsidian_sync(dry=False)
    raw_after, _ = store.read_data()
    check("行数骤减被护栏拦下", state3 == "blocked" and info3.get("blocked"), state3)
    check("被拦下时 App 数据一个字节没动", raw_after == raw_before)

    # ── 没配 / 找不到 / 读不懂 ──
    os.environ.pop("FT_OBSIDIAN", None)
    check("没配 FT_OBSIDIAN → not_configured",
          store.obsidian_sync(dry=True)[0] == "not_configured")
    os.environ["FT_OBSIDIAN"] = os.path.join(tmp, "no-such-file.md")
    check("文件不存在 → no_file", store.obsidian_sync(dry=True)[0] == "no_file")
    weird = os.path.join(tmp, "weird.md")
    with open(weird, "wb") as f:
        f.write("## 胶卷列表\n\n| 序号 | 品牌/型号 | 格式 |\n| --- | --- | --- |\n| 1 | Portra | 135 |\n".encode("utf-8"))
    os.environ["FT_OBSIDIAN"] = weird
    st, inf = store.obsidian_sync(dry=True)
    check("表头缺「数量」列 → parse_failed", st == "parse_failed", inf.get("reason", ""))

    # ── 响应外壳的措辞 ──
    check("not_configured 的 body 里 ok 是 false 且 configured 是 false",
          S.Handler._obsidian_body(object(), "not_configured", {})["ok"] is False)
    b = S.Handler._obsidian_body(object(), "ok", {"changes": [], "unchanged": 5})
    check("无事可做时措辞说「已经一致」", "已经一致" in b["message"], b["message"])
    b = S.Handler._obsidian_body(object(), "ok", {"blocked": "行数掉太多", "changes": []})
    check("dry-run 被护栏拦下时 ok 是 false", b["ok"] is False and "行数掉太多" in b["message"])
finally:
    os.environ.pop("FT_OBSIDIAN", None)
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 62)
print("通过 %d / 失败 %d" % (len(ok), len(bad)))
if bad:
    print("失败项：")
    for b in bad:
        print("  - " + b)
sys.exit(1 if bad else 0)
