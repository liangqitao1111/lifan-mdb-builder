@echo off
chcp 65001 >nul
rem 理反 Web 一键启动（Windows）
rem 生成 MDB 功能需要 GH_TOKEN（GitHub PAT，Actions+Contents 权限）：
rem   方式一：系统环境变量设置 GH_TOKEN（start.bat 启动的进程自动继承）
rem   方式二：仓库根目录放 .gh_token 文件（内容为 token，已 gitignore）
if not defined GH_TOKEN (
  if exist .gh_token set /p GH_TOKEN=<.gh_token
)
cd /d "%~dp0"
rem 优先使用 WorkBuddy 托管环境（已装 uvicorn）；不存在则回退系统 python
set "PY=C:\Users\神舟\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo [1/3] 检查 Python...
"%PY%" --version >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python，请先安装 Python 3.11+ 并加入 PATH
  pause & exit /b 1
)
echo [2/3] 安装依赖（首次）...
"%PY%" -m pip install -r backend\requirements.txt -q 2>nul
echo [3/3] 启动服务（http://127.0.0.1:8766）并打开浏览器...
start "" /b "%PY%" -m uvicorn main:app --host 127.0.0.1 --port 8766 --app-dir backend
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8766/
pause
