# AI-ERP 桌面版一键构建：前端 → Python 后端 → Electron 安装包
# 用法：
#   powershell -ExecutionPolicy Bypass -File desktop\scripts\build.ps1
#   ... -SkipFrontend   仅重新打包后端和 Electron（前端没改动时用）
param(
  [switch]$SkipFrontend,
  [switch]$SkipBackend,
  [switch]$SkipElectron
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$frontend = Join-Path $root "frontend"
$backend = Join-Path $root "backend"
$desktop = Join-Path $root "desktop"

# 国内网络下 Electron / electron-builder 依赖的二进制走镜像，避免卡在 GitHub
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
$env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"

function Step($title) {
  Write-Host ""
  Write-Host "==== $title ====" -ForegroundColor Cyan
}

# 把 backend/.env 里的大模型配置写进安装包默认值，客户装完即可用 AI 助手，无需自己填 Key。
function Sync-Presets {
  $envFile = Join-Path $backend ".env"
  if (-not (Test-Path $envFile)) { return }
  $map = @{}
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^(LLM_API_KEY|LLM_BASE_URL|LLM_MODEL)\s*=\s*(.+)$') {
      $map[$matches[1]] = $matches[2].Trim()
    }
  }
  if (-not $map['LLM_API_KEY']) { return }
  $json = [ordered]@{
    llmApiKey  = [string]$map['LLM_API_KEY']
    llmBaseUrl = [string]$map['LLM_BASE_URL']
    llmModel   = [string]$map['LLM_MODEL']
  } | ConvertTo-Json
  Set-Content -Path (Join-Path $desktop "electron\defaults.json") -Value $json -Encoding UTF8
  Write-Host "已预置大模型配置：$($map['LLM_MODEL'])" -ForegroundColor Yellow
}

# electron-builder 需要 winCodeSign 里的 rcedit/signtool。它在 Windows 上自动解压该包时，
# 会因为包内 darwin 的软链接需要管理员权限而失败（Cannot create symbolic link）。
# 这里提前解压到它的缓存目录（跳过 darwin 部分），后续构建就能直接命中缓存。
function Ensure-WinCodeSign {
  $cacheRoot = Join-Path $env:LOCALAPPDATA "electron-builder\Cache\winCodeSign"
  $target = Join-Path $cacheRoot "winCodeSign-2.6.0"
  if (Test-Path (Join-Path $target "rcedit-x64.exe")) { return }

  $sevenZip = Join-Path $desktop "node_modules\7zip-bin\win\x64\7za.exe"
  if (-not (Test-Path $sevenZip)) {
    throw "找不到 7za.exe，请先在 desktop 目录执行 npm install"
  }
  $archive = Join-Path $cacheRoot "winCodeSign-2.6.0.7z"
  New-Item -ItemType Directory -Force -Path $cacheRoot | Out-Null
  if (-not (Test-Path $archive)) {
    Write-Host "下载 winCodeSign ..."
    Invoke-WebRequest -Uri "https://cdn.npmmirror.com/binaries/electron-builder-binaries/winCodeSign-2.6.0/winCodeSign-2.6.0.7z" -OutFile $archive
  }
  if (Test-Path $target) { Remove-Item $target -Recurse -Force }
  Write-Host "解压 winCodeSign（跳过 darwin 软链接）..."
  & $sevenZip x -bd -y $archive "-o$target" "-x!darwin" | Out-Null
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $target "rcedit-x64.exe"))) {
    throw "winCodeSign 解压失败"
  }
}

Step "1/3 构建前端"
if (-not $SkipFrontend) {
  Push-Location $frontend
  try {
    if (-not (Test-Path "node_modules")) { npm install }
    npm run build
    if (-not (Test-Path "dist\index.html")) { throw "前端构建失败：dist\index.html 不存在" }
  }
  finally { Pop-Location }
}
else { Write-Host "已跳过前端构建" }

Step "2/3 打包 Python 后端"
if (-not $SkipBackend) {
  $venvPython = Join-Path $backend ".venv\Scripts\python.exe"
  if (-not (Test-Path $venvPython)) {
    Write-Host "创建虚拟环境 backend\.venv ..."
    python -m venv (Join-Path $backend ".venv")
  }
  & $venvPython -m pip install --upgrade pip
  & $venvPython -m pip install -r (Join-Path $backend "requirements.txt")
  & $venvPython -m pip install "pyinstaller>=6.11"

  Push-Location $backend
  try {
    if (Test-Path "dist\erp-server") { Remove-Item "dist\erp-server" -Recurse -Force }
    if (Test-Path "build\erp-server") { Remove-Item "build\erp-server" -Recurse -Force }
    & $venvPython -m PyInstaller "erp-server.spec" --noconfirm
    if (-not (Test-Path "dist\erp-server\erp-server.exe")) { throw "后端打包失败：dist\erp-server\erp-server.exe 不存在" }
  }
  finally { Pop-Location }
}
else { Write-Host "已跳过后端打包" }

Step "3/3 生成 Electron 安装包"
if (-not $SkipElectron) {
  Push-Location $desktop
  try {
    if (-not (Test-Path "node_modules")) { npm install }
    Ensure-WinCodeSign
    Sync-Presets
    npx electron-builder --win nsis
    Write-Host ""
    Write-Host "安装包输出目录：$desktop\release" -ForegroundColor Green
    Get-ChildItem (Join-Path $desktop "release") -Filter "AI-ERP-Setup-*.exe" |
      Select-Object Name, @{ n = "大小(MB)"; e = { [math]::Round($_.Length / 1MB, 1) } } |
      Format-Table -AutoSize
  }
  finally { Pop-Location }
}
else { Write-Host "已跳过 Electron 打包" }

Write-Host "完成。" -ForegroundColor Green
