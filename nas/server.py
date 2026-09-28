#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""film-tap 自建后端 —— 零第三方依赖，只用标准库。

这就是这个 App 的正式形态：页面和数据都由这个进程发出来，
跑在自己 NAS 上的一个容器里。镜像由 GitHub Actions 自动构建（见
.github/workflows/docker.yml），NAS 上由 watchtower 自动拉新版。

它顺带解决了两件原先做不到的事 ——

  ① 数据不再进任何 Git 仓库。
     数据就是 NAS 上一个普通文件，没有第二种凭据要维护，
     也不用担心「提交进 Git 的东西删不掉」。

  ② **「本机存储被清空后自动恢复」第一次能做成真的。**
     Safari 的 7 天清理只清「脚本写的」存储（localStorage / IndexedDB / Cache），
     服务器用 Set-Cookie 下发的 cookie **不在清理范围内**（有效期可到 400 天）。
     于是会话能活过那次清空，页面一打开就自动把数据拉回来 ——
     不需要用户重填任何东西。这是几条备份路线里唯一能做到的。

刻意保持的东西：
  · 密码只以加盐哈希落在 data 目录的 config.json（0600）；页面里、代码里、
    日志里都不出现明文。日志只记「方法 + 路径 + 状态码」，绝不记请求体。
  · 写文件是「同目录临时文件 → fsync → os.replace」，半截写入不会顶掉好的那份。
  · 每次接受写入都留一份带时间戳的历史副本 —— 这是原来只有 Git 才有的东西。
  · 拒绝「服务端有 N 台机身、而送来的这份是空的」这种上传（除非带 force）。
    这是唯一会造成**静默丢数据**的路径：本机存储被系统清理后随手记一台机身，
    那一下就能把服务端几十台冲掉，而且不报任何错。
"""

import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

APP_NAME      = "film-tap"
DATA_FILE     = "data.json"
HISTORY_DIR   = "history"
CONFIG_FILE   = "config.json"
SESSIONS_FILE = "sessions.json"

COOKIE_NAME    = "ft_sid"
COOKIE_MAX_AGE = 400 * 24 * 3600     # 400 天：Safari 对服务端下发的 cookie 就是给到这个上限
HISTORY_KEEP   = 120                 # 历史副本保留份数，超了就删最旧的
MAX_BODY       = 4 * 1024 * 1024     # 4MB，正常数据是几十 KB

LOGIN_WINDOW   = 300                 # 登录失败统计窗口（秒）
LOGIN_MAX_FAIL = 8                   # 窗口内失败超过这个数就 429

# Obsidian 联动（见下面的解析 / 合并 / 回写那一节）
OBSIDIAN_BASELINE    = "obsidian-sync.json"   # 同步基线，独立于 data.json
OBSIDIAN_BACKUP_DIR  = "obsidian-backups"     # 回写前的原文备份
OBSIDIAN_BACKUP_KEEP = 20

# 静态文件白名单。**不做目录遍历** —— 只认这几个文件名，别的路径一律 404。
STATIC = {
    "index.html":            "text/html; charset=utf-8",
    "sw.js":                 "text/javascript; charset=utf-8",
    "manifest.webmanifest":  "application/manifest+json; charset=utf-8",
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha16(data):
    return hashlib.sha256(data).hexdigest()[:16]


def hash_password(password, salt=None):
    """PBKDF2-SHA256。返回 "salt$hash"，两个都是 hex。"""
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), 200000)
    return salt + "$" + dk.hex()


def verify_password(password, stored):
    if not stored or "$" not in stored:
        return False
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_password(password, salt), stored)


# ══════════════ Obsidian 联动：解析 / 重算 / 回写 ══════════════
#
# 两份笔记，各管一件事：
#
#   · 胶卷账本.md   —— **唯一真源**，三节表格：
#       `## 期初（…）` 冻结快照，余额就是它自己
#       `## 流水`     只能追加，**只有这一节参与累加**
#       `## 历史（…）` 改造前的记录，**不参与累加**（理由见下）
#     于是：库存 = 期初 + Σ流水。
#   · 胶卷库存清单.md —— **生成物**，三列（品牌/型号 | 格式 | 数量），
#     每次同步按账本重算后写回。回写仍只替换数量单元格里的数字，其余逐字节保留。
#
# 为什么历史段必须排除在求和之外：改造前那些流水 / 旧账都发生在期初**之前**，
# 已经含在期初里了。再加一遍就会把 99 卷算成 120 卷。**不按日期过滤** ——
# Hermes 补录旧日期会漏，而且当日边界很容易写错；按节排除是确定性的。
#
# App 侧的扣减（装卷）也会变成流水行：同步时拿 data.json 的数量和基线比，
# 变少就追加一条「消耗」，变多就追加一条「盘点修正」。

OBSIDIAN_SECTION       = "胶卷列表"   # 生成物里的那一节
OBSIDIAN_LEDGER_BEGIN  = "期初"       # 账本：冻结快照
OBSIDIAN_LEDGER_FLOW   = "流水"       # 账本：唯一参与累加的一节
OBSIDIAN_LEDGER_HISTORY = "历史"      # 账本：改造前的记录，只存档
FORMATS = ("135", "120", "110", "大画幅")

# 表头别名。比前端的 IMPORT_COLS 窄：这里**故意不收「类型」** ——
# 本文件里「类型」是独立的一列（电影卷 / 彩色负片 / 黑白负片），
# 把「类型」当成型号别名会把它错认成型号列。
OBS_COLS = {
    "stock":  ("品牌/型号", "品牌型号", "型号", "胶卷型号", "型号名称", "胶卷", "名称", "品名", "film", "stock"),
    "count":  ("数量", "卷数", "剩余", "剩余卷数", "存量", "库存", "qty", "count", "rolls"),
    "format": ("格式", "画幅", "规格", "幅面", "size", "format"),
}

# 流水表的表头别名。**故意不把「库存」「剩余」收进 qty** ——
# 那一列是「本次变动量」（带符号），不是余额，混进来会把 -1 当成余量。
# 备注列必须认出来：film-tap 自己追加的行靠 AUTO_TAG 认，见 is_auto_row()。
LEDGER_COLS = {
    "date":   ("日期", "时间", "date"),
    "type":   ("类型", "类别", "type", "kind"),
    "stock":  OBS_COLS["stock"],
    "format": OBS_COLS["format"],
    "qty":    ("数量", "卷数", "增减", "变动", "qty", "delta"),
    "note":   ("备注", "说明", "注", "note", "remark", "memo"),
}

# film-tap 自动追加的流水行，备注都以这个开头。它必须能和人手写的行分开 ——
# 「账本这一侧自己变了多少」正是靠这个算出来的（见 compute_stock 的 u / m）。
AUTO_TAG = "[自动]"

# 拍摄记录：每次「拍完换卷」追一行。它不参与任何累加，只是一份日志，
# 所以去重靠「自然键 + 表里已有内容」两条（见 shots_from_cameras）。
OBSIDIAN_SHOTS_SECTION = "拍摄记录"
SHOT_COLS = {
    "camera":   ("相机", "机身", "camera"),
    "stock":    ("胶卷型号", "品牌/型号", "型号", "film", "stock"),
    "loaded":   ("装卷日", "装卷日期", "上卷日", "loaded"),
    "unloaded": ("退卷日", "退卷日期", "卸卷日", "拍完日", "unloaded"),
    "frames":   ("张数", "拍摄张数", "标称张数", "frames"),
    "note":     ("备注", "说明", "note"),
}

_FORMAT_PROBES = (
    ("大画幅", re.compile(r"4\s*[x×*]\s*5|大画幅|页片|sheet", re.I)),
    ("135",   re.compile(r"135|\b3[35]\s*mm\b|全画幅", re.I)),
    ("120",   re.compile(r"\b120\b|中画幅|6\s*[x×*]\s*[467]|645", re.I)),
    ("110",   re.compile(r"\b110\b", re.I)),
)


def film_key(stock, fmt):
    """与前端 filmKey() 完全等价：小写 + 空白折叠成 '-'，中文原样保留。
    两边算出来的键必须一模一样，否则同一条库存会被当成两条。"""
    base = re.sub(r"\s+", "-", str(stock or "").strip().lower()).strip("-")
    return (base or "roll") + "@" + (fmt or "其他")


def probe_format(text):
    for name, rx in _FORMAT_PROBES:
        if rx.search(text or ""):
            return name
    return None


def norm_format(value, stock=""):
    """画幅归一：表里写了就用表里的，没写就从型号名里猜。与前端 normFormat 等价。"""
    s = str(value if value is not None else "").strip()
    if s:
        got = probe_format(s)
        if got:
            return got
    return probe_format(str(stock or "")) or "135"


def parse_count(value):
    """从 "4卷" / "4" 里取数字。读不出返回 None（这行会被记下来，不静默丢掉）。"""
    m = re.search(r"\d+", str(value if value is not None else ""))
    return int(m.group()) if m else None


def _norm_head(s):
    t = str(s if s is not None else "").strip().lower()
    t = re.sub(r"[\s\u3000]", "", t)
    t = re.sub(r"[（(].*?[)）]", "", t)
    return re.sub(r"[:：*]", "", t)


def map_columns(headers, cols=None):
    """识别表头 → {stock:1, count:5, format:4}（列定义由 cols 给，默认库存表那套）。
    必须整表精确匹配一遍、再整表包含匹配一遍 —— 合成一趟的话，
    「品牌/型号」会被先命中的「型号」抢走。与前端 mapImport 同一套两趟法。"""
    cols = cols or OBS_COLS
    norms = [_norm_head(h) for h in headers]
    out = {}
    for loose in (False, True):
        for i, n in enumerate(norms):
            if not n:
                continue
            for key, aliases in cols.items():
                if key in out:
                    continue
                for a in aliases:
                    na = _norm_head(a)
                    hit = (na in n) if loose else (n == na)
                    if hit:
                        out[key] = i
                        break
    return out


def iter_cells(line):
    """把一行 markdown 表格切成 [(start, end, text)]，start/end 是**原文里的偏移**。
    回写就靠这个偏移只替换数量那一格里的数字，其余字符一个都不动。"""
    cells, start = [], None
    for i, ch in enumerate(line):
        if ch == "|":
            if start is not None:
                cells.append((start, i, line[start:i]))
            start = i + 1
    if start is not None and line[start:].strip() != "":
        cells.append((start, len(line), line[start:]))
    return cells


def _is_sep_row(texts):
    """表格的分隔行（|------|------|）。"""
    return bool(texts) and all(t.strip() and set(t.strip()) <= {"-", ":"} for t in texts)


def _section_range(lines, title):
    """定位「## <title>…」到下一个「## 」之间的 [start, end) 行区间；找不到返回 None。
    允许标题带后缀（如「## 期初（2026-09-28）」），所以是前缀匹配而不是全等 ——
    但前缀是锚在 `## ` 之后的，所以「## 历史（…已计入期初…）」不会被当成「期初」那一节。"""
    head = re.compile(r"^##\s+" + re.escape(title))
    start = None
    for i, ln in enumerate(lines):
        if head.match(ln.rstrip("\r\n").strip()):
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start, len(lines)):
        if re.match(r"^##\s+", lines[j].rstrip("\r\n").strip()):
            end = j
            break
    return (start, end)


def _table_line_indexes(lines, start, end):
    """在 [start,end) 里找第一张**连续**的 markdown 表 → (表头行号, [各行行号])。
    连表头都找不到就是 (None, [])。"""
    hdr = None
    for j in range(start, end):
        s = lines[j].rstrip("\r\n").strip()
        if s.startswith("|") and s.count("|") >= 2:
            hdr = j
            break
    if hdr is None:
        return None, []
    out = []
    for j in range(hdr, end):
        if lines[j].rstrip("\r\n").strip().startswith("|"):
            out.append(j)
        else:
            break
    return hdr, out


def parse_stock_table(lines, start, end):
    """解析「品牌/型号 | 格式 | 数量」这张三列表 —— 「## 胶卷列表」（生成物）和
    账本的「## 期初」是同一个形状，共用这一份。
    → {ok, reason?, rows, skipped}；rows 带上重算时判断用不上的行号与回写用的单元格偏移。"""
    hdr, table = _table_line_indexes(lines, start, end)
    if hdr is None:
        return {"ok": False, "reason": "这一节里没有表格"}

    header_cells = [c[2].strip() for c in iter_cells(lines[hdr].rstrip("\r\n"))]
    colmap = map_columns(header_cells)
    missing = [k for k in ("stock", "count") if k not in colmap]
    if missing:
        return {"ok": False,
                "reason": "表头里认不出「%s」列（表头是：%s）"
                          % ("、".join(OBS_COLS[k][0] for k in missing), " / ".join(header_cells))}

    rows, skipped = [], []
    for j in table[1:]:
        body = lines[j].rstrip("\r\n")
        cells = iter_cells(body)
        texts = [c[2] for c in cells]
        if _is_sep_row(texts):
            continue
        # 修「|| 7 |」这种多出来的空首格 —— 真实文件里有过两行是这样。
        while len(cells) > len(header_cells) and cells[0][2].strip() == "":
            cells.pop(0)
        if len(cells) != len(header_cells):
            skipped.append({"line": j + 1, "reason": "有 %d 格，表头是 %d 格"
                                                  % (len(cells), len(header_cells))})
            continue
        stock = cells[colmap["stock"]][2].strip()
        count_raw = cells[colmap["count"]][2]
        if not stock:
            skipped.append({"line": j + 1, "reason": "这一行没有型号"})
            continue
        count = parse_count(count_raw)
        if count is None:
            skipped.append({"line": j + 1, "reason": "数量里读不出数字：%s" % count_raw.strip()})
            continue
        fmt = norm_format(cells[colmap["format"]][2] if "format" in colmap else "", stock)
        rows.append({
            "line": j + 1,
            "line_index": j,
            "stock": stock,
            "format": fmt,
            "count": count,
            "raw_count": count_raw.strip(),
            "cell": cells[colmap["count"]][:2],
        })

    return {"ok": True, "rows": rows, "skipped": skipped}


def parse_film_table(text):
    """解析「## 胶卷列表」里的那张表（生成物）。
    → {ok, reason?, lines, rows, skipped}；rows 里带上回写要用的行号与单元格偏移。"""
    lines = text.splitlines(True)
    if not lines:
        return {"ok": False, "reason": "文件是空的"}
    rng = _section_range(lines, OBSIDIAN_SECTION)
    if rng is None:
        return {"ok": False, "reason": "没找到「## %s」这一节" % OBSIDIAN_SECTION}
    got = parse_stock_table(lines, rng[0], rng[1])
    if not got["ok"]:
        return {"ok": False, "reason": "「%s」%s" % (OBSIDIAN_SECTION, got["reason"])}
    if not got["rows"]:
        return {"ok": False, "reason": "「%s」的表里一行数据都没解析出来" % OBSIDIAN_SECTION}
    return {"ok": True, "lines": lines, "rows": got["rows"], "skipped": got["skipped"]}


def parse_signed_count(value):
    """流水里的「变动量」→ 带符号整数。`+2` / `-1` / `2` / 全角 `－1` 都认。
    空、`—` 这类占位返回 None —— 账本历史段里「当时没记数量」就是这么写的，
    那不是错误，所以也不算 skipped。"""
    s = str(value if value is not None else "").strip()
    s = s.replace("－", "-").replace("−", "-").replace("＋", "+")
    if not s or set(s) <= set("-+—–·. "):
        return None
    m = re.search(r"[+-]?\d+", s)
    return int(m.group()) if m else None


def parse_flow_table(lines, start, end):
    """解析账本里的流水表（日期 | 类型 | 品牌/型号 | 格式 | 数量 | 单价 | 金额 | 备注）。
    「## 流水」和「## 历史」形状相同、共用这一份 —— 但**求和时只取流水那一节**。
    → {ok, reason?, rows, skipped, header, last}；last 是表内最后一个行号，追加就插它后面。"""
    hdr, table = _table_line_indexes(lines, start, end)
    if hdr is None:
        return {"ok": False, "reason": "这一节里没有表格"}

    header_cells = [c[2].strip() for c in iter_cells(lines[hdr].rstrip("\r\n"))]
    colmap = map_columns(header_cells, LEDGER_COLS)
    missing = [k for k in ("stock", "qty") if k not in colmap]
    if missing:
        return {"ok": False,
                "reason": "表头里认不出「%s」列（表头是：%s）"
                          % ("、".join(LEDGER_COLS[k][0] for k in missing), " / ".join(header_cells))}

    rows, skipped = [], []
    last = hdr
    for j in table[1:]:
        last = j
        body = lines[j].rstrip("\r\n")
        cells = iter_cells(body)
        texts = [c[2] for c in cells]
        if _is_sep_row(texts):
            continue
        while len(cells) > len(header_cells) and cells[0][2].strip() == "":
            cells.pop(0)
        if len(cells) != len(header_cells):
            skipped.append({"line": j + 1, "reason": "有 %d 格，表头是 %d 格"
                                                  % (len(cells), len(header_cells))})
            continue
        stock = cells[colmap["stock"]][2].strip()
        if not stock:
            skipped.append({"line": j + 1, "reason": "这一行没有型号"})
            continue
        qty_raw = cells[colmap["qty"]][2]
        rows.append({
            "line": j + 1,
            "line_index": j,
            "date": cells[colmap["date"]][2].strip() if "date" in colmap else "",
            "type": cells[colmap["type"]][2].strip() if "type" in colmap else "",
            "stock": stock,
            "format": norm_format(cells[colmap["format"]][2] if "format" in colmap else "", stock),
            "qty": parse_signed_count(qty_raw),
            "raw_qty": qty_raw.strip(),
            "note": cells[colmap["note"]][2].strip() if "note" in colmap else "",
        })
        # 数量读不出**不算 skipped**：历史段里「当时没记数量」就是这么留的，那是合法的。
        # 但流水段里出现这种行会让余额对不上，所以由调用方单独提出来（见 obsidian_sync）。

    return {"ok": True, "rows": rows, "skipped": skipped, "header": hdr, "last": last}


def parse_ledger(text):
    """解析胶卷账本。
    → {ok, reason?, lines, begin_range, flow_range, begin_rows, begin_skipped,
       flow_rows, flow_skipped, flow_last, history_rows, history_skipped}
    「## 期初」和「## 流水」缺一节就不 ok；「## 历史」可有可无（它只存档）。"""
    lines = text.splitlines(True)
    if not lines:
        return {"ok": False, "reason": "文件是空的"}

    begin_rng = _section_range(lines, OBSIDIAN_LEDGER_BEGIN)
    if begin_rng is None:
        return {"ok": False, "reason": "账本里没找到「## %s」这一节" % OBSIDIAN_LEDGER_BEGIN}
    flow_rng = _section_range(lines, OBSIDIAN_LEDGER_FLOW)
    if flow_rng is None:
        return {"ok": False, "reason": "账本里没找到「## %s」这一节" % OBSIDIAN_LEDGER_FLOW}

    begin = parse_stock_table(lines, begin_rng[0], begin_rng[1])
    if not begin["ok"]:
        return {"ok": False, "reason": "「%s」%s" % (OBSIDIAN_LEDGER_BEGIN, begin["reason"])}
    if not begin["rows"]:
        return {"ok": False, "reason": "「%s」的表里一行数据都没解析出来" % OBSIDIAN_LEDGER_BEGIN}

    # 流水表刚改造完是空的（只有表头 + 分隔行），那是正常的；但表头必须在，
    # 否则往哪儿追加、按什么列读都无从谈起。
    flow = parse_flow_table(lines, flow_rng[0], flow_rng[1])
    if not flow["ok"]:
        return {"ok": False, "reason": "「%s」%s" % (OBSIDIAN_LEDGER_FLOW, flow["reason"])}

    hist_rng = _section_range(lines, OBSIDIAN_LEDGER_HISTORY)
    history = {"rows": [], "skipped": []}
    if hist_rng is not None:
        h = parse_flow_table(lines, hist_rng[0], hist_rng[1])
        if h["ok"]:
            history = h

    return {"ok": True, "lines": lines,
            "begin_range": begin_rng, "flow_range": flow_rng,
            "begin_rows": begin["rows"], "begin_skipped": begin["skipped"],
            "flow_rows": flow["rows"], "flow_skipped": flow["skipped"],
            "flow_last": flow["last"],
            "history_rows": history["rows"], "history_skipped": history["skipped"]}


def parse_shots_table(text):
    """解析拍摄记录笔记（`## 拍摄记录` 那张表）。
    → {ok, reason?, lines, rows, fingerprints, header, last, colmap, ncols}
    - `fingerprints` 是每一行「内容指纹」的集合，用来去重（表里已经有同样的事就不再追）。
      **指纹里不含机身名** —— 改过名的机身不该被当成一桩新事件再写一遍。
    - `colmap` / `ncols` 给拼新行用：**按表头位置摆**，所以表头少一两列也照样能写。
    必需列只有「胶卷型号」和「退卷日」—— 没有它们就认不出是哪一卷、什么时候退的。"""
    lines = text.splitlines(True)
    if not lines:
        return {"ok": False, "reason": "文件是空的"}
    rng = _section_range(lines, OBSIDIAN_SHOTS_SECTION)
    if rng is None:
        return {"ok": False, "reason": "没找到「## %s」这一节" % OBSIDIAN_SHOTS_SECTION}
    hdr, table = _table_line_indexes(lines, rng[0], rng[1])
    if hdr is None:
        return {"ok": False, "reason": "「%s」这一节里没有表格" % OBSIDIAN_SHOTS_SECTION}

    header_cells = [c[2].strip() for c in iter_cells(lines[hdr].rstrip("\r\n"))]
    colmap = map_columns(header_cells, SHOT_COLS)
    missing = [k for k in ("stock", "unloaded") if k not in colmap]
    if missing:
        return {"ok": False,
                "reason": "「%s」表头里认不出「%s」列（表头是：%s）"
                          % (OBSIDIAN_SHOTS_SECTION,
                             "、".join(SHOT_COLS[k][0] for k in missing),
                             " / ".join(header_cells))}

    cam_col = colmap.get("camera")
    rows, fps, last = [], set(), hdr
    for j in table[1:]:
        last = j
        body = lines[j].rstrip("\r\n")
        texts = [c[2].strip() for c in iter_cells(body)]
        if _is_sep_row(texts):
            continue
        while len(texts) > len(header_cells) and texts[0] == "":
            texts.pop(0)
        while len(texts) < len(header_cells):
            texts.append("")
        if not any(texts):
            continue                    # 全空的占位行：不算数据，也不算错
        rows.append({"line": j + 1, "line_index": j, "cells": texts})
        fps.add(shot_fingerprint(texts, cam_col))

    return {"ok": True, "lines": lines, "rows": rows, "fingerprints": fps,
            "header": hdr, "last": last, "colmap": colmap, "ncols": len(header_cells)}


def shot_fingerprint(cells, cam_col):
    """一行的内容指纹：把机身名那一格抹掉再比 —— 改名不该算成一桩新事件。
    其余各格（型号、两个日期、张数、备注）有一格不同就算另一桩事。"""
    fp = list(cells)
    if isinstance(cam_col, int) and 0 <= cam_col < len(fp):
        fp[cam_col] = ""
    return tuple(fp)


def is_auto_row(row):
    """这一行是 film-tap 自己追加的吗？靠备注开头的标记认。
    自动行必须能和「人写的行」分开：账本这一侧自己变了多少，正是靠这个算出来的。"""
    return str(row.get("note") or "").strip().startswith(AUTO_TAG)


def compute_stock(begin_rows, flow_rows):
    """库存 = 期初 + Σ流水。
    → ([{key, stock, format, count, u, m}], [warning])，顺序 = 期初顺序 + 账本里新出现的款。

    count = u + m，拆开记是因为「要不要追加流水」算的是账本**人侧**的净变化：
      u = 期初 + **人写的**流水（购入 / 售出 / 消耗 / 盘点修正）
      m = film-tap **自动追加的**流水（装卷扣减、App 侧手改库存）

    负数**只告警、不钳** —— 钳了等式就不自洽了（「对不上账」这件事必须说出来）；
    真正钳到 0 只发生在写给清单和 App 的那一步。"""
    out, order = {}, []

    def slot(stock, fmt):
        k = film_key(stock, fmt)
        if k not in out:
            out[k] = {"key": k, "stock": stock, "format": fmt,
                      "count": 0, "u": 0, "m": 0}
            order.append(k)
        return out[k]

    for r in begin_rows:
        s = slot(r["stock"], r["format"])
        s["count"] += int(r["count"])
        s["u"] += int(r["count"])

    for r in flow_rows:
        if r.get("qty") is None:
            continue
        s = slot(r["stock"], r["format"])
        s["count"] += int(r["qty"])
        if is_auto_row(r):
            s["m"] += int(r["qty"])
        else:
            s["u"] += int(r["qty"])

    warnings = []
    for k in order:
        if out[k]["count"] < 0:
            warnings.append("%s：账本累加后是 %d 卷，库存按 0 显示 —— "
                            "多半是漏记了购入，或者同一卷被扣了两次"
                            % (out[k]["stock"], out[k]["count"]))
    return [out[k] for k in order], warnings


def loaded_dates(cameras):
    """机身里当前装着的那卷 → {film_key: 装卷日期}。

    装卷那一瞬间就是库存 -1 的时刻，拿它的 loadedAt 当流水日期，比「同步当天」准。
    多台机身撞同一款时取最新那个日期。"""
    out = {}
    for c in (cameras or {}).values():
        if not isinstance(c, dict):
            continue
        r = c.get("loaded")
        if not isinstance(r, dict):
            continue
        stock = str(r.get("stock") or "").strip()
        if not stock:
            continue
        k = film_key(stock, norm_format(c.get("format"), stock))
        d = str(r.get("loadedAt") or "")
        if d and d > out.get(k, ""):
            out[k] = d
    return out


def shot_rows_from_cameras(cameras):
    """机身 history 里「已经退过卷」的那些卷 → 拍摄记录要用的字段（还没去重）。

    「已经退卷」= history 里有 `unloadedAt` 的条目。装新卷顶掉旧卷、以及
    「标记为空机」，在 App 里都是把当前那卷推进 history，所以两种都算「拍完换卷」。

    `key` 是稳定自然键（机身 id + 型号 + 装卷日 + 退卷日）—— 用 id 而不是机身名，
    改过名的机身不会因为名字变了就把老记录再写一遍。
    → 按退卷日排序的列表；`fields` 里的键和 SHOT_COLS 对齐。"""
    out = []
    for cid, c in (cameras or {}).items():
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip() or str(cid)
        hist = c.get("history")
        if not isinstance(hist, list):
            continue
        for r in hist:
            if not isinstance(r, dict):
                continue
            stock = str(r.get("stock") or "").strip()
            unloaded = str(r.get("unloadedAt") or "").strip()
            if not stock or not unloaded:
                continue            # 没型号 / 没退卷日：认不出是哪一卷，不写
            loaded = str(r.get("loadedAt") or "").strip()
            frames = r.get("total")
            frames = str(int(frames)) if isinstance(frames, (int, float)) and frames else ""
            note = str(r.get("note") or "").strip()
            ei, iso = r.get("ei"), r.get("iso")
            if ei and iso and ei != iso:
                tip = "按 EI %s 拍" % ei
                note = (note + "；" + tip) if note else tip
            out.append({
                "key": "|".join([str(cid), stock, loaded, unloaded]),
                "fields": {"camera": name, "stock": stock, "loaded": loaded,
                           "unloaded": unloaded, "frames": frames, "note": note},
            })
    out.sort(key=lambda x: (x["fields"]["unloaded"], x["fields"]["loaded"],
                            x["fields"]["camera"], x["fields"]["stock"]))
    return out


def shot_cells(fields, colmap, ncols):
    """把字段按**表头的位置**摆成一行。表头少几列也照写（缺的列就空着）。"""
    cells = [""] * ncols
    for name, col in colmap.items():
        if 0 <= col < ncols:
            cells[col] = str(fields.get(name) or "")
    return cells


def plan_app_flows(films, baseline, ledger_rows, dates=None, today=""):
    """App 侧的变动 → 要往账本追加的流水行，以及重算后的库存。

    ledger_rows = compute_stock() 的结果；baseline = {key: 上次同步时该款的 u}。

    对每一款推一遍：

        F = A + (U - Ubase)
        A     = App 现值
        U     = 账本「人侧」存量（期初 + 人写的流水）
        Ubase = 基线里记的人侧存量

    也就是「App 现值 + 账本这一侧自上次约定以来的净变化」—— 买卷（+）和装卷（-）
    都是加法事件，两边各自动过一次也能叠加，不需要「谁赢」这种规则。
    要追加的量 = `F - L`，L 是账本现值（U + M，含我们以前自动追加过的）。

    这条式子是**幂等的**：追加完 L 就等于 F，重放一次算出来就是 0 ——
    所以哪怕「基线还没来得及写」就崩了，也不会重复扣减。
    基线缺失时取 Ubase = A - M，等价于「假设上次两边一致」：于是 F = L，一行都不追加，
    以账本为准。

    → (planned, appended)；planned 是重算后的库存（顺序即写入清单的顺序），
      appended 是要追加的流水行。负数只告警不钳，钳只发生在写出去那一步。"""
    dates = dates or {}
    planned, appended = [], []
    index = {r["key"]: r for r in ledger_rows}

    for L in ledger_rows:                       # 顺序即期初顺序，写入清单时照抄
        key = L["key"]
        cur = films.get(key)
        has = isinstance(cur, dict)
        was = int(cur.get("count") or 0) if has else None
        A = max(0, int(cur.get("count") or 0)) if has else 0

        Ubase = baseline.get(key)
        if not isinstance(Ubase, int):
            Ubase = A - L["m"]          # 没基线 → 假设上次两边一致，以账本为准

        F = A + (L["u"] - Ubase)
        q = F - L["count"]
        if q:
            appended.append({
                "key": key, "stock": L["stock"], "format": L["format"], "qty": q,
                "type": "消耗" if q < 0 else "盘点修正",
                "date": dates.get(key) or today,
                "note": (AUTO_TAG + " 装卷从库存里拿走") if q < 0
                        else (AUTO_TAG + " 在 App 里改了库存"),
            })
        planned.append({"key": key, "stock": L["stock"], "format": L["format"],
                        "count": max(0, F), "raw": F, "was": was})

    # 账本里根本没有、但 App 里有的款：只能补一条盘点修正把它记进去，
    # 否则「库存 = 期初 + Σ流水」就解释不了它。
    for key in sorted(films):
        if key in index:
            continue
        rec = films[key] if isinstance(films.get(key), dict) else {}
        stock = str(rec.get("stock") or "").strip()
        A = max(0, int(rec.get("count") or 0))
        if not stock or not A:
            continue
        fmt = norm_format(rec.get("format"), stock)
        appended.append({"key": key, "stock": stock, "format": fmt, "qty": A,
                         "type": "盘点修正", "date": today,
                         "note": AUTO_TAG + " App 里有、账本里没有，先记一笔"})
        planned.append({"key": key, "stock": stock, "format": fmt,
                        "count": A, "raw": A, "was": A})

    warnings = []
    for p in planned:
        if p["raw"] < 0:
            warnings.append("%s：重算后是 %d 卷，库存按 0 显示 —— "
                            "多半是漏记了购入，或者同一卷被扣了两次"
                            % (p["stock"], p["raw"]))
    return planned, appended, warnings


def apply_counts(lines, updates):
    """把 rows 里的数量改成新值。**只替换那一格里的数字**：行尾、其它单元格、
    以及整份文件的其余部分都原样保留。updates = [(line_index, start, end, new)]。"""
    out = list(lines)
    for line_index, start, end, new_count in updates:
        body = out[line_index].rstrip("\r\n")
        tail = out[line_index][len(body):]
        m = re.search(r"\d+", body[start:end])
        if not m:
            continue
        out[line_index] = body[:start + m.start()] + str(new_count) + body[start + m.end():] + tail
    return out


def rebuild_stock_table(lines, start, end, rows):
    """把「## 胶卷列表」的表行整段重建 —— 只在**款式集合变了**（账本里冒出新款）时才走这条
    路；集合没变一律走 apply_counts，改动面更小。表头与分隔行原样保留，节外逐字节不动。
    rows = [{stock, format, count}]，顺序即输出顺序。返回 None 表示这一段结构不正常。"""
    hdr, table = _table_line_indexes(lines, start, end)
    if hdr is None:
        return None
    keep = 1
    if len(table) > 1 and _is_sep_row([c[2] for c in iter_cells(lines[table[1]].rstrip("\r\n"))]):
        keep = 2
    if len(table) < keep:
        return None
    insert_at = hdr + keep
    tail_at = table[-1] + 1
    nl = "\r\n" if lines[hdr].endswith("\r\n") else "\n"
    fresh = ["| %s | %s | %d |%s" % (r["stock"], r["format"], r["count"], nl) for r in rows]
    return lines[:insert_at] + fresh + lines[tail_at:]


def append_flow_rows(lines, last, rows):
    """在流水表最后一行（行号 last）后面插入若干流水行。
    rows = [[日期, 类型, 品牌/型号, 格式, 数量, 单价, 金额, 备注]]，逐格按字符串写。"""
    if not rows:
        return lines
    nl = "\r\n" if lines[last].endswith("\r\n") else "\n"
    fresh = ["| " + " | ".join(str(c) for c in r) + " |" + nl for r in rows]
    return lines[:last + 1] + fresh + lines[last + 1:]


class Store:
    """data 目录里的一切都在这里。所有写操作走同一把锁。"""

    def __init__(self, data_dir, log):
        self.dir = data_dir
        self.log = log
        self.lock = threading.RLock()
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        os.makedirs(os.path.join(self.dir, HISTORY_DIR), mode=0o700, exist_ok=True)
        # 内存里的登录失败计数：{ip: [时间戳, ...]}。重启就清空，无所谓。
        self.fails = {}

    # ── 路径 ──
    def p(self, *parts):
        return os.path.join(self.dir, *parts)

    # ── 小文件读写（配置文件 / 会话表）──
    def _write_json(self, path, obj, mode=0o600):
        tmp = path + ".tmp"
        raw = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)          # 同目录 rename 是原子的

    def _read_json(self, path, default=None):
        try:
            with open(path, "rb") as f:
                return json.loads(f.read().decode("utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return default

    # ── 密码 ──
    def configured(self):
        c = self._read_json(self.p(CONFIG_FILE), {})
        return bool(c.get("passwordHash"))

    def set_password(self, password):
        with self.lock:
            c = self._read_json(self.p(CONFIG_FILE), {}) or {}
            c["passwordHash"] = hash_password(password)
            c["updatedAt"] = now_iso()
            self._write_json(self.p(CONFIG_FILE), c, 0o600)

    def check_password(self, password):
        c = self._read_json(self.p(CONFIG_FILE), {}) or {}
        return verify_password(password, c.get("passwordHash"))

    # ── 会话 ──
    def new_session(self):
        token = secrets.token_urlsafe(32)
        with self.lock:
            s = self._read_json(self.p(SESSIONS_FILE), {}) or {}
            now = time.time()
            # 顺手清掉过期的，免得这个文件只增不减
            s = {k: v for k, v in s.items()
                 if isinstance(v, dict) and v.get("exp", 0) > now}
            s[sha16(token.encode())] = {"exp": now + COOKIE_MAX_AGE,
                                        "created": now_iso()}
            self._write_json(self.p(SESSIONS_FILE), s, 0o600)
        return token

    def has_session(self, token):
        if not token:
            return False
        s = self._read_json(self.p(SESSIONS_FILE), {}) or {}
        v = s.get(sha16(token.encode()))
        return bool(v and v.get("exp", 0) > time.time())

    def drop_session(self, token):
        if not token:
            return
        with self.lock:
            s = self._read_json(self.p(SESSIONS_FILE), {}) or {}
            s.pop(sha16(token.encode()), None)
            self._write_json(self.p(SESSIONS_FILE), s, 0o600)

    # ── 登录失败限速（这是公网可达的，不能让人无限试密码）──
    def note_fail(self, ip):
        now = time.time()
        with self.lock:
            q = [t for t in self.fails.get(ip, []) if now - t < LOGIN_WINDOW]
            q.append(now)
            self.fails[ip] = q
            return len(q)

    def too_many_fails(self, ip):
        now = time.time()
        with self.lock:
            q = [t for t in self.fails.get(ip, []) if now - t < LOGIN_WINDOW]
            self.fails[ip] = q
            return len(q) >= LOGIN_MAX_FAIL

    def clear_fails(self, ip):
        with self.lock:
            self.fails.pop(ip, None)

    # ── 数据 ──
    def read_data(self):
        """→ (bytes|None, rev|None)。rev 就是文件内容的哈希 ——
        不另开一个计数器文件，就不会出现「计数器写失败但数据写成功」这种不一致。"""
        try:
            with open(self.p(DATA_FILE), "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return None, None
        return raw, sha16(raw)

    def write_data(self, raw, rev_from_client, force):
        """返回 (状态, 附加信息)。
        状态是 "ok" / "stale" / "not_empty_overwrite"。"""
        with self.lock:
            cur_raw, cur_rev = self.read_data()

            try:
                incoming = json.loads(raw.decode("utf-8"))
            except ValueError:
                return "bad_json", {}
            if not isinstance(incoming, dict) or not isinstance(incoming.get("cameras"), dict):
                return "bad_json", {}

            in_n = len(incoming["cameras"])
            cur_n = 0
            if cur_raw:
                try:
                    cur_n = len(json.loads(cur_raw.decode("utf-8")).get("cameras", {}))
                except ValueError:
                    cur_n = 0

            if not force:
                # ① 会静默清空服务端的那一下，直接拦下
                if cur_raw and cur_n > 0 and in_n == 0:
                    return "not_empty_overwrite", {"remote": cur_n}
                # ② 客户端报的版本和服务端对不上 → 别处改过，让它先拉一次
                #    （客户端没带版本号就跳过这一条：那多半是本机刚被清空过，
                #      这种情况由上面 ① 兜住最要紧的那部分）
                if rev_from_client and rev_from_client != cur_rev:
                    return "stale", {"rev": cur_rev}

            tmp = self.p(DATA_FILE + ".tmp")
            # 0o600：data.json 是你的机身与胶卷清单，不需要让容器里别的
            # 进程（或任何将来加进来的东西）读得到。容器里现在只有
            # server.py 自己在跑，这是纵深防御，不是当前有洞。
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, raw)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self.p(DATA_FILE))     # 原子替换

            new_rev = sha16(raw)
            self._snapshot(raw, new_rev)
            return "ok", {"rev": new_rev}

    def _snapshot(self, raw, rev):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.p(HISTORY_DIR, "%s-%s.json" % (stamp, rev))
        # 历史副本和本体同级敏感 —— 里面是同一份数据的不同版本。
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        # 只留最近 HISTORY_KEEP 份
        d = self.p(HISTORY_DIR)
        names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
        for n in names[:-HISTORY_KEEP]:
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass

    def history(self, limit=50):
        d = self.p(HISTORY_DIR)
        try:
            names = sorted((n for n in os.listdir(d) if n.endswith(".json")), reverse=True)
        except OSError:
            return []
        out = []
        for n in names[:limit]:
            item = {"name": n}
            try:
                with open(os.path.join(d, n), "rb") as f:
                    obj = json.loads(f.read().decode("utf-8"))
                item["cameras"] = len(obj.get("cameras", {}))
                item["savedAt"]  = obj.get("savedAt", "")
            except (ValueError, OSError):
                item["cameras"] = None
            out.append(item)
        return out

    # ── Obsidian 联动 ──
    def obsidian_path(self):
        """生成物（胶卷库存清单.md）的路径。从环境变量来 —— **绝不能硬编码进仓库**：
        这个仓库是公开的，路径本身就在讲谁的 NAS 怎么摆的。"""
        return (os.environ.get("FT_OBSIDIAN") or "").strip()

    def obsidian_ledger_path(self):
        """账本（胶卷账本.md）的路径 —— 唯一真源。同样只从环境变量来。
        两个都设齐联动才启用：只有账本没清单就没地方写生成物，只有清单没账本就无从重算。"""
        return (os.environ.get("FT_OBSIDIAN_LEDGER") or "").strip()

    def obsidian_shots_path(self):
        """拍摄记录（胶卷拍摄记录.md）的路径。**这个是可选的** ——
        没设就跳过这一块，其余同步照常；设了但读不懂才会报错。
        不设它整块停用，是为了让老部署升级上来的时候不至于因为少一个变量就全废。"""
        return (os.environ.get("FT_OBSIDIAN_SHOTS") or "").strip()

    def read_baseline(self):
        b = self._read_json(self.p(OBSIDIAN_BASELINE), {}) or {}
        return b if isinstance(b, dict) else {}

    def write_baseline(self, obj):
        self._write_json(self.p(OBSIDIAN_BASELINE), obj, 0o600)

    def _write_text_atomic(self, path, text):
        """同目录临时文件 → fsync → os.replace，和别的写盘一个规矩；
        但**保留原文件的权限位** —— 那是用户的笔记，别因为我们碰过一次就改掉它。

        `O_BINARY` 这一位不能省：Windows 上 `os.open` 默认是文本模式，
        会把写出的每个 "\\n" 再补一个 "\\r"，于是笔记里原有的 CRLF 变成 CRLFCRLF ——
        整份文件被撑花。容器跑在 Linux 上不受影响，但「逐字节保留」是这个功能
        对用户笔记的核心承诺，不该只在某一个平台上成立。
        （Linux 上没有 O_BINARY 这个常量，取 0 即等于不加。）"""
        try:
            mode = os.stat(path).st_mode & 0o777
        except OSError:
            mode = 0o644
        tmp = path + ".filmtap-tmp"
        data = text.encode("utf-8")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                     | getattr(os, "O_BINARY", 0), mode)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)

    def _backup_vault_file(self, path):
        """回写前把原文留一份。出问题时这是唯一能整份还原的东西。"""
        d = self.p(OBSIDIAN_BACKUP_DIR)
        os.makedirs(d, mode=0o700, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dst = os.path.join(d, "%s-%s" % (stamp, os.path.basename(path)))
        try:
            shutil.copyfile(path, dst)
            os.chmod(dst, 0o600)
        except OSError:
            return None
        names = sorted(n for n in os.listdir(d))
        for n in names[:-OBSIDIAN_BACKUP_KEEP]:
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass
        return dst

    def obsidian_sync(self, dry=False, today=""):
        """读账本 → 重算库存 → 追加 App 侧变动的流水 →（非 dry 时）写 data.json
        + 回写清单 + 追加流水到账本 + 推基线。

        写入顺序刻意是「data.json → 清单 → 账本 → 基线」：基线只有在前面都成了之后，
        才代表「已经同步到的状态」。中间任何一步失败都**不推进基线**，而追加量是
        `F - L` 这种幂等的式子 —— 重放一次算出来是 0，不会重复扣减。

        返回 (状态, 详情)。状态：
          not_configured / no_file / parse_failed / blocked / write_failed /
          partial（App 写成了但回写笔记失败）/ ok
        """
        path = self.obsidian_path()
        ledger = self.obsidian_ledger_path()
        if not path or not ledger:
            return "not_configured", {}
        for target, which in ((ledger, "ledger"), (path, "list")):
            if not os.path.isfile(target):
                return "no_file", {"path": target, "which": which}

        with self.lock:
            try:
                with open(ledger, "rb") as f:
                    led_raw = f.read()
            except OSError:
                return "no_file", {"path": ledger, "which": "ledger"}
            led = parse_ledger(led_raw.decode("utf-8", "replace"))
            if not led["ok"]:
                return "parse_failed", {"path": ledger, "which": "ledger",
                                        "reason": led["reason"]}

            try:
                with open(path, "rb") as f:
                    list_raw = f.read()
            except OSError:
                return "no_file", {"path": path, "which": "list"}
            lst = parse_film_table(list_raw.decode("utf-8", "replace"))
            if not lst["ok"]:
                return "parse_failed", {"path": path, "which": "list",
                                        "reason": lst["reason"]}

            # 拍摄记录是**可选**的一块：没设 FT_OBSIDIAN_SHOTS 就跳过。
            # 设了就得能读 —— 读不到宁可报出来，也别让「退了卷却没记录」静悄悄发生。
            shots_path = self.obsidian_shots_path()
            shots = None
            if shots_path:
                if not os.path.isfile(shots_path):
                    return "no_file", {"path": shots_path, "which": "shots"}
                try:
                    with open(shots_path, "rb") as f:
                        shots_raw = f.read()
                except OSError:
                    return "no_file", {"path": shots_path, "which": "shots"}
                shots = parse_shots_table(shots_raw.decode("utf-8", "replace"))
                if not shots["ok"]:
                    return "parse_failed", {"path": shots_path, "which": "shots",
                                            "reason": shots["reason"]}

            cur_raw, _ = self.read_data()
            db = {}
            if cur_raw:
                try:
                    db = json.loads(cur_raw.decode("utf-8"))
                except ValueError:
                    db = {}
            if not isinstance(db, dict):
                db = {}
            films = db.get("films") if isinstance(db.get("films"), dict) else {}
            cameras = db.get("cameras") if isinstance(db.get("cameras"), dict) else {}

            # 基线记的是「上次同步时每一款的 u（账本的人侧存量）」。换了文件就作废；
            # 老基线（还没有 ubase 字段的那版）**故意继续沿用** —— 升级那一下漏掉 App
            # 侧的扣减比什么都糟，而旧的 counts 字段正好也能当 u 用（当时没有自动行）。
            base = self.read_baseline()
            if base.get("file") != path:
                base = {}
            elif base.get("ledger") and base["ledger"] != ledger:
                base = {}
            ubase = base.get("ubase")
            if not isinstance(ubase, dict):
                ubase = base.get("counts") if isinstance(base.get("counts"), dict) else {}

            begin_text = "".join(led["lines"][led["begin_range"][0]:led["begin_range"][1]])
            begin_hash = sha16(begin_text.encode("utf-8"))

            # 护栏。两条都只拒绝、不动任何数据。
            blocked = None
            prev_flow = base.get("flowRows")
            if (isinstance(prev_flow, int) and prev_flow > 0
                    and len(led["flow_rows"]) < prev_flow * 0.5):
                blocked = ("账本「## 流水」的行数从 %d 掉到了 %d，看着像被写坏了 —— "
                           "这次没动任何数据" % (prev_flow, len(led["flow_rows"])))
            if blocked is None and base.get("beginHash") and base["beginHash"] != begin_hash:
                blocked = ("账本「## 期初」被改过了（它是冻结快照，本就不该动）—— "
                           "这次没动任何数据。真要改库存，请往「## 流水」追加一条「盘点修正」。")

            ledger_rows, _ = compute_stock(led["begin_rows"], led["flow_rows"])
            u_by_key = {r["key"]: r["u"] for r in ledger_rows}
            planned, appended, warnings = plan_app_flows(
                films, ubase, ledger_rows,
                dates=loaded_dates(cameras),
                today=today or datetime.now().strftime("%Y-%m-%d"))

            # 清单侧要改哪些格子：款式集合没变就只改数字，变了才整段重建。
            by_key = {p["key"]: p for p in planned}
            listed = {}
            for r in lst["rows"]:
                listed[film_key(r["stock"], r["format"])] = r
            same_keys = set(by_key) == set(listed)
            list_writes = []
            if same_keys:
                list_writes = [(r["line_index"], r["cell"][0], r["cell"][1], by_key[k]["count"])
                               for k, r in listed.items() if r["count"] != by_key[k]["count"]]

            # 拍摄记录：把「已经退卷」的卷补进去。去重两条 —— ① 基线里的自然键
            # （机身 id 参与的，扛得住改名）② 表里已有同样的内容（指纹不含机身名，
            # 所以基线丢了也不会因为改过名就把老记录重写一遍）。
            # 两条都在，所以重放一次算出来是空的，不需要额外的幂等技巧。
            shots_new, shots_dup = [], 0
            shots_seen = set(base.get("shots") or [])
            if shots:
                cam_col = shots["colmap"].get("camera")
                batch = set()
                for s in shot_rows_from_cameras(cameras):
                    cells = shot_cells(s["fields"], shots["colmap"], shots["ncols"])
                    fp = shot_fingerprint(cells, cam_col)
                    if s["key"] in shots_seen or fp in shots["fingerprints"] or fp in batch:
                        shots_dup += 1
                        continue
                    batch.add(fp)
                    shots_new.append({"key": s["key"], "cells": cells,
                                      "camera": s["fields"]["camera"],
                                      "stock": s["fields"]["stock"],
                                      "unloaded": s["fields"]["unloaded"]})

            no_qty = [r for r in led["flow_rows"] if r["qty"] is None]
            if no_qty:
                warnings.append("账本「## 流水」里有 %d 行没写数量（第 %s 行），"
                                "它们没被计入库存"
                                % (len(no_qty), "、".join(str(r["line"]) for r in no_qty)))
            if not shots_path:
                warnings.append("没配拍摄记录笔记（部署时设 FT_OBSIDIAN_SHOTS），"
                                "「拍完换卷」这一块这次跳过了")

            summary = {
                "path": path,
                "ledger": ledger,
                "beginRows": len(led["begin_rows"]),
                "beginTotal": sum(r["count"] for r in led["begin_rows"]),
                "flowRows": len(led["flow_rows"]),
                "historyRows": len(led["history_rows"]),
                "stockTotal": sum(p["count"] for p in planned),
                "appended": appended,
                "shots": shots_new,
                "shotsPath": shots_path,
                "shotsDup": shots_dup,
                "recalc": [{"key": p["key"], "stock": p["stock"], "format": p["format"],
                            "was": p["was"], "now": p["count"]}
                           for p in planned
                           if p["was"] != p["count"]
                           and not (p["was"] is None and p["count"] == 0)],
                "listWrites": len(list_writes) if same_keys else "rebuild",
                "skipped": led["begin_skipped"] + led["flow_skipped"] + lst["skipped"],
                "warnings": warnings,
                "firstSync": not ubase,
                "at": base.get("at") or "",
            }
            if dry:
                summary["blocked"] = blocked
                return "ok", summary
            if blocked:
                summary["blocked"] = blocked
                return "blocked", summary

            # ① 先写 data.json —— 页面直接读这份，先让它落地
            newfilms = dict(films)
            for p in planned:
                if p["was"] is None and p["count"] == 0:
                    # 账本里有这一款、但已经一卷不剩，App 里又没有它 ——
                    # 别凭空造一条空记录。清单里照样会列出它（0 卷）。
                    continue
                rec = dict(newfilms[p["key"]]) if isinstance(newfilms.get(p["key"]), dict) else {}
                rec["id"] = p["key"]
                rec["stock"] = p["stock"]
                rec["format"] = p["format"]
                rec["count"] = p["count"]
                # 其余字段（有效期 / 购入日期 / 单价 / 备注）不动 —— 它们不在同步范围里
                newfilms[p["key"]] = rec
            db["version"] = 2
            db["cameras"] = cameras
            db["films"] = newfilms
            payload = json.dumps(db, ensure_ascii=False, indent=2).encode("utf-8")
            state, info = self.write_data(payload, None, True)
            if state != "ok":
                return "write_failed", {"path": path, "message": "写 data.json 失败：" + state}

            # ② 回写清单（生成物）：款式集合没变只改数字格，变了才整段重建
            new_list = None
            if not same_keys:
                rng = _section_range(lst["lines"], OBSIDIAN_SECTION)
                built = rebuild_stock_table(lst["lines"], rng[0], rng[1], planned) if rng else None
                if built is None:
                    summary.update({"rev": info.get("rev"), "vaultError":
                                    "清单的「## 胶卷列表」结构认不出来，这次没回写它"})
                    return "partial", summary
                new_list = "".join(built)
            elif list_writes:
                new_list = "".join(apply_counts(lst["lines"], list_writes))
            backup = None
            if new_list is not None:
                backup = self._backup_vault_file(path)
                try:
                    self._write_text_atomic(path, new_list)
                except OSError as e:
                    # App 已更新、清单没写成功。**故意不推进基线** ——
                    # 重放会算出同一个结果，不会重复扣减。
                    summary.update({"backup": backup, "rev": info.get("rev"),
                                    "vaultError": "回写清单失败：%s" % e})
                    return "partial", summary

            # ③ 追加流水到账本（唯一真源）。放在清单之后：它记的是「App 侧已经发生的
            #    变动」；它成了而基线没成也不要紧，重放算出来是 0。
            if appended:
                cells = [[a["date"], a["type"], a["stock"], a["format"],
                          "%+d" % a["qty"], "", "", a["note"]] for a in appended]
                led_backup = self._backup_vault_file(ledger)
                try:
                    self._write_text_atomic(
                        ledger, "".join(append_flow_rows(led["lines"], led["flow_last"], cells)))
                except OSError as e:
                    summary.update({"backup": led_backup, "rev": info.get("rev"),
                                    "ledgerError": "追加账本流水失败：%s" % e})
                    return "partial", summary
                backup = backup or led_backup

            # ④ 拍摄记录：只在表尾追加行。它不参与任何累加，去重也不靠基线，
            #    所以这一步失败最多是「下次再补」，不会算错数。
            if shots_new:
                shots_backup = self._backup_vault_file(shots_path)
                try:
                    self._write_text_atomic(shots_path, "".join(append_flow_rows(
                        shots["lines"], shots["last"], [s["cells"] for s in shots_new])))
                except OSError as e:
                    summary.update({"backup": shots_backup, "rev": info.get("rev"),
                                    "shotsError": "追加拍摄记录失败：%s" % e})
                    return "partial", summary
                backup = backup or shots_backup
                shots_seen |= set(s["key"] for s in shots_new)

            # ⑤ 基线最后写：只有前面都成了，它才代表「已经同步到的状态」
            self.write_baseline({
                "file": path,
                "ledger": ledger,
                "at": now_iso(),
                "rows": len(lst["rows"]),
                "flowRows": len(led["flow_rows"]) + len(appended),
                "beginHash": begin_hash,
                "counts": {p["key"]: p["count"] for p in planned},
                "ubase": {p["key"]: u_by_key[p["key"]]
                          for p in planned if p["key"] in u_by_key},
                # 已经写进拍摄记录的那些退卷事件（自然键），扛得住机身改名
                "shots": sorted(shots_seen),
            })
            summary.update({"backup": backup, "rev": info.get("rev"),
                            "vaultError": None, "ledgerError": None, "shotsError": None})
            return "ok", summary


class Handler(BaseHTTPRequestHandler):
    server_version = APP_NAME
    protocol_version = "HTTP/1.1"

    store: Store = None
    app_dir: str = "."
    quiet: bool = False
    assume_https: bool = False

    # ── 日志：只记方法 / 路径 / 状态码。请求体和响应体一概不进日志。 ──
    def log_message(self, fmt, *args):
        if self.quiet:
            return
        sys.stderr.write("%s %s %s\n" % (now_iso(), self.address_string(), fmt % args))

    # ── 基础设施 ──
    def _json(self, code, obj, extra=None):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _cookie(self, token=None):
        """`Secure` 只在真的走 https 时才加。
        后端在反代后面，自己看到的是 http —— 所以只能看反代转发的协议头。
        不加 `Secure` 不会让直连挂掉，加了则公网那条路更严；两边都要能跑。

        /api/health 会把这个判断结果报出来（`https` 字段）：反代到底有没有转发
        协议头，是这台反代的具体行为，猜不出来，只能实测。"""
        parts = ["%s=%s" % (COOKIE_NAME, token or ""), "Path=/", "HttpOnly",
                 "SameSite=Lax", "Max-Age=%d" % (COOKIE_MAX_AGE if token else 0)]
        if self._is_https():
            parts.append("Secure")
        return ("Set-Cookie", "; ".join(parts))

    def _is_https(self):
        """反代有没有告诉我们「外面那一段是 https」。各家用的头不一样，都认一遍。
        实测：Lucky 2.27.2 的反代**一个都不转发**，所以这种部署要显式加
        `--assume-https`，不能指望猜出来。"""
        if Handler.assume_https:
            return True
        if self.headers.get("X-Forwarded-Proto", "").lower() == "https":
            return True
        if self.headers.get("X-Forwarded-Ssl", "").lower() == "on":
            return True
        if self.headers.get("X-Url-Scheme", "").lower() == "https":
            return True
        if "proto=https" in self.headers.get("Forwarded", "").lower():
            return True
        return False

    def _token(self):
        raw = self.headers.get("Cookie", "") or ""
        for piece in raw.split(";"):
            k, _, v = piece.strip().partition("=")
            if k == COOKIE_NAME:
                return v
        return ""

    def _authed(self):
        return self.store.has_session(self._token())

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n <= 0 or n > MAX_BODY:
            return None
        return self.rfile.read(n)

    def _client_ip(self):
        """登录失败计数用的「客户端身份」。

        ⚠️ **绝对不能信 `X-Forwarded-For`。**
        这个头是客户端可以随便写的。用它来分桶的话，攻击者只要每次换一个假的
        XFF 值，就能让每个桶都是新的、都是空的，于是 `LOGIN_MAX_FAIL` 永远
        到不了 —— 限速等于不存在，密码可以无限猜。这是实测确认过的：
        同一台机器带 20 个不同的伪造 XFF 打 20 次，一次都没被限速。

        反代（Lucky）是跑在 NAS **本机**上的，容器端口只绑 127.0.0.1，
        所以 `client_address[0]` 必然就是那台反代的回环地址 —— 它不区分
        不同的外部访客，但**它伪造不了**。宁可所有人都挤在一个桶里
        （真实意图是「挡住暴力猜测」而不是「按人限速」），也不要一个
        可以被绕过的桶。

        有个副作用要知道：反代和容器在同一台机器上，所以所有人共用
        127.0.0.1 这一个桶 —— 8 次失败就 429 五分钟。对一个只有自己用的
        家庭服务来说这是**期望的行为**（本来就不该有人在猜你的密码）；
        万一自己手滑输错被锁，等五分钟，或者重启容器清掉内存里的计数。"""
        return self.client_address[0]

    # ── Obsidian 联动的响应外壳 ──
    def _obsidian_body(self, state, info):
        """把 (状态, 详情) 拼成给前端的一句话 + 结构化字段。
        中文提示只在这里生成一处，预览和执行两条路共用，措辞不会两边打架。"""
        info = info or {}
        appended = info.get("appended") or []
        shots = info.get("shots") or []
        recalc = info.get("recalc") or []
        blocked = info.get("blocked")
        n_consume = sum(1 for a in appended if (a.get("qty") or 0) < 0)
        which = {"ledger": "账本", "shots": "拍摄记录"}.get(info.get("which"), "清单")

        if state == "not_configured":
            msg = ("这台机器没配 Obsidian 联动（部署时要挂载 vault，"
                   "并设 FT_OBSIDIAN 与 FT_OBSIDIAN_LEDGER）")
        elif state == "no_file":
            msg = "找不到%s文件：%s" % (which, info.get("path") or "")
        elif state == "parse_failed":
            msg = "读不懂%s：%s" % (which, info.get("reason") or "")
        elif blocked:
            # 护栏拦下。dry-run 时 state 仍是 ok，所以这里先判 blocked。
            msg = blocked
        elif state == "write_failed":
            msg = str(info.get("message") or "写数据失败")
        elif state == "partial":
            msg = ("库存已更新，但回写笔记失败："
                   + str(info.get("vaultError") or info.get("ledgerError")
                         or info.get("shotsError") or ""))
        else:
            bits = []
            if n_consume:
                bits.append("App 追加 %d 条消耗流水" % n_consume)
            elif appended:
                bits.append("追加 %d 条流水" % len(appended))
            if shots:
                bits.append("拍摄记录追加 %d 行" % len(shots))
            if recalc:
                bits.append("重算后 %d 款有变化" % len(recalc))
            if not bits:
                bits.append("账本与库存已经一致")
            msg = "、".join(bits) + "（期初 %d 卷，流水 %d 行，库存 %d 卷）" % (
                info.get("beginTotal") or 0, info.get("flowRows") or 0,
                info.get("stockTotal") or 0)
        if info.get("firstSync") and state == "ok" and not blocked:
            msg = "首次同步，以账本为准 —— " + msg

        out = dict(info)
        out.update({"ok": state == "ok" and not blocked, "state": state,
                    "configured": state != "not_configured", "message": msg})
        return out

    # ── 路由 ──
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._json(200, {"app": APP_NAME, "ok": True,
                                    "configured": self.store.configured(),
                                    "https": self._is_https()})
        if path == "/api/session":
            # 这里**故意回 200 + {"ok": false}，而不是 401**。
            # 它问的是「现在是谁」，答案可以是「没人」—— 那不是一个错误。
            # 回 401 的话，浏览器会把每次「未登录打开页面」都记成一条
            # console error，而那是完全正常的启动路径，会淹掉真正的报错。
            return self._json(200, {"ok": True} if self._authed() else {"ok": False})
        if path == "/api/data":
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            raw, rev = self.store.read_data()
            if raw is None:
                return self._json(200, {"data": None, "rev": None})
            try:
                data = json.loads(raw.decode("utf-8"))
            except ValueError:
                return self._json(500, {"error": "server data unreadable"})
            return self._json(200, {"data": data, "rev": rev})
        if path == "/api/history":
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            return self._json(200, {"items": self.store.history()})
        if path == "/api/obsidian":
            # dry-run：只读，报「会改什么」，一个字节都不动。
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            state, info = self.store.obsidian_sync(dry=True)
            return self._json(200, self._obsidian_body(state, info))
        if path in ("/", "/index.html", "/sw.js", "/manifest.webmanifest"):
            return self._static("index.html" if path in ("/", "/index.html") else path[1:])
        return self._json(404, {"error": "not found"})

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        ip = self._client_ip()

        if path == "/api/login":
            if not self.store.configured():
                return self._json(503, {"error": "not_configured",
                                        "message": "服务端还没设密码"})
            if self.store.too_many_fails(ip):
                return self._json(429, {"error": "too_many",
                                        "message": "失败次数太多，等几分钟再试"})
            raw = self._body()
            try:
                pw = str(json.loads(raw.decode("utf-8")).get("password") or "")
            except (ValueError, AttributeError, UnicodeDecodeError):
                pw = ""
            if not pw or not self.store.check_password(pw):
                n = self.store.note_fail(ip)
                return self._json(401, {"error": "bad_password",
                                        "message": "密码不对",
                                        "fails": n})
            self.store.clear_fails(ip)
            token = self.store.new_session()
            return self._json(200, {"ok": True}, [self._cookie(token)])

        if path == "/api/logout":
            self.store.drop_session(self._token())
            return self._json(200, {"ok": True}, [self._cookie(None)])

        if path == "/api/obsidian/sync":
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            # 自动追加的流水行要写日期。容器多半跑在 UTC 上，直接用服务端的「今天」
            # 会在晚上差一天，所以让页面把它的本地日期带上来。
            today = ""
            raw = self._body()
            if raw:
                try:
                    today = str(json.loads(raw.decode("utf-8")).get("today") or "")[:10]
                except (ValueError, AttributeError, UnicodeDecodeError):
                    today = ""
            state, info = self.store.obsidian_sync(dry=False, today=today)
            body = self._obsidian_body(state, info)
            # 只有 ok 是 200；没配好是 400（这台机器不该点这个按钮）；
            # 其余（没文件 / 解析失败 / 护栏拦下 / 写盘失败 / 半成功）都是 409：
            # 请求本身没问题，是当前状态不允许它完成。
            code = 200 if state == "ok" else (400 if state == "not_configured" else 409)
            return self._json(code, body)

        return self._json(404, {"error": "not found"})

    def do_PUT(self):
        path = urlparse(self.path).path
        if path != "/api/data":
            return self._json(404, {"error": "not found"})
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})

        raw = self._body()
        if raw is None:
            return self._json(413, {"error": "body too large or missing"})

        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "bad_json"})

        data = body.get("data")
        if not isinstance(data, dict):
            return self._json(400, {"error": "bad_json"})

        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        state, info = self.store.write_data(payload, body.get("rev"), bool(body.get("force")))

        if state == "ok":
            return self._json(200, {"ok": True, "rev": info.get("rev")})
        if state == "stale":
            return self._json(409, {"error": "stale", "rev": info.get("rev"),
                                    "message": "数据在别处被改过，先拉一次再传"})
        if state == "not_empty_overwrite":
            return self._json(409, {"error": "not_empty_overwrite",
                                    "remote": info.get("remote"),
                                    "message": "服务端有 %d 台机身，这份是空的 —— 先拉一次对齐"
                                               % info.get("remote", 0)})
        return self._json(400, {"error": "bad_json"})

    # ── 静态 ──
    def _static(self, name):
        ctype = STATIC.get(name)
        path = os.path.join(self.app_dir, name)
        if not ctype or not os.path.isfile(path):
            return self._json(404, {"error": "not found", "file": name})
        with open(path, "rb") as f:
            raw = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        # ⚠️ 必须 no-cache：页面是「容器活着才有」的东西 —— 容器停了、反代 502，
        #    浏览器不该还能从自己的缓存里把这一屏端出来（那正是「服务挂了却
        #    看起来一切正常」的来源）。每次都要回来问服务端一次。
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)


def env_flag(name, default=False):
    """把环境变量当开关读。空串 / 0 / false / no / off 都算「关」。

    镜像里就靠这个把配置带进来 —— 这样 `docker run` 不用在命令行上
    堆一串参数，重新部署时也少一处会写错的地方。"""
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


def main():
    ap = argparse.ArgumentParser(
        description="film-tap 自建后端（命令行参数优先于 FT_* 环境变量）")
    ap.add_argument("--data", default=os.environ.get("FT_DATA", "/data"),
                    help="数据目录（放 data.json / config.json / history/）")
    ap.add_argument("--app",  default=os.environ.get("FT_APP", "/app"),
                    help="静态文件目录（放 index.html / sw.js）")
    ap.add_argument("--port", type=int, default=int(os.environ.get("FT_PORT", "8300")))
    ap.add_argument("--bind", default=os.environ.get("FT_BIND", "0.0.0.0"))
    ap.add_argument("--set-password", action="store_true",
                    help="设置或修改密码后退出（交互式读入，不经过命令行参数，免得进 ps）")
    ap.add_argument("--quiet", action="store_true", default=env_flag("FT_QUIET"),
                    help="不打访问日志")
    ap.add_argument("--no-quiet", dest="quiet", action="store_false",
                    help="显式打开日志（覆盖 FT_QUIET）")
    ap.add_argument("--assume-https", action="store_true", default=env_flag("FT_ASSUME_HTTPS"),
                    help="外面那一段一定是 https（前面有终结 TLS 的反代）→ 会话 cookie 带上 Secure。"
                         "反代不转发协议头时必须显式打开，猜不出来")
    ap.add_argument("--no-assume-https", dest="assume_https", action="store_false",
                    help="显式关掉（覆盖 FT_ASSUME_HTTPS）。只有直连 http 调试时才用得到")
    args = ap.parse_args()

    log = lambda *a: sys.stderr.write("%s %s\n" % (now_iso(), " ".join(str(x) for x in a)))
    store = Store(args.data, log)

    if args.set_password:
        if sys.stdin.isatty():
            p1 = getpass.getpass("新密码：")
            p2 = getpass.getpass("再输一次：")
        else:
            p1 = sys.stdin.readline().rstrip("\n")
            p2 = p1
        if not p1:
            print("密码不能为空", file=sys.stderr)
            return 2
        if p1 != p2:
            print("两次输入不一致", file=sys.stderr)
            return 2
        store.set_password(p1)
        print("密码已更新（%s）" % os.path.join(args.data, CONFIG_FILE))
        return 0

    if not store.configured():
        log("⚠️  还没设密码 —— 先跑一次 `--set-password`，否则登录一定失败")

    Handler.store   = store
    Handler.app_dir = args.app
    Handler.quiet   = args.quiet
    Handler.assume_https = args.assume_https

    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    srv.daemon_threads = True
    log("%s 起在 %s:%d，静态目录 %s，数据目录 %s，Secure cookie %s"
        % (APP_NAME, args.bind, args.port, args.app, args.data,
           "开" if args.assume_https else "关"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("收到中断，退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
