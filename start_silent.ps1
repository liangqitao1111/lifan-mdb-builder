# 理反 Web 静默启动脚本
# 由桌面快捷方式以 -WindowStyle Hidden 调用，因此不会弹出 cmd 窗口。
# 功能与 start.bat 一致:
#   1. 读取 .gh_token（如未设置 GH_TOKEN）
#   2. 安装后端依赖（首次）
#   3. 后台静默启动 uvicorn (127.0.0.1:8766)
#   4. 打开浏览器（可用 -NoBrowser 跳过）
param(
    [switch]$NoBrowser
)
$ErrorActionPreference = 'SilentlyContinue'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root 'backend'
$workDir = Join-Path $root 'work'
$py = 'C:\Users\神舟\.workbuddy\binaries\python\envs\default\Scripts\python.exe'

if (-not (Test-Path $py)) { $py = 'python' }
New-Item -ItemType Directory -Force -Path $workDir | Out-Null

Set-Location $root

# 与 start.bat 相同的 GH_TOKEN 兼容逻辑
if (-not $env:GH_TOKEN) {
    $tokenFile = Join-Path $root '.gh_token'
    if (Test-Path $tokenFile) {
        $env:GH_TOKEN = (Get-Content -LiteralPath $tokenFile -Raw).Trim()
    }
}

# 首次安装依赖（静默，失败不阻塞启动）
& $py -m pip install -r (Join-Path $backend 'requirements.txt') -q 2>$null | Out-Null

# 端口已被占用时不重复启动
$busy = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
if (-not $busy) {
    $outLog = Join-Path $workDir 'uvicorn.out.log'
    $errLog = Join-Path $workDir 'uvicorn.err.log'
    Start-Process -FilePath $py -ArgumentList @(
        '-m', 'uvicorn',
        'main:app',
        '--host', '127.0.0.1',
        '--port', '8766',
        '--app-dir', $backend
    ) -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog | Out-Null
    Start-Sleep -Seconds 3
}

# 打开浏览器（默认打开；调试/自动化可 -NoBrowser）
if (-not $NoBrowser) {
    Start-Process 'http://127.0.0.1:8766/'
}