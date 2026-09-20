# ocr_image.ps1 — 对**图片文件**跑 Windows 内置中文 OCR，返回带坐标的 JSON
#
# 为什么要自己写：第三方助手 wx_helper.ps1 只支持"按屏幕坐标截图再 OCR"，
# 而 screen 截图会被其它窗口遮挡（这台机器上就翻过车——抓到的图是编辑器界面）。
# 配合 PrintWindow 抓到的不受遮挡的窗口图，就能稳定识别微信界面。
#
# 用法：
#   powershell -File ocr_image.ps1 -Path shot.png [-Scale 3]
#   输出: [{"text":"...","x":10,"y":20,"w":30,"h":12,"cx":25,"cy":26}, ...]
#   （x/y 是**图片内**坐标，已按 Scale 反向还原为原图坐标）

param(
    [Parameter(Mandatory=$true)][string]$Path,
    [double]$Scale = 3.0
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

# 放大以便 OCR 认小字
$src = [System.Drawing.Image]::FromFile($Path)
$nw = [int]($src.Width * $Scale)
$nh = [int]($src.Height * $Scale)
$big = New-Object System.Drawing.Bitmap($nw, $nh)
$gfx = [System.Drawing.Graphics]::FromImage($big)
$gfx.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$gfx.DrawImage($src, 0, 0, $nw, $nh)
$gfx.Dispose()
$tmp = [System.IO.Path]::Combine($env:TEMP, "ocr_img_$PID.png")
$big.Save($tmp, [System.Drawing.Imaging.ImageFormat]::Png)
$src.Dispose(); $big.Dispose()

[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime] | Out-Null

$file    = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($tmp)) ([Windows.Storage.StorageFile])
$stream  = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap  = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$lang = New-Object Windows.Globalization.Language "zh-Hans-CN"
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
if ($engine -eq $null) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($engine -eq $null) { Write-Output "[]"; exit 0 }

$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$out = @()
foreach ($line in $result.Lines) {
    $words = $line.Words
    if ($words.Count -eq 0) { continue }
    $x1 = ($words | ForEach-Object { $_.BoundingRect.X } | Measure-Object -Minimum).Minimum
    $y1 = ($words | ForEach-Object { $_.BoundingRect.Y } | Measure-Object -Minimum).Minimum
    $x2 = ($words | ForEach-Object { $_.BoundingRect.X + $_.BoundingRect.Width } | Measure-Object -Maximum).Maximum
    $y2 = ($words | ForEach-Object { $_.BoundingRect.Y + $_.BoundingRect.Height } | Measure-Object -Maximum).Maximum
    # 还原到原图坐标
    $out += [pscustomobject]@{
        text = $line.Text
        x    = [int]($x1 / $Scale)
        y    = [int]($y1 / $Scale)
        w    = [int](($x2 - $x1) / $Scale)
        h    = [int](($y2 - $y1) / $Scale)
        cx   = [int](($x1 + $x2) / 2 / $Scale)
        cy   = [int](($y1 + $y2) / 2 / $Scale)
    }
}

Remove-Item $tmp -ErrorAction SilentlyContinue
$stream.Dispose()
Write-Output ($out | ConvertTo-Json -Compress -Depth 3)
