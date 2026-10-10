#!/usr/bin/env bash
# FinHOT 服务器上的日常操作。在仓库目录里运行：deploy/finhot.sh <命令>
#
#   init      生成配置 web/.env（问公网 IP 和智谱 API Key，打印管理员密码）
#   up        构建镜像并启动全部服务（第一次大约 10~20 分钟）
#   status    看各服务是否在跑、推送进程最近的情况
#   logs [服务] 跟踪日志，服务名：api worker web pusher db（不写就是全部），Ctrl+C 退出
#   update    拉取最新代码、重新构建、迁移数据库、重启
#   backup    把数据库导出到 backups/
#   restart [服务]  重启
#   stop      停止全部服务（数据保留）
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=web/.env
NPM_REGISTRY=${NPM_REGISTRY:-https://registry.npmmirror.com}
PIP_INDEX_URL=${PIP_INDEX_URL:-https://mirrors.cloud.tencent.com/pypi/simple}

compose() { docker compose --env-file "$ENV_FILE" "$@"; }
need_env() {
  if [ ! -f "$ENV_FILE" ]; then
    echo "还没有 $ENV_FILE，先运行：deploy/finhot.sh init" >&2
    exit 1
  fi
}
build() {
  compose build --build-arg "NPM_REGISTRY=$NPM_REGISTRY" --build-arg "PIP_INDEX_URL=$PIP_INDEX_URL"
}

case "${1:-help}" in
  init)
    shift
    python3 deploy/init_env.py "$@"
    ;;
  up|start)
    need_env
    build
    compose up -d
    echo
    echo "已启动。网站：$(grep -m1 '^SITE_URL=' "$ENV_FILE" | cut -d= -f2-)  后台在 /admin"
    echo "第一次启动后要等几分钟：数据库迁移、导入信源，之后快讯每 90 秒推一轮，模型处理完才会出现在精选里。"
    ;;
  status)
    need_env
    compose ps
    echo
    echo "== 推送进程最近 10 轮"
    compose logs --tail=10 --no-log-prefix pusher 2>/dev/null | grep -E '本轮|出错|失败|拒绝' || echo "（还没有记录）"
    echo
    free -h | sed -n '1,3p'
    df -h / | tail -1
    ;;
  logs)
    need_env
    shift
    compose logs -f --tail=100 "$@"
    ;;
  update)
    need_env
    git pull --ff-only
    build
    compose stop api worker web pusher
    compose run --rm setup
    compose up -d
    ;;
  backup)
    need_env
    mkdir -p backups
    out="backups/finhot-$(date +%Y%m%d-%H%M).sql.gz"
    compose exec -T db pg_dump -U aihot aihot | gzip > "$out"
    echo "已备份到 $out（$(du -h "$out" | cut -f1)）。建议偶尔下载到自己电脑上保存。"
    ;;
  restart)
    need_env
    shift
    compose restart "$@"
    ;;
  stop)
    need_env
    compose stop
    ;;
  *)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
