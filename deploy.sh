#!/usr/bin/env bash
# =============================================================================
# 理反 Web 一键部署（Linux 服务器，Docker 方式）
# 用法：
#   1. 服务器安装 Docker + Compose 插件（多数云主机镜像已自带）
#   2. 把项目目录上传到服务器（git clone 或 scp）
#   3. 配置 .env：GH_TOKEN / ADMIN_USER / ADMIN_PASS / AUTH_SECRET
#   4. bash deploy.sh
# 访问：http://<服务器IP>:8000
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "=== 1/4 检查 Docker ==="
if ! command -v docker >/dev/null 2>&1; then
  echo "未安装 Docker，请先安装：curl -fsSL https://get.docker.com | sh"
  exit 1
fi

echo "=== 2/4 检查 .env ==="
if [ ! -f .env ]; then
  echo "生成 .env 模板（请编辑填入 GH_TOKEN / ADMIN_PASS / AUTH_SECRET）"
  cat > .env <<'EOF'
# GitHub PAT（生成 MDB 必需，Actions+Contents 权限）
GH_TOKEN=
# 登录凭据（生产务必修改默认 admin/admin）
ADMIN_USER=admin
ADMIN_PASS=admin
# 鉴权签名密钥（生产务必设置随机值，如 openssl rand -hex 32）
AUTH_SECRET=change-me-in-production
EOF
  echo "已生成 .env —— 请编辑后重新运行本脚本"
  exit 1
fi

echo "=== 3/4 构建并启动 ==="
docker compose up -d --build

echo "=== 4/4 健康检查 ==="
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; then
    echo "✓ 部署成功：http://<服务器IP>:8000 （账号见 .env）"
    exit 0
  fi
  sleep 2
done
echo "✗ 服务未就绪，查看日志：docker compose logs -f lifan-web"
exit 1
