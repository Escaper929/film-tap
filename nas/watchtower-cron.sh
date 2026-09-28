#!/usr/bin/env bash
#
# 在 NAS 上装一条 cron：每天跑一次一次性 watchtower，让 film-tap 跟上新镜像。
#
#   NAS_HOST=<你的 NAS 地址> NAS_USER=<SSH 用户> SSH_PORT=<SSH 端口> ./nas/watchtower-cron.sh
#
# 它做两件事：
#   1. 把 nas/filmtap-update.sh 放到 NAS 上那个用户的 home 下（~/.filmtap-update.sh）
#   2. 往 crontab 里加一行（幂等：重复跑不会加第二条，也不碰别的条目）
#
# 和 deploy.sh 里那个**常驻** watchtower 的区别 —— 两种都能用，看你更在意哪头：
#   常驻（deploy.sh WATCHTOWER=yes）：docker.sock 24 小时挂在那容器上，更新最及时。
#   本条（cron 一次性）：socket 只在每天那一小段运行期间暴露，更新最多晚一天。
# deploy.sh 的 WATCHTOWER 默认是 no —— 把 socket 常年交出去这件事，该由你显式点头。
#
# 环境变量（除 NAS_HOST 外都有默认值）：
#   NAS_HOST   必填。NAS 的地址（也可以写 ~/.ssh/config 里的 Host 名）
#   NAS_USER   SSH 用户，默认当前用户
#   SSH_PORT   SSH 端口。默认空 —— 不传就让 ssh 自己去定（~/.ssh/config，否则 22）
#   CRON_SPEC  cron 时间，默认 "30 4 * * *"，即每天 04:30（错开 02:00 的 vault 备份）
#   NAME       容器名，默认 film-tap
#
# 卸载：ssh 上去 `crontab -e` 删掉那一行，再 rm 掉那两个文件。
set -euo pipefail

need(){ command -v "$1" >/dev/null 2>&1 || { echo "缺少 $1" >&2; exit 1; }; }
need ssh
need scp

: "${NAS_HOST:?请先给 NAS_HOST，例如 NAS_HOST=nas.example.lan ./nas/watchtower-cron.sh}"
NAS_USER="${NAS_USER:-$(id -un)}"
SSH_PORT="${SSH_PORT:-}"
CRON_SPEC="${CRON_SPEC:-30 4 * * *}"
NAME="${NAME:-film-tap}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${HERE}/filmtap-update.sh"
[ -f "${SRC}" ] || { echo "找不到 ${SRC}" >&2; exit 1; }

# SSH_PORT 留空就完全不传 -p：这样 NAS_HOST 可以直接写成 ~/.ssh/config 里的
# Host 名，端口和私钥都交给那份配置。**别把某个人的具体端口写进默认值** ——
# 仓库是公开的，写进去等于把部署位置一起公开（_selftest.js 的护栏就拦这个）。
#
# 可选参数用字符串拼、不用数组：set -u 下空数组展开在老 bash 上会报错，
# macOS 自带的 bash 3.2 就是。deploy.sh 里 VAULT_MOUNT 也是同一个理由。
SSH_PORT_OPT=""
[ -n "${SSH_PORT}" ] && SSH_PORT_OPT="-p ${SSH_PORT}"
SCP_PORT_OPT=""
[ -n "${SSH_PORT}" ] && SCP_PORT_OPT="-P ${SSH_PORT}"

TARGET="${NAS_USER}@${NAS_HOST}"

echo "→ 目标 ${TARGET}（SSH 端口 ${SSH_PORT:-由 ssh 决定}）"

echo "→ 取远端 home"
REMOTE_HOME="$(ssh ${SSH_PORT_OPT} "${TARGET}" 'printf %s "$HOME"')"
[ -n "${REMOTE_HOME}" ] || { echo "取不到远端 HOME（连得上吗？）" >&2; exit 1; }

REMOTE_SCRIPT="${REMOTE_HOME}/.filmtap-update.sh"
REMOTE_LOG="${REMOTE_HOME}/.filmtap-update.log"

echo "→ 传 ${SRC##*/} → ${REMOTE_SCRIPT}"
scp ${SCP_PORT_OPT} "${SRC}" "${TARGET}:${REMOTE_SCRIPT}"
ssh ${SSH_PORT_OPT} "${TARGET}" "chmod 700 '${REMOTE_SCRIPT}'"

echo "→ 写 crontab（幂等）"
# 远端整段用 bash -s 从 stdin 读，避免把带重定向的整行塞进 ssh 的参数字符串 ——
# 那样引号会在「本地 shell → ssh → 远端 shell」之间被吃掉一层。
ssh ${SSH_PORT_OPT} "${TARGET}" "bash -s" <<REMOTE
set -euo pipefail
LINE='${CRON_SPEC} ${REMOTE_SCRIPT} >> ${REMOTE_LOG} 2>&1'
TMP="\$(mktemp)"
crontab -l 2>/dev/null | grep -vF '.filmtap-update.sh' > "\${TMP}" || true
printf '%s\n' "\${LINE}" >> "\${TMP}"
crontab "\${TMP}"
rm -f "\${TMP}"
REMOTE

echo "→ 回读 crontab"
ssh ${SSH_PORT_OPT} "${TARGET}" "crontab -l" | sed 's/^/   /'

echo
echo "装好了。想立刻验证一次：ssh 上去跑 bash ${REMOTE_SCRIPT}"
echo "日志在 ${REMOTE_LOG}，注意看结尾那两行镜像 ID 是否一致。"