#!/usr/bin/env bash
#
# 把 film-tap 部署到自己的 NAS 上（飞牛 fnOS，或任何装了 Docker 的 Linux）。
#
#   NAS_HOST=<你的 NAS 地址> NAS_USER=<用户> SSH_PORT=<SSH 端口> ./nas/deploy.sh
#
# 它做的事：拉镜像 → 起容器（只挂 data 卷）→ 设密码 → 起 watchtower 自动更新。
# **不再往 NAS 上传任何源码** —— 代码在镜像里，镜像由 GitHub Actions 构建。
#
# 环境变量（除 NAS_HOST 外都有默认值）：
#   NAS_HOST             必填。NAS 的地址
#   NAS_USER             SSH 用户，默认当前用户
#   SSH_PORT             SSH 端口，默认 22
#   BASE                 部署根目录，默认 /vol1/@appdata/film-tap（只用来放 data）
#   PORT                 对外端口，默认 8300。容器内部固定 8300，这里只映射
#   UID_GID              容器以哪个 uid:gid 跑，默认 1000:1001（数据文件归你，不归 root）
#   IMAGE                镜像，默认 docker.io/liritian/film-tap:latest
#   FT_PASSWORD          只在**首次**初始化密码时用；不设就自己进去跑一次 --set-password
#   VAULT_DIR            可选。NAS 上**放着清单笔记的那一层目录** —— 默认布局是
#                        50_Assets，不是 vault 根（填 vault 根会找不到笔记）。
#                        只挂这一层就够，容器拿到的写权限越小越好。
#                        设了才把「从 Obsidian 同步」打通：目录挂到 /vault（读写），
#                        并把笔记路径经 FT_OBSIDIAN 传进容器。不设就整块停用
#   VAULT_FILE           可选。笔记在 VAULT_DIR 里的相对路径，默认 胶卷库存清单.md
#   WATCHTOWER           yes | no，默认 yes。no 就只部署不装自动更新
#   WATCHTOWER_IMAGE     默认 nickfedor/watchtower:latest
#   WATCHTOWER_INTERVAL  轮询间隔秒数，默认 3600
#
# ── 三条刻意的设计 ──
#
# ① 这个文件会出现在公开仓库里，所以**地址、端口、密码一个都不写死在脚本里**，
#    全部从环境变量进来。源码里出现真实凭据是这个项目踩过的坑，
#    `_selftest.js` 的护栏会扫这个文件。
#
# ② 容器端口**只绑 127.0.0.1**，不绑 0.0.0.0。后端因此不进局域网 —— 外面一律
#    走反代。多一层还是少一层，区别是「局域网上任何一台设备都能直接打这个端口」
#    和「只有本机能打」。反代在本机，所以它连得上。
#
# ③ 自动更新用 watchtower，但**限定它只管打了标签的容器**
#    （--label-enable + com.centurylinklabs.watchtower.enable）。
#    不加这一条的话，它会顺手去重建 NAS 上所有别的容器 —— 那些是别人配的。
#    ⚠️ watchtower 要挂 /var/run/docker.sock，等同于把 NAS 的 Docker 控制权
#    交给这个容器。不想要这条通道就把 WATCHTOWER=no，改用 nas/update.sh 手动更新。
#
# 回滚：docker rm -f film-tap watchtower && rm -rf "$BASE"
#       这个脚本没有碰过 NAS 上任何别的东西，反代那条规则在管理界面上删掉即可。
set -euo pipefail

need(){ command -v "$1" >/dev/null 2>&1 || { echo "缺少 $1" >&2; exit 1; }; }
need ssh

: "${NAS_HOST:?请先给 NAS_HOST，例如 NAS_HOST=nas.example.lan NAS_USER=someone SSH_PORT=22 ./nas/deploy.sh}"
NAS_USER="${NAS_USER:-$(id -un)}"
SSH_PORT="${SSH_PORT:-22}"
BASE="${BASE:-/vol1/@appdata/film-tap}"
PORT="${PORT:-8300}"
UID_GID="${UID_GID:-1000:1001}"
IMAGE="${IMAGE:-docker.io/liritian/film-tap:latest}"
NAME="film-tap"
WATCHTOWER="${WATCHTOWER:-yes}"
WATCHTOWER_IMAGE="${WATCHTOWER_IMAGE:-nickfedor/watchtower:latest}"
WATCHTOWER_INTERVAL="${WATCHTOWER_INTERVAL:-3600}"

# 容器里固定监听 8300（镜像里的 FT_PORT），外面看到的端口由 PORT 决定。
CONTAINER_PORT=8300

# Obsidian 联动：只有给了 VAULT_DIR 才挂 /vault 并传 FT_OBSIDIAN。
# 拼成一个字符串（不用数组）是为了在 set -u 下空展开也安全，也跟本文件
# 「单引号嵌在 ssh 双引号里」的写法一致。
VAULT_MOUNT=""
if [ -n "${VAULT_DIR:-}" ]; then
  VAULT_FILE="${VAULT_FILE:-胶卷库存清单.md}"
  VAULT_MOUNT="-v '${VAULT_DIR}:/vault:rw' -e FT_OBSIDIAN='/vault/${VAULT_FILE}'"
fi

SSH=(ssh -p "${SSH_PORT}" "${NAS_USER}@${NAS_HOST}")

echo "→ 目标 ${NAS_USER}@${NAS_HOST}:${SSH_PORT}"
echo "  根目录 ${BASE}   对外 ${PORT} → 容器 ${CONTAINER_PORT}（只绑回环）"
echo "  镜像 ${IMAGE}"
if [ -n "${VAULT_DIR:-}" ]; then
  echo "  Obsidian：${VAULT_DIR}/${VAULT_FILE} → /vault（容器内读写，只挂这一层）"
else
  echo "  Obsidian：没设 VAULT_DIR，这一块停用"
fi

echo "→ 拉镜像"
# --pull=always 是给 :latest 用的。不加它、或者用 docker start，
# 都会让「更新」悄悄变成「什么都没做」—— 这是自动更新最常见的假成功。
"${SSH[@]}" "docker pull ${IMAGE}"

echo "→ 建目录"
"${SSH[@]}" "mkdir -p '${BASE}/data' '${BASE}/data/history'"

# 老形态把源码挂在 ${BASE}/app 上。代码现在进镜像了，那个目录只剩一份
# 会和镜像漂移的旧副本 —— 留着它，下次排查问题时第一件事就得先问
# 「NAS 上那份到底是哪一版」。确认是空壳后删掉。
"${SSH[@]}" "if [ -d '${BASE}/app' ]; then
    if [ -f '${BASE}/app/server.py' ]; then
      echo '   删掉旧的 app/ 目录（源码已进镜像）'
      rm -rf '${BASE}/app'
    fi
  fi"

echo "→ 起容器"
"${SSH[@]}" "docker rm -f ${NAME} >/dev/null 2>&1 || true"
"${SSH[@]}" "docker run -d --name ${NAME} --restart unless-stopped -u ${UID_GID} \
  -p 127.0.0.1:${PORT}:${CONTAINER_PORT} \
  -v '${BASE}/data:/data' \
  ${VAULT_MOUNT} \
  --label com.centurylinklabs.watchtower.enable=true \
  ${IMAGE} >/dev/null"

if [ -n "${FT_PASSWORD:-}" ]; then
  echo "→ 初始化密码"
  # 非交互模式下 --set-password 只读一行，然后自己把它当成两边一致
  printf '%s\n' "${FT_PASSWORD}" \
    | "${SSH[@]}" "docker exec -i ${NAME} python3 /app/server.py --set-password"
else
  echo "→ 没给 FT_PASSWORD。密码要你自己设（不经过命令行参数，不会进 ps）："
  echo "   ssh -p ${SSH_PORT} -t ${NAS_USER}@${NAS_HOST} 'docker exec -it ${NAME} python3 /app/server.py --set-password'"
fi

if [ "${WATCHTOWER}" = "yes" ]; then
  echo "→ 起 watchtower（每 ${WATCHTOWER_INTERVAL} 秒查一次，只重建打了标签的容器）"
  "${SSH[@]}" "docker rm -f watchtower >/dev/null 2>&1 || true"
  "${SSH[@]}" "docker run -d --name watchtower --restart unless-stopped \
    -v /var/run/docker.sock:/var/run/docker.sock \
    ${WATCHTOWER_IMAGE} \
    --label-enable --cleanup --interval ${WATCHTOWER_INTERVAL} >/dev/null"
else
  echo "→ WATCHTOWER=no：跳过自动更新。想更新时跑 ./nas/update.sh"
fi

sleep 1
echo "→ 探活"
"${SSH[@]}" "curl -sS -m 5 http://127.0.0.1:${PORT}/api/health" && echo
echo
echo "容器起来了。反代那边把域名指到  http://127.0.0.1:${PORT}"
echo "以后项目更新：GitHub 自动出镜像，watchtower 自己拉。想立刻更新跑 ./nas/update.sh"
