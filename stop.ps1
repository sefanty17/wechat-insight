# 微信洞察 · 停止脚本
$ErrorActionPreference = "SilentlyContinue"

$killed = 0
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "wcinsight\.py|wcserve\.py" } |
    ForEach-Object {
        $tag = if ($_.CommandLine -match "wcinsight\.py") { "主页面" } else { "聊天即消费" }
        Write-Host "  停止 PID $($_.ProcessId)  [$tag]"
        Stop-Process -Id $_.ProcessId -Force
        $killed++
    }

if ($killed -eq 0) {
    Write-Host "  没有在运行的微信洞察服务。"
} else {
    Write-Host ""
    Write-Host "  [OK] 已停止 $killed 个进程。" -ForegroundColor Green
}
