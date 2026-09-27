#!/usr/bin/env bash
#
# 立刻让 NAS 更新一次（不等 watchtower 那个轮询间隔）。
#
#   NAS_HOST=<你的 NAS 地址> NAS_USER=<用户> SSH_PORT=<SSH 端口> ./nas/update.sh
#
# 它只是拿 watchtower 的 --run-once 跑一遍：查 Docker Hub、有新镜像就重建容器。
# 所以**就算 deploy 时用了 WATCHTOWER=no**（不想把 docker.sock 交给
# 常驻容器），这个脚本照样能用 —— 它是一次性的，跑完就退。
#
# 更新不会碰数据：data.json / config.json / sessions.json / history 都在
# ${BASE}/data 这个卷里，容器换了卷不动。所以更新之后不用重新登录。
#
# 环境变量：NAS_HOST（必填）、NAS_USER、SSH_PORT、WATCHTOWER_IMAGE、WATCHTOWER_INTERVAL
set -euo pipefail

need(){ command -v "$1" >/dev/null 2>&1 || { echo "缺少 $1" >&2; exit 1; }; }
need ssh

: "${NAS_HOST:?请先给 NAS_HOST，例如 NAS_HOST=nas.example.lan ./nas/update.sh}"
NAS_USER="${NAS_USER:-$(id -un)}"
SSH_PORT="${SSH_PORT:-22}"
NAME="film-tap"
WATCHTOWER_IMAGE="${WATCHTOWER_IMAGE:-nickfedor/watchtower:latest}"

SSH=(ssh -p "${SSH_PORT}" "${NAS_USER}@${NAS_HOST}")

echo "→ 更新前："
"${SSH[@]}" "docker image inspect --format '  当前镜像 {{.Id}}（{{.Created}}）' \$(docker inspect -f '{{.Image}}' ${NAME}) 2>/dev/null || echo '  （取不到，容器可能不在）'"

echo "→ 让 watchtower 跑一遍（--run-once）"
# 用一次性容器而不是 docker exec：不依赖 deploy 时到底起没起常驻的 watchtower。
# --label-enable 和 deploy 里保持一致 —— 只管我们打了标签的那一个容器。
"${SSH[@]}" "docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  ${WATCHTOWER_IMAGE} --run-once --cleanup --label-enable"

echo "→ 更新后："
"${SSH[@]}" "docker inspect --format '  容器状态 {{.State.Status}}，用的镜像 {{.Image}}' ${NAME}
  curl -sS -m 5 http://127.0.0.1:8300/api/health && echo"
