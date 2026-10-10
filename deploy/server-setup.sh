#!/usr/bin/env bash
# 新装的 Ubuntu 24.04 服务器上跑一次（需要 root）：加虚拟内存、设时区、装 Docker（腾讯云镜像源）。
# 用法：sudo bash deploy/server-setup.sh
# 重复运行是安全的：已经做过的步骤会跳过。
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 运行：sudo bash deploy/server-setup.sh" >&2
  exit 1
fi

echo "== 1/4 虚拟内存（4 GB）：构建网站镜像时内存会吃紧"
if ! swapon --show | grep -q '/swapfile'; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo 'vm.swappiness=10' > /etc/sysctl.d/99-finhot.conf
sysctl -q -p /etc/sysctl.d/99-finhot.conf

echo "== 2/4 时区设为北京时间"
timedatectl set-timezone Asia/Shanghai || true

echo "== 3/4 安装 Docker（腾讯云软件源）"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -q
  apt-get install -y -q ca-certificates curl git gnupg openssl python3
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://mirrors.cloud.tencent.com/docker-ce/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://mirrors.cloud.tencent.com/docker-ce/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
  apt-get install -y -q git openssl python3 >/dev/null
fi

echo "== 4/4 Docker 镜像加速与日志上限"
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{
  "registry-mirrors": ["https://mirror.ccs.tencentyun.com"],
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "3" }
}
JSON
systemctl enable docker >/dev/null 2>&1 || true
systemctl restart docker
if id ubuntu >/dev/null 2>&1; then usermod -aG docker ubuntu; fi

echo
docker --version
docker compose version
free -h | sed -n '1,3p'
echo
echo "完成。下一步：deploy/finhot.sh init（生成配置），然后 deploy/finhot.sh up（构建并启动）。"
