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


# ══════════════ Obsidian 联动：解析 / 合并 / 回写 ══════════════
#
# 只碰「## 胶卷列表」那一张表的三列：品牌/型号、格式、数量。
# 其它列（序号 / 类型 / ISO / 状态 / 购买时间 / 购买价格 / 备注）和
# 其它几个板块（出售记录 / 拍摄记录 / 库存统计 / 冲扫店）**一个字都不动** ——
# 那一整份是人手写的账本，回写必须只改一个数字，其余逐字节保留。
#
# 数量用「带基线的增量」合并：
#   基线 B = 上次同步时的剩余数；Obsidian 现值 O；App 现值 A。
#   新值 = B + (O - B) + (A - B)
# 买卷（Obsidian 那头 +2）和装卷（App 这头 -1）都是**加法事件**，
# 于是两边同时改也**不冲突**，不需要「谁赢」这种规则。
# 没有基线（首次同步 / 基线丢了）时一律以 Obsidian 为准，并如实标出来。

OBSIDIAN_SECTION = "胶卷列表"
FORMATS = ("135", "120", "110", "大画幅")

# 表头别名。比前端的 IMPORT_COLS 窄：这里**故意不收「类型」** ——
# 本文件里「类型」是独立的一列（电影卷 / 彩色负片 / 黑白负片），
# 把「类型」当成型号别名会把它错认成型号列。
OBS_COLS = {
    "stock":  ("品牌/型号", "品牌型号", "型号", "胶卷型号", "型号名称", "胶卷", "名称", "品名", "film", "stock"),
    "count":  ("数量", "卷数", "剩余", "剩余卷数", "存量", "库存", "qty", "count", "rolls"),
    "format": ("格式", "画幅", "规格", "幅面", "size", "format"),
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


def map_columns(headers):
    """识别表头 → {stock:1, count:5, format:4}。
    必须整表精确匹配一遍、再整表包含匹配一遍 —— 合成一趟的话，
    「品牌/型号」会被先命中的「型号」抢走。与前端 mapImport 同一套两趟法。"""
    norms = [_norm_head(h) for h in headers]
    out = {}
    for loose in (False, True):
        for i, n in enumerate(norms):
            if not n:
                continue
            for key, aliases in OBS_COLS.items():
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


def parse_film_table(text):
    """解析「## 胶卷列表」里的那张表。
    → {ok, reason?, lines, rows, skipped}；rows 里带上回写要用的行号与单元格偏移。"""
    lines = text.splitlines(True)
    if not lines:
        return {"ok": False, "reason": "文件是空的"}

    head = re.compile(r"^##\s+" + re.escape(OBSIDIAN_SECTION) + r"\s*$")
    start = None
    for i, ln in enumerate(lines):
        if head.match(ln.rstrip("\r\n").strip()):
            start = i + 1
            break
    if start is None:
        return {"ok": False, "reason": "没找到「## %s」这一节" % OBSIDIAN_SECTION}

    end = len(lines)
    for j in range(start, len(lines)):
        if re.match(r"^##\s+", lines[j].rstrip("\r\n").strip()):
            end = j
            break

    hdr = None
    for j in range(start, end):
        s = lines[j].rstrip("\r\n").strip()
        if s.startswith("|") and s.count("|") >= 2:
            hdr = j
            break
    if hdr is None:
        return {"ok": False, "reason": "「%s」这一节里没有表格" % OBSIDIAN_SECTION}

    table = []
    for j in range(hdr, end):
        if lines[j].rstrip("\r\n").strip().startswith("|"):
            table.append(j)
        else:
            break

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
        # 修「|| 7 |」这种多出来的空首格 —— 真实文件里就有两行是这样。
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

    if not rows:
        return {"ok": False, "reason": "「%s」的表里一行数据都没解析出来" % OBSIDIAN_SECTION}

    return {"ok": True, "lines": lines, "rows": rows, "skipped": skipped}


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


def merge_films(rows, films, baseline):
    """带基线的增量合并 → (changes, unmatched, warnings)。

    同一款（型号@画幅）两边都改过也不冲突：买卷是 +、装卷是 -，加在一起就是净变化。
    没有基线时以 Obsidian 为准，并把 basis 标成 "obsidian"，让调用方如实告诉用户。
    """
    changes, warnings = [], []
    seen = set()
    for r in rows:
        key = film_key(r["stock"], r["format"])
        seen.add(key)
        prev = films.get(key)
        old = int(prev.get("count") or 0) if isinstance(prev, dict) else None
        obs = int(r["count"])

        B = baseline.get(key)
        if isinstance(B, int) and old is not None:
            new = B + (obs - B) + (old - B)      # = old + (obs - B)
            basis = "merge"
        else:
            new = obs
            basis = "obsidian"

        overflow = 0
        if new < 0:
            overflow, new = -new, 0
            warnings.append("%s：合并后是负数，按 0 计（多扣了 %d 卷）" % (r["stock"], overflow))

        changes.append({
            "key": key, "stock": r["stock"], "format": r["format"],
            "old": old, "obs": obs, "new": new, "basis": basis, "overflow": overflow,
            "action": "new" if old is None else ("change" if old != new else "same"),
            "line": r["line"], "line_index": r["line_index"], "cell": r["cell"],
        })

    unmatched = sorted(k for k in films if k not in seen)
    return changes, unmatched, warnings


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
        """vault 里的那个文件路径。从环境变量来 —— **绝不能硬编码进仓库**：
        这个仓库是公开的，路径本身就在讲谁的 NAS 怎么摆的。"""
        return (os.environ.get("FT_OBSIDIAN") or "").strip()

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

    def obsidian_sync(self, dry=False):
        """读 vault → 合并 →（非 dry 时）写 data.json + 回写 md + 更新基线。

        返回 (状态, 详情)。状态：
          not_configured / no_file / parse_failed / blocked / write_failed /
          partial（App 写成了但回写 vault 失败）/ ok
        """
        path = self.obsidian_path()
        if not path:
            return "not_configured", {}
        if not os.path.isfile(path):
            return "no_file", {"path": path}

        with self.lock:
            try:
                with open(path, "rb") as f:
                    raw = f.read()
            except OSError:
                return "no_file", {"path": path}
            parsed = parse_film_table(raw.decode("utf-8", "replace"))
            if not parsed["ok"]:
                return "parse_failed", {"path": path, "reason": parsed["reason"]}

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

            base = self.read_baseline()
            if base.get("file") != path:            # 换了文件，旧基线作废
                base = {}
            baseline = base.get("counts") if isinstance(base.get("counts"), dict) else {}
            prev_rows = base.get("rows")

            # 护栏：行数骤减说明文件多半被写坏了。宁可拒绝，也不要把库存清零。
            blocked = None
            if isinstance(prev_rows, int) and prev_rows > 0 and len(parsed["rows"]) < prev_rows * 0.5:
                blocked = ("解析出的行数从 %d 掉到了 %d，看着像文件被写坏了 —— "
                           "这次没动任何数据" % (prev_rows, len(parsed["rows"])))

            changes, unmatched, warnings = merge_films(parsed["rows"], films, baseline)
            # 要展示 / 要处理的，是这两类里的一种：
            #   · App 侧真变了（action != "same"）
            #   · App 没变，但笔记那一格和合并结果对不上（action == "same" 且 new != obs）
            # 第二类是**最常见**的一类：App 里装了/拍了一卷、笔记还没改。
            # 漏掉它，笔记就永远追不上 App。
            todo = [c for c in changes
                    if c["action"] != "same" or c["new"] != c["obs"]]
            summary = {
                "path": path,
                "rows": len(parsed["rows"]),
                "skipped": parsed["skipped"],
                "firstSync": not baseline,
                "changes": [{k: c[k] for k in ("key", "stock", "format", "old", "new", "obs", "action", "basis")}
                            for c in todo],
                "unchanged": len(changes) - len(todo),
                "unmatched": unmatched,
                "warnings": warnings,
                "at": base.get("at") or "",
            }
            if dry:
                summary["blocked"] = blocked
                return "ok", summary
            if blocked:
                summary["blocked"] = blocked
                return "blocked", summary

            # ① 先写 data.json —— App 是权威的一份，先让它落地
            newfilms = dict(films)
            for c in changes:
                rec = dict(newfilms[c["key"]]) if isinstance(newfilms.get(c["key"]), dict) else {}
                rec["id"] = c["key"]
                rec["stock"] = c["stock"]
                rec["format"] = c["format"]
                rec["count"] = c["new"]
                # 其余字段（有效期 / 购入日期 / 单价 / 备注）不动 —— 它们不在同步范围里
                newfilms[c["key"]] = rec
            db["version"] = 2
            db["cameras"] = cameras
            db["films"] = newfilms
            payload = json.dumps(db, ensure_ascii=False, indent=2).encode("utf-8")
            state, info = self.write_data(payload, None, True)
            if state != "ok":
                return "write_failed", {"path": path, "message": "写 data.json 失败：" + state}

            # ② 回写 vault：哪一格和合并结果对不上就改哪一格，判断只看 new != obs。
            #    遍历 changes 而不是 todo：App 里拍掉一卷后，「App 值」可能和
            #    合并结果正好相等（action 仍是 same），可笔记那一格还是旧数字，
            #    一样得写回去，否则笔记永远追不上。
            writes = [(c["line_index"], c["cell"][0], c["cell"][1], c["new"])
                      for c in changes if c["new"] != c["obs"]]
            backup = None
            if writes:
                backup = self._backup_vault_file(path)
                try:
                    self._write_text_atomic(path, "".join(apply_counts(parsed["lines"], writes)))
                except OSError as e:
                    # App 已更新、Obsidian 没写成功。**故意不更新基线** ——
                    # 增量是可重放的，下次同步会算出同一个结果，不会重复加减。
                    summary.update({"written": 0, "backup": backup, "rev": info.get("rev"),
                                    "vaultError": "回写 Obsidian 失败：%s" % e})
                    return "partial", summary

            # ③ 基线最后写：只有前面都成了，它才代表「已经同步到的状态」
            self.write_baseline({
                "file": path,
                "at": now_iso(),
                "rows": len(parsed["rows"]),
                "counts": {c["key"]: c["new"] for c in changes},
            })
            summary.update({"written": len(writes), "backup": backup,
                            "rev": info.get("rev"), "vaultError": None})
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
        changes = info.get("changes") or []
        n_new = sum(1 for c in changes if c.get("action") == "new")
        n_chg = sum(1 for c in changes if c.get("action") == "change")
        # App 侧没动、只是笔记那一格落后了 —— 要写回笔记，但不算「App 改动」。
        n_back = sum(1 for c in changes
                     if c.get("action") != "new" and c.get("action") != "change")
        blocked = info.get("blocked")

        if state == "not_configured":
            msg = "这台机器没配 Obsidian 同步（部署时要挂载 vault 并设 FT_OBSIDIAN）"
        elif state == "no_file":
            msg = "找不到那个笔记文件：" + str(info.get("path") or "")
        elif state == "parse_failed":
            msg = "读不懂那份笔记：" + str(info.get("reason") or "")
        elif blocked:
            # 护栏拦下。dry-run 时 state 仍是 ok，所以这里先判 blocked。
            msg = blocked
        elif state == "write_failed":
            msg = str(info.get("message") or "写数据失败")
        elif state == "partial":
            msg = "库存已更新，但回写笔记失败：" + str(info.get("vaultError") or "")
        elif not changes:
            msg = "两边已经一致，没有要改的（对上了 %d 行）" % (info.get("unchanged") or 0)
        else:
            bits = []
            if n_new:
                bits.append("新增 %d" % n_new)
            if n_chg:
                bits.append("改动 %d" % n_chg)
            if n_back:
                bits.append("回写笔记 %d" % n_back)
            msg = "、".join(bits) + "，另外 %d 行没变" % (info.get("unchanged") or 0)
        if info.get("firstSync") and state == "ok":
            msg = "首次同步，以 Obsidian 为准 —— " + msg

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
            state, info = self.store.obsidian_sync(dry=False)
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
        # ⚠️ 必须 no-cache：sw.js 就是靠**字节变化**来判断要不要装新 SW 的，
        #    让中间层或浏览器缓存住它，线上就会停在一个旧版本上而且不报错。
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
