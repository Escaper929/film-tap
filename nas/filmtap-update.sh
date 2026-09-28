#!/usr/bin/env bash
#
# 让 NAS 上的 film-tap 跟上镜像更新。由 cron 调用，也可以随时手动跑一遍。
#
# 安装到 NAS 上：./nas/watchtower-cron.sh（把这支脚本放到 NAS 用户的 home 下，
# 再往 crontab 里加一行）。装好之后长这样：
#
#   ~/.filmtap-update.sh          这支脚本
#   ~/.filmtap-update.log         cron 重定向过去的日志
#   crontab：30 4 * * * ~/.filmtap-update.sh >> ~/.filmtap-update.log 2>&1
#
# 日志放 home 而不是 vault —— 笔记库每天凌晨会被 git 备份整份提交，日志放进去
# 会一天不落地长在历史里。
#
# 用的是一次性 watchtower：--run-once 跑完就退。docker.sock 只在这一次运行期间
# 被那个容器拿到，不像常驻 watchtower 那样 24 小时挂着 —— 这是「不想把 socket
# 长期交出去」时的折中，代价是更新最多晚一个 cron 周期。
#
# --label-enable = 只管打了 com.centurylinklabs.watchtower.enable 标签的容器。
# 不加这条，它会顺手重建这台 NAS 上所有别的容器 —— 那些是别人配的。
#
# ⚠️ Docker Hub 连不上时 watchtower 会**静默跳过**：退出码是 0、容器不动、也
#    不报错（实测过：`scanned=0 skipped=1 updated=0` 配一条 registry 超时）。
#    所以结尾特意把「容器在用的镜像」和「镜像名现在指向的」都打出来对比 ——
#    两个 ID 不一致就说明这次没更成。否则这条链路坏了你永远看不出来。
set -euo pipefail

NAME="film-tap"
IMAGE="${WATCHTOWER_IMAGE:-nickfedor/watchtower:latest}"

echo "=== $(date '+%F %T') 开始 ==="

# 不用 set -e 在这里直接中断：失败也要把退出码和之后的状态写进日志。
rc=0
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  "${IMAGE}" --run-once --cleanup --label-enable || rc=$?
echo "watchtower 退出码 ${rc}"

echo "--- 之后的状态 ---"
if docker inspect "${NAME}" >/dev/null 2>&1; then
  used="$(docker inspect --format '{{.Image}}' "${NAME}")"
  ref="$(docker inspect --format '{{.Config.Image}}' "${NAME}")"
  now="$(docker image inspect --format '{{.Id}}' "${ref}" 2>/dev/null || echo '(本地没有这个镜像名)')"
  echo "容器在跑：      ${used}"
  echo "${ref} 指向： ${now}"
  if [ "${used}" = "${now}" ]; then
    echo "✅ 容器已是最新"
  else
    echo "⚠️ 容器还在旧镜像上 —— 多半是 Docker Hub 没连上，等下一轮"
  fi
else
  echo "取不到 ${NAME}（容器可能不在）"
fi