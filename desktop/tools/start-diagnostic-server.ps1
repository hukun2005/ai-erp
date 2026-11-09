# 一键启动诊断包接收：本机接收服务 + Cloudflare 公网隧道
# 双击运行（或右键“使用 PowerShell 运行”），把窗口里打印的
#   https://xxxx.trycloudflare.com/upload
# 填到客户端“使用模式设置 → 高级设置 → 诊断接收地址”。
# 注意：临时隧道的地址在每次重启后会变化，变化后需要重新打包或在客户端设置里修改。
$env:DIAG_TOKEN = '7fb18a3ada757924eb95b740d59d33fe'
$env:DIAG_PORT = '8799'
$env:DIAG_DIR = Join-Path $PSScriptRoot 'diagnostics'

$serverScript = Join-Path $PSScriptRoot 'diagnostic-server.js'
$cloudflared = Join-Path $PSScriptRoot 'cloudflared.exe'

# 接收服务在后台运行，日志写到 diagnostics\server.log
Start-Process node -ArgumentList $serverScript -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $env:DIAG_DIR 'server.log') `
  -RedirectStandardError (Join-Path $env:DIAG_DIR 'server.err.log') | Out-Null
Start-Sleep -Seconds 2

if (Test-Path $cloudflared) {
  Write-Host '正在建立公网隧道，请稍候……' -ForegroundColor Cyan
  & $cloudflared tunnel --url "http://127.0.0.1:$($env:DIAG_PORT)" --no-autoupdate
} else {
  Write-Host '未找到 cloudflared.exe，仅本机/局域网可用。' -ForegroundColor Yellow
  Write-Host "局域网地址：http://$((Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } | Select-Object -First 1).IPAddress):$($env:DIAG_PORT)/upload"
  while ($true) { Start-Sleep -Seconds 60 }
}
