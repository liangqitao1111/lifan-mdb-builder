@echo off
chcp 65001 >nul
rem 理反 Web 一键启动（Windows）
cd /d "%~dp0"
echo [1/3] 检查 Python...
where python >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python，请先安装 Python 3.11+ 并加入 PATH
  pause & exit /b 1
)
echo [2/3] 安装依赖（首次）...
python -m pip install -r backend\requirements.txt -q 2>nul
echo [3/3] 启动服务（http://127.0.0.1:8000）...
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir backend
pause
