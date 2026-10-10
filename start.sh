#!/usr/bin/env bash
# 理反 Web 一键启动（Linux / macOS）
set -e
cd "$(dirname "$0")"
echo "[1/3] 检查 Python..."
command -v python3 >/dev/null || { echo "缺少 python3"; exit 1; }
echo "[2/3] 安装 mdbtools（读取 .mdb，可选）与依赖..."
if command -v apt-get >/dev/null; then
  sudo apt-get install -y mdbtools >/dev/null 2>&1 || true
fi
python3 -m pip install -r backend/requirements.txt -q
echo "[3/3] 启动服务（http://0.0.0.0:8000）..."
cd backend
exec python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
