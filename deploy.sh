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
  echo "生成 .env 模板（已随机生成管理员密码和鉴权密钥，请妥善保存）"
  if ! command -v openssl >/dev/null 2>&1; then
    echo "未找到 openssl，无法安全生成初始密钥；请安装 openssl 后重试"
    exit 1
  fi
  generated_pass="$(openssl rand -hex 24)"
  generated_secret="$(openssl rand -hex 32)"
  cat > .env <<'EOF'
# GitHub PAT（生成 MDB 必需，Actions+Contents 权限）
GH_TOKEN=
# 登录凭据（首次启动已随机生成密码）
ADMIN_USER=admin
ADMIN_PASS=__GENERATED_PASS__
# 鉴权签名密钥（随机值，勿提交到版本库）
AUTH_SECRET=__GENERATED_SECRET__
EOF
  sed -i "s/__GENERATED_PASS__/${generated_pass}/; s/__GENERATED_SECRET__/${generated_secret}/" .env
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
