# 微信洞察 · 启动脚本
#
# ⚠️ 用 WMI 启动而不是 Start-Process：
#    Start-Process 在这台机器上会因环境变量 NO_PROXY/no_proxy 重复键而失败
#    （"Item has already been added. Key in dictionary: 'NO_PROXY'"）。
#    WMI 的 Win32_Process.Create 没这个问题。
#
# ⚠️ .ps1 必须带 UTF-8 BOM，否则 PowerShell 5.1 按 ANSI 解析中文会乱码、
#    进而语法报错。改完这个文件记得确认 BOM 还在。
#
# 启动两个服务：
#   8772  微信洞察主页面（5 个标签）
#   8899  聊天即消费（完整版仪表盘，嵌在主页面第 4 个标签里）
#
# 用法：
#   .\start.ps1              启动并打开浏览器
#   .\start.ps1 -NoBrowser   不打开浏览器

param(
    [int]$Port = 8772,
    [int]$ConsumePort = 8899,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# 找 Python：**优先用 PATH 里的 python**，找不到才回退到常见安装位置。
# （原来先写死 D:\Python313，别人机器上没这个目录，虽然会回退但顺序不合理）
$py = $null
$c = Get-Command python -ErrorAction SilentlyContinue
if ($c) { $py = $c.Source }
if (-not $py) {
    foreach ($p in @("D:\Python313\python.exe", "D:\Python312\python.exe",
                     "C:\Python313\python.exe", "C:\Python312\python.exe",
                     "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
                     "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")) {
        if (Test-Path $p) { $py = $p; break }
    }
}
if (-not $py) {
    throw "找不到 python.exe。请安装 Python 3.10+ 并加入 PATH，或用 -Python 参数指定绝对路径。"
}

$db = Join-Path $root "00-core\chat.db"
if (-not (Test-Path $db)) {
    Write-Host ""
    Write-Host "  [!] 还没导入数据。先跑一次：" -ForegroundColor Yellow
    Write-Host "      cd 00-core; & '$py' wcstore.py import"
    Write-Host ""
    exit 1
}

function Stop-Matching([string]$pattern) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match $pattern } |
        ForEach-Object {
            Write-Host "  停止旧进程 PID $($_.ProcessId)  [$pattern]"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

function Start-One([string]$workdir, [string]$script, [string]$argline, [string]$tag) {
    $line = 'cmd.exe /c cd /d "{0}" && "{1}" -u {2} {3} > {4}.log 2> {4}.err.log' `
        -f $workdir, $py, $script, $argline, $tag
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine      = $line
        CurrentDirectory = $workdir
    }
    if ($r.ReturnValue -ne 0) { throw "$tag 启动失败，ReturnValue=$($r.ReturnValue)" }
    return $r.ProcessId
}

Write-Host ""
Stop-Matching "wcinsight\.py"
Stop-Matching "wcserve\.py"
Start-Sleep -Milliseconds 900

$pid2 = Start-One (Join-Path $root "05-insight\consume") "wcserve.py" `
    "--port $ConsumePort --interval 30" "server"

$mode = if ($NoBrowser) { "" } else { " --open" }
$pid1 = Start-One (Join-Path $root "05-insight") "wcinsight.py" `
    "--port $Port$mode" "server"

Start-Sleep -Seconds 2
Write-Host "  [OK] 微信洞察已启动 (PID $pid1)" -ForegroundColor Green
Write-Host "       http://127.0.0.1:$Port/"
Write-Host "  [OK] 聊天即消费子应用已启动 (PID $pid2)" -ForegroundColor Green
Write-Host "       http://127.0.0.1:$ConsumePort/"
Write-Host ""
Write-Host "  四个功能: 年度报告 / 聊天与关系 / 聊天即消费 / 业务线"
Write-Host "  日志: 05-insight\server.log   05-insight\consume\server.log"
Write-Host "  停止: .\stop.ps1"
Write-Host ""
