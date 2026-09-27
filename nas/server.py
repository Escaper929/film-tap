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
import secrets
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


class Store:
    """data 目录里的一切都在这里。所有写操作走同一把锁。"""

    def __init__(self, data_dir, log):
        self.dir = data_dir
        self.log = log
        self.lock = threading.RLock()
        os.makedirs(self.dir, mode=0o755, exist_ok=True)
        os.makedirs(os.path.join(self.dir, HISTORY_DIR), mode=0o755, exist_ok=True)
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
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
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
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
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
        # Lucky 反代会带 X-Forwarded-For；取第一段
        fwd = self.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return self.client_address[0]

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
